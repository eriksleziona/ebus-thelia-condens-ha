#!/usr/bin/env python3
import logging
import sys
import time

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
HISTORY_POLL_INTERVAL_SECONDS = 15

POLL_SOURCE_ADDR = 0x30
POLL_DEST_ADDR = 0x08
STATUS_QUERY_TYPE_0 = bytes([0x00])  # B511/00: status, pressure, flags
STATUS_QUERY_TYPE_2 = bytes([0x02])  # B511/02: modulation/setpoints
HISTORY_INDEX_MAX = 12


def _build_history_query_sequence():
    """
    Build a mixed query list:
    - direct single-byte query types
    - indexed two-byte variants (query_type + index) for month/day buckets.
    """
    sequence = []

    # Single-byte baseline queries.
    sequence.extend(
        [
            (0x13, bytes([0x00])),
            (0x13, bytes([0x01])),
            (0x13, bytes([0x02])),
            (0x13, bytes([0x03])),
            (0x13, bytes([0x04])),
            (0x14, bytes([0x00])),
            (0x15, bytes([0x00])),
            (0x15, bytes([0x01])),
            (0x17, bytes([0x00])),
            (0x18, bytes([0x00])),
            (0x19, bytes([0x00])),
            (0x1A, bytes([0x00])),
        ]
    )

    # Indexed history windows, likely required for month/day breakdown pages.
    for idx in range(HISTORY_INDEX_MAX + 1):
        for qtype in (0x00, 0x01, 0x02, 0x03, 0x04):
            sequence.append((0x13, bytes([qtype, idx])))
        for qtype in (0x00, 0x01):
            sequence.append((0x15, bytes([qtype, idx])))

    return tuple(sequence)


HISTORY_QUERY_SEQUENCE = _build_history_query_sequence()
HISTORY_MESSAGE_NAMES = {
    "history_stats",
    "history_programs",
    "error_history",
    "history_stats_ext_17",
    "history_stats_ext_18",
    "history_stats_ext_19",
    "history_stats_ext_1a",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)


def _sensor_value(sensors: dict, key: str):
    if key not in sensors:
        return None
    return sensors[key].get("value")


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
    last_history_poll = 0.0
    history_query_index = 0

    try:
        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            now = time.time()
            sensors = aggregator.get_all_sensors()

            # Instant feedback for parameter writes.
            if msg.name == "param_write":
                if sensors:
                    mqtt_client.publish_sensors(sensors)
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
                if sensors:
                    mqtt_client.publish_sensors(sensors)
                last_publish = now

            # Push history data immediately when new historical block is received.
            if msg.name in HISTORY_MESSAGE_NAMES:
                if sensors:
                    mqtt_client.publish_sensors(sensors)
                last_publish = now

            if now - last_publish >= PUBLISH_INTERVAL_SECONDS:
                if sensors:
                    mqtt_client.publish_sensors(sensors)
                last_publish = now

            status_age_s = _sensor_value(sensors, "boiler.status_last_update_s")
            modulation_age_s = _sensor_value(sensors, "boiler.modulation_last_update_s")
            status_stale = bool(_sensor_value(sensors, "boiler.status_stale"))

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

            if HISTORY_QUERY_SEQUENCE and (now - last_history_poll) >= HISTORY_POLL_INTERVAL_SECONDS:
                secondary_command, payload = HISTORY_QUERY_SEQUENCE[history_query_index % len(HISTORY_QUERY_SEQUENCE)]
                if connection.send_query(
                    source=POLL_SOURCE_ADDR,
                    destination=POLL_DEST_ADDR,
                    primary_command=0xB5,
                    secondary_command=secondary_command,
                    data=payload,
                    prepend_sync=True,
                    append_sync=True,
                ):
                    logger.info(f"Sent history poll B5{secondary_command:02X} payload={payload.hex()}")
                    last_history_poll = now
                    history_query_index += 1

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()
