#!/usr/bin/env python3
import logging
import sys
import time
from typing import Any, Dict, Optional

from ebus_core.connection import ConnectionConfig, SerialConnection
from thelia.mqtt import HAMqttClient
from thelia.parser import DataAggregator, TheliaParser

# Configuration
MQTT_BROKER = "192.168.1.84"
MQTT_PORT = 1883
MQTT_USER = "mqtt_user"  # Update these values.
MQTT_PASS = "mqtt_password"  # Update these values.
SERIAL_PORT = "/dev/ttyAMA0"
RUNTIME_STATE_FILE = "config/runtime_state.json"
FLAME_DEBOUNCE_SECONDS = 8.0
STATUS_STALE_THRESHOLD_SECONDS = 120.0

PUBLISH_INTERVAL_SECONDS = 5
STATUS_POLL_INTERVAL_SECONDS = 60
MODULATION_POLL_INTERVAL_SECONDS = 75

POLL_SOURCE_ADDR = 0x30
POLL_DEST_ADDR = 0x08
STATUS_QUERY_TYPE_0 = bytes([0x00])  # B511/00: status, pressure, flags
STATUS_QUERY_TYPE_2 = bytes([0x02])  # B511/02: modulation/setpoints


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)


def _sensor_value(sensors: dict, key: str):
    if key not in sensors:
        return None
    return sensors[key].get("value")


def _metric(value: Any, unit: str = "", description: str = "") -> Dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "age_seconds": 0.0,
        "description": description,
    }


