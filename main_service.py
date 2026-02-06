#!/usr/bin/env python3
import time
import logging
import sys
import os

# Configuration
MQTT_BROKER = "192.168.1.84"
MQTT_PORT = 1883
MQTT_USER = "mqtt_user"  # <--- Update these
MQTT_PASS = "mqtt_password"  # <--- Update these
SERIAL_PORT = "/dev/ttyAMA0"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)

from ebus_core.connection import SerialConnection, ConnectionConfig
from ebus_core.telegram import EbusTelegram  # We might need this to construct a message
from thelia.parser import TheliaParser, DataAggregator
from thelia.mqtt import HAMqttClient

# Hex constants for "Read Status Type 0"
# Source: 0x30 (Our PC/Pi), Dest: 0x08 (Boiler), Cmd: B5 11, Data: 00
# checksum logic is usually handled by the connection class,
# but if we need raw bytes, we calculate it.
POLL_COMMAND_BYTES = bytes.fromhex("30 08 B5 11 01 00")


def create_poll_packet():
    """
    Creates the raw bytes for a Status Request.
    Using Source 0x30 (common for PC interface) -> Dest 0x08 (Boiler)
    """
    # Simple CRC8 implementation for eBUS (Poly 0xD5? No, eBUS is 0xB9/0x9B usually)
    # Actually, usually the library handles the handshake (SYN, arbitration).
    # If your library only reads, we can't poll easily.
    pass


def main():
    logger = logging.getLogger("Main")
    logger.info("🚀 Starting Thelia Ebus Bridge (Active Mode)...")

    ebus_config = ConnectionConfig(port=SERIAL_PORT, baudrate=2400)
    connection = SerialConnection(ebus_config)

    parser = TheliaParser()
    aggregator = DataAggregator()

    mqtt_client = HAMqttClient(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS)
    mqtt_client.connect()

    parser.register_callback(aggregator.update)

    if not connection.connect():
        logger.error("❌ Could not open serial port. Exiting.")
        sys.exit(1)

    logger.info("✅ System Running. Monitoring...")

    last_publish = 0
    PUBLISH_INTERVAL = 30

    # NEW: Polling timers
    last_poll = 0
    POLL_INTERVAL = 60  # Ask for status every 60s if we haven't heard it

    try:
        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)

            # 1. Handle Writes
            if msg.name == "param_write":
                logger.info("⚡ Instant Update Triggered")
                mqtt_client.publish_sensors(aggregator.get_all_sensors())

            # 2. Check if we received fresh Status (Type 0) naturally
            # If we did, reset our "last_poll" timer so we don't spam
            if msg.name == "status_temps" and msg.query_data.get('query_type') == 0:
                last_poll = time.time()

            # 3. Publish to MQTT
            now = time.time()
            if now - last_publish > PUBLISH_INTERVAL:
                sensors = aggregator.get_all_sensors()
                if sensors:
                    mqtt_client.publish_sensors(sensors)
                last_publish = now

            # 4. ACTIVE POLLING LOGIC
            # This is complex without a robust 'write' library because of bus arbitration.
            # If your ebus_core doesn't support writing safely, skipping this block.

            # Simple check to see if sensors are stale
            sensors = aggregator.get_all_sensors()
            if "boiler.flame_on" not in sensors:
                # If flame status is missing/stale, warn the user
                # (Active polling code requires the 'write' implementation from ebus_core)
                pass

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()