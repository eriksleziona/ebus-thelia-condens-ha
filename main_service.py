#!/usr/bin/env python3
import time
import logging
import sys
import os

# Configuration - CHANGE THESE TO MATCH YOUR HOME ASSISTANT
MQTT_BROKER = "192.168.1.84"  # <--- Change IP
MQTT_PORT = 1883
MQTT_USER = None  # <--- Change User (or None)
MQTT_PASS = None  # <--- Change Pass (or None)
SERIAL_PORT = "/dev/ttyAMA0"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, DataAggregator
from thelia.mqtt import HAMqttClient


def main():
    logger = logging.getLogger("Main")
    logger.info("🚀 Starting Thelia Ebus Bridge...")

    # 1. Setup EBUS Connection
    ebus_config = ConnectionConfig(port=SERIAL_PORT, baudrate=2400)
    connection = SerialConnection(ebus_config)

    # 2. Setup Logic
    parser = TheliaParser()
    aggregator = DataAggregator()

    # 3. Setup MQTT
    mqtt_client = HAMqttClient(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS)
    mqtt_client.connect()

    # Link parser to aggregator
    parser.register_callback(aggregator.update)

    if not connection.connect():
        logger.error("❌ Could not open serial port. Exiting.")
        sys.exit(1)

    logger.info("✅ System Running. Waiting for data...")

    last_publish = 0
    PUBLISH_INTERVAL = 30  # Publish to HA every 30 seconds (prevents flooding)

    try:
        for telegram in connection.telegram_generator():
            # Parse the telegram
            msg = parser.parse(telegram)

            # If we detected an "Instant Write" (like you turning the knob), publish IMMEDIATELY
            if msg.name == "param_write":
                logger.info("⚡ Detected Write Command - Triggering Instant Update")
                mqtt_client.publish_sensors(aggregator.get_all_sensors())

            # Standard interval publishing
            now = time.time()
            if now - last_publish > PUBLISH_INTERVAL:
                sensors = aggregator.get_all_sensors()
                if sensors:
                    mqtt_client.publish_sensors(sensors)
                    logger.debug(f"Published {len(sensors)} sensors to MQTT")
                last_publish = now

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()