def _build_bridge_stats(
    parser: TheliaParser,
    poll_status_sent: int,
    poll_modulation_sent: int,
    poll_status_received: int,
    poll_modulation_received: int,
    last_status_poll_at: Optional[str],
    last_modulation_poll_at: Optional[str],
    last_status_reply_at: Optional[str],
    last_modulation_reply_at: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    stats = parser.get_stats()
    total = int(stats.get("total", 0))
    parsed = int(stats.get("parsed", 0))
    unknown = int(stats.get("unknown", 0))
    parse_ratio_pct = round((parsed * 100.0 / total), 1) if total > 0 else 0.0

    return {
        "bridge.telegrams_total": _metric(total, "", "Total telegrams seen"),
        "bridge.telegrams_parsed": _metric(parsed, "", "Parsed telegrams"),
        "bridge.telegrams_unknown": _metric(unknown, "", "Unknown telegrams"),
        "bridge.parse_ratio_pct": _metric(parse_ratio_pct, "%", "Parse success ratio"),
        "bridge.poll_b511_q0_sent": _metric(poll_status_sent, "", "Active polls B511/00 sent"),
        "bridge.poll_b511_q2_sent": _metric(poll_modulation_sent, "", "Active polls B511/02 sent"),
        "bridge.poll_b511_q0_received": _metric(poll_status_received, "", "Responses B511/00 received"),
        "bridge.poll_b511_q2_received": _metric(poll_modulation_received, "", "Responses B511/02 received"),
        "bridge.poll_b511_q0_last_sent_at": _metric(last_status_poll_at or "", "", "Last B511/00 poll sent timestamp"),
        "bridge.poll_b511_q2_last_sent_at": _metric(last_modulation_poll_at or "", "", "Last B511/02 poll sent timestamp"),
        "bridge.poll_b511_q0_last_received_at": _metric(last_status_reply_at or "", "", "Last B511/00 response timestamp"),
        "bridge.poll_b511_q2_last_received_at": _metric(last_modulation_reply_at or "", "", "Last B511/02 response timestamp"),
    }


def _compose_publish_payload(base_sensors: Dict[str, Dict[str, Any]], bridge_stats: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    payload = dict(base_sensors)
    payload.update(bridge_stats)
    return payload


def main():
    logger = logging.getLogger("Main")
    logger.info("Starting Thelia eBUS bridge (active polling mode)")

    ebus_config = ConnectionConfig(port=SERIAL_PORT, baudrate=2400)
    connection = SerialConnection(ebus_config)

    parser = TheliaParser()
    aggregator = DataAggregator(
        state_file=RUNTIME_STATE_FILE,
        flame_debounce_seconds=FLAME_DEBOUNCE_SECONDS,
        status_stale_threshold_seconds=STATUS_STALE_THRESHOLD_SECONDS,
    )

    mqtt_client = HAMqttClient(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS)
    mqtt_client.connect()

    parser.register_callback(aggregator.update)

    if not connection.connect():
        logger.error("Could not open serial port. Exiting.")
        sys.exit(1)

    logger.info("System running. Monitoring traffic and polling stale data.")

    last_publish = 0.0
    last_status_poll = 0.0
    last_modulation_poll = 0.0
    poll_status_sent = 0
    poll_modulation_sent = 0
    poll_status_received = 0
    poll_modulation_received = 0
    last_status_poll_at = None
    last_modulation_poll_at = None
    last_status_reply_at = None
    last_modulation_reply_at = None

    try:
        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            now = time.time()

            if (
                msg.name == "status_temps"
                and msg.source == POLL_SOURCE_ADDR
                and msg.destination == POLL_DEST_ADDR
                and msg.raw_telegram is not None
                and msg.raw_telegram.response_data
            ):
                query_type = msg.query_data.get("query_type")
                if query_type == 0:
                    poll_status_received += 1
                    last_status_reply_at = msg.timestamp.isoformat(timespec="seconds")
                elif query_type == 2:
                    poll_modulation_received += 1
                    last_modulation_reply_at = msg.timestamp.isoformat(timespec="seconds")

            base_sensors = aggregator.get_all_sensors()
            bridge_stats = _build_bridge_stats(
                parser=parser,
                poll_status_sent=poll_status_sent,
                poll_modulation_sent=poll_modulation_sent,
                poll_status_received=poll_status_received,
                poll_modulation_received=poll_modulation_received,
                last_status_poll_at=last_status_poll_at,
                last_modulation_poll_at=last_modulation_poll_at,
                last_status_reply_at=last_status_reply_at,
                last_modulation_reply_at=last_modulation_reply_at,
            )
            publish_payload = _compose_publish_payload(base_sensors, bridge_stats)

            # Instant feedback for parameter writes.
            if msg.name == "param_write":
                if publish_payload:
                    mqtt_client.publish_sensors(publish_payload)
                last_publish = now

            # Natural updates reset poll cooldowns.
            if msg.name == "status_temps" and msg.query_data.get("query_type") == 0:
                last_status_poll = now
            if (
                (msg.name == "status_temps" and msg.query_data.get("query_type") == 2)
                or msg.name == "modulation_outdoor"
                or (msg.name == "room_temp" and msg.source == 0x08)
            ):
                last_modulation_poll = now

            # Push live modulation-related updates immediately.
            if (
                (msg.name == "status_temps" and msg.query_data.get("query_type") == 2)
                or msg.name == "modulation_outdoor"
                or (msg.name == "room_temp" and msg.source == 0x08)
            ):
                if publish_payload:
                    mqtt_client.publish_sensors(publish_payload)
                last_publish = now

            if now - last_publish >= PUBLISH_INTERVAL_SECONDS:
                if publish_payload:
                    mqtt_client.publish_sensors(publish_payload)
                last_publish = now

            status_age_s = _sensor_value(base_sensors, "boiler.status_last_update_s")
            modulation_age_s = _sensor_value(base_sensors, "boiler.modulation_last_update_s")
            status_stale = bool(_sensor_value(base_sensors, "boiler.status_stale"))

            should_poll_status = (
                status_stale
                or status_age_s is None
                or (
                    isinstance(status_age_s, (int, float))
                    and status_age_s > STATUS_STALE_THRESHOLD_SECONDS
                )
            )
            should_poll_modulation = (
                modulation_age_s is None
                or (
                    isinstance(modulation_age_s, (int, float))
                    and modulation_age_s > STATUS_STALE_THRESHOLD_SECONDS
                )
            )

            if should_poll_status and (now - last_status_poll) >= STATUS_POLL_INTERVAL_SECONDS:
                if connection.send_query(
                    source=POLL_SOURCE_ADDR,
                    destination=POLL_DEST_ADDR,
                    primary_command=0xB5,
                    secondary_command=0x11,
                    data=STATUS_QUERY_TYPE_0,
                    prepend_sync=True,
                    append_sync=True,
                ):
                    logger.info("Sent active poll B511/00")
                    last_status_poll = now
                    poll_status_sent += 1
                    last_status_poll_at = msg.timestamp.isoformat(timespec="seconds")

            if should_poll_modulation and (now - last_modulation_poll) >= MODULATION_POLL_INTERVAL_SECONDS:
                if connection.send_query(
                    source=POLL_SOURCE_ADDR,
                    destination=POLL_DEST_ADDR,
                    primary_command=0xB5,
                    secondary_command=0x11,
                    data=STATUS_QUERY_TYPE_2,
                    prepend_sync=True,
                    append_sync=True,
                ):
                    logger.info("Sent active poll B511/02")
                    last_modulation_poll = now
                    poll_modulation_sent += 1
                    last_modulation_poll_at = msg.timestamp.isoformat(timespec="seconds")

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()
