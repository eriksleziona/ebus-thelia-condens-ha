import time
from src.ebus_direct_reader import EbusDirectReader
from src.mqtt_publisher import MQTTPublisher
from src.utils.logger import setup_logger


class HeaterController:
    """Main controller for heater integration using direct eBUS."""

    def __init__(self, config):
        self.config = config
        self.logger = setup_logger(
            __name__,
            log_file=config.get('logging.file'),
            level=config.get('logging.level', 'INFO')
        )

        self.ebus = EbusDirectReader(config)
        self.mqtt = MQTTPublisher(config)
        self.mqtt.set_message_callback(self.handle_mqtt_message)

        self.running = False
        self.publish_interval = 10  # Publish every 10 seconds

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
                self.mqtt.client.publish(f"{base_topic}/mode", mode)

        except Exception as e:
            self.logger.error(f"Error handling MQTT message: {e}")

    def start(self):
        """Start the controller."""
        self.logger.info("Starting Thelia eBUS Direct controller")

        # Connect to MQTT
        if not self.mqtt.connect():
            self.logger.error("Failed to connect to MQTT broker")
            return False

        # Start eBUS listener
        if not self.ebus.start_listening():
            self.logger.error("Failed to start eBUS listener")
            return False

        self.running = True
        self.run_loop()

        return True

    def run_loop(self):
        """Main control loop."""
        last_publish = 0

        while self.running:
            try:
                current_time = time.time()

                # Publish status at regular intervals
                if current_time - last_publish >= self.publish_interval:
                    # Get heater status
                    status = self.ebus.get_heater_status()

                    # Log status
                    self.logger.info(f"Status: {status}")

                    # Publish to MQTT
                    self.mqtt.publish_state(status)

                    last_publish = current_time

                # Sleep briefly
                time.sleep(1)

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
        self.ebus.stop_listening()
        self.mqtt.disconnect()