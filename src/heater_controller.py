import time
from src.ebus_reader import EbusReader
from src.mqtt_publisher import MQTTPublisher
from src.utils.logger import setup_logger


class HeaterController:
    """Main controller for heater integration."""

    def __init__(self, config):
        self.config = config
        self.logger = setup_logger(
            __name__,
            log_file=config.get('logging.file'),
            level=config.get('logging.level', 'INFO')
        )

        self.ebus = EbusReader(config)
        self.mqtt = MQTTPublisher(config)
        self.mqtt.set_message_callback(self.handle_mqtt_message)

        self.running = False
        self.update_interval = 30  # seconds

    def handle_mqtt_message(self, topic, payload):
        """Handle incoming MQTT commands from Home Assistant."""
        try:
            base_topic = self.config.get('mqtt.base_topic')

            if topic == f"{base_topic}/target_temp/set":
                temp = float(payload)
                self.logger.info(f"Setting target temperature to {temp}°C")
                self.ebus.set_heating_temp(temp)
                self.mqtt.client.publish(f"{base_topic}/target_temp", str(temp))

            elif topic == f"{base_topic}/mode/set":
                mode = payload.lower()
                self.logger.info(f"Mode change requested: {mode}")
                # Implement mode changes based on your heater capabilities
                self.mqtt.client.publish(f"{base_topic}/mode", mode)

        except Exception as e:
            self.logger.error(f"Error handling MQTT message: {e}")

    def start(self):
        """Start the controller."""
        self.logger.info("Starting Thelia eBUS MQTT controller")

        # Connect to MQTT
        if not self.mqtt.connect():
            self.logger.error("Failed to connect to MQTT broker")
            return False

        self.running = True
        self.run_loop()

        return True

    def run_loop(self):
        """Main control loop."""
        while self.running:
            try:
                # Read heater status
                status = self.ebus.get_heater_status()

                # Publish to MQTT
                self.mqtt.publish_state(status)

                # Wait for next update
                time.sleep(self.update_interval)

            except KeyboardInterrupt:
                self.logger.info("Received shutdown signal")
                self.stop()
                break
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}")
                time.sleep(5)

    def stop(self):
        """Stop the controller."""
        self.logger.info("Stopping controller")
        self.running = False
        self.mqtt.disconnect()