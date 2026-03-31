#!/usr/bin/env python3
import logging
import time
from dataclasses import dataclass

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
TEMPERATURE_POLL_INTERVAL_SECONDS = 65
MODULATION_POLL_INTERVAL_SECONDS = 75
SERIAL_IDLE_RECONNECT_SECONDS = 180.0
MAIN_LOOP_SLEEP_SECONDS = 0.1

POLL_SOURCE_ADDR = 0x30
POLL_DEST_ADDR = 0x08
STATUS_QUERY_TYPE_0 = bytes([0x00])  # B511/00: status, pressure, flags
STATUS_QUERY_TYPE_1 = bytes([0x01])  # B511/01: live temperatures
STATUS_QUERY_TYPE_2 = bytes([0x02])  # B511/02: modulation/setpoints


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)


@dataclass
class BridgeLoopState:
    last_publish_monotonic: float = 0.0
    last_status_poll_monotonic: float = 0.0
    last_temperature_poll_monotonic: float = 0.0
    last_modulation_poll_monotonic: float = 0.0
    last_serial_connect_attempt_monotonic: float = 0.0


def _sensor_value(sensors: dict, key: str):
    if key not in sensors:
        return None
    return sensors[key].get("value")


def _is_modulation_update(message) -> bool:
    return (
        (message.name == "status_temps" and message.query_data.get("query_type") == 2)
        or message.name == "modulation_outdoor"
        or (message.name == "room_temp" and message.source == 0x08)
    )


def _publish_sensors_safe(mqtt_client: HAMqttClient, sensors: dict, logger: logging.Logger, reason: str) -> None:
    if not sensors:
        return

    try:
        mqtt_client.publish_sensors(sensors)
    except Exception:
        logger.exception("MQTT publish failed during %s", reason)


def _process_telegrams(telegrams, parser: TheliaParser, logger: logging.Logger, loop_now: float, state: BridgeLoopState) -> bool:
    force_publish = False

    for telegram in telegrams:
        try:
            message = parser.parse(telegram)
        except Exception:
            logger.exception("Unexpected parser failure for telegram %s", telegram)
            continue

        if message.name == "param_write":
            force_publish = True

        if message.name == "status_temps" and message.query_data.get("query_type") == 0:
            state.last_status_poll_monotonic = loop_now

        if message.name == "status_temps" and message.query_data.get("query_type") == 1:
            state.last_temperature_poll_monotonic = loop_now

        if _is_modulation_update(message):
            state.last_modulation_poll_monotonic = loop_now
            force_publish = True

    return force_publish


def _send_active_poll(connection: SerialConnection, logger: logging.Logger, label: str, payload: bytes) -> bool:
    if connection.send_query(
        source=POLL_SOURCE_ADDR,
        destination=POLL_DEST_ADDR,
        primary_command=0xB5,
        secondary_command=0x11,
        data=payload,
        prepend_sync=True,
        append_sync=True,
    ):
        logger.info("Sent active poll %s", label)
        return True
    return False


def _ensure_serial_connection(
    connection: SerialConnection,
    logger: logging.Logger,
    state: BridgeLoopState,
    loop_now: float,
) -> bool:
    if connection.connected:
        return True

    reconnect_delay = max(connection.config.reconnect_delay, MAIN_LOOP_SLEEP_SECONDS)
    if (loop_now - state.last_serial_connect_attempt_monotonic) < reconnect_delay:
        return False

    state.last_serial_connect_attempt_monotonic = loop_now
    logger.warning("eBUS serial disconnected. Attempting reconnect on %s", connection.config.port)

    if connection.connect():
        logger.info("eBUS serial connection is active again")
        state.last_publish_monotonic = 0.0
        state.last_status_poll_monotonic = 0.0
        state.last_temperature_poll_monotonic = 0.0
        state.last_modulation_poll_monotonic = 0.0
        return True

    return False


def _run_maintenance_cycle(
    connection: SerialConnection,
    parser: TheliaParser,
    aggregator: DataAggregator,
    mqtt_client: HAMqttClient,
    state: BridgeLoopState,
    logger: logging.Logger,
    loop_now: float,
) -> None:
    idle_seconds = connection.seconds_since_last_activity(loop_now)
    if idle_seconds is not None and idle_seconds >= SERIAL_IDLE_RECONNECT_SECONDS:
        logger.warning("No eBUS activity for %.1fs. Recycling serial connection.", idle_seconds)
        connection.disconnect()
        return

    try:
        telegrams = connection.read_telegrams()
    except Exception:
        logger.exception("Unexpected failure while reading eBUS traffic")
        connection.disconnect()
        return

    force_publish = _process_telegrams(telegrams, parser, logger, loop_now, state)
    sensors = aggregator.get_all_sensors()

    if force_publish:
        _publish_sensors_safe(mqtt_client, sensors, logger, "live update")
        state.last_publish_monotonic = loop_now

    if (loop_now - state.last_publish_monotonic) >= PUBLISH_INTERVAL_SECONDS:
        _publish_sensors_safe(mqtt_client, sensors, logger, "periodic refresh")
        state.last_publish_monotonic = loop_now

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

    if should_poll_status and (loop_now - state.last_status_poll_monotonic) >= STATUS_POLL_INTERVAL_SECONDS:
        if _send_active_poll(connection, logger, "B511/00", STATUS_QUERY_TYPE_0):
            state.last_status_poll_monotonic = loop_now

    if (loop_now - state.last_temperature_poll_monotonic) >= TEMPERATURE_POLL_INTERVAL_SECONDS:
        if _send_active_poll(connection, logger, "B511/01", STATUS_QUERY_TYPE_1):
            state.last_temperature_poll_monotonic = loop_now

    if should_poll_modulation and (loop_now - state.last_modulation_poll_monotonic) >= MODULATION_POLL_INTERVAL_SECONDS:
        if _send_active_poll(connection, logger, "B511/02", STATUS_QUERY_TYPE_2):
            state.last_modulation_poll_monotonic = loop_now


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

    logger.info("System running. Monitoring traffic, polling stale data and auto-recovering after bus silence.")
    state = BridgeLoopState()

    try:
        while True:
            loop_now = time.monotonic()

            if not _ensure_serial_connection(connection, logger, state, loop_now):
                time.sleep(MAIN_LOOP_SLEEP_SECONDS)
                continue

            _run_maintenance_cycle(connection, parser, aggregator, mqtt_client, state, logger, loop_now)
            time.sleep(MAIN_LOOP_SLEEP_SECONDS)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        mqtt_client.disconnect()
        connection.disconnect()


if __name__ == "__main__":
    main()
