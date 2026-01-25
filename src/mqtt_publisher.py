import paho.mqtt.client as mqtt
import json
import time
from src.utils.logger import setup_logger


class MQTTPublisher:
    """Handle MQTT publishing to Home Assistant."""

    def __init__(self, config):
        self.config = config
        self.logger = setup_logger(__name__)
        self.client = mqtt.Client(client_id=config.get('mqtt.client_id'))
        self.base_topic = config.get('mqtt.base_topic')
        self.discovery_prefix = config.get('mqtt.discovery_prefix')
        self.connected = False

        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection."""
        if rc == 0:
            self.logger.info("Connected to MQTT broker")
            self.connected = True
            self.publish_discovery()
            # Subscribe to command topic
            self.client.subscribe(f"{self.base_topic}/set")
        else:
            self.logger.error(f"Failed to connect to MQTT broker: {rc}")
            self.connected = False

    def _on_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection."""
        self.logger.warning(f"Disconnected from MQTT broker: {rc}")
        self.connected = False

    def _on_message(self, client, userdata, msg):
        """Callback for incoming MQTT messages."""
        self.logger.info(f"Received message on {msg.topic}: {msg.payload.decode()}")
        # This will be handled by the controller
        if hasattr(self, 'message_callback'):
            self.message_callback(msg.topic, msg.payload.decode())

    def connect(self):
        """Connect to MQTT broker."""
        try:
            broker = self.config.get('mqtt.broker')
            port = self.config.get('mqtt.port')
            self.logger.info(f"Connecting to MQTT broker at {broker}:{port}")
            self.client.connect(broker, port, 60)
            self.client.loop_start()

            # Wait for connection
            timeout = 10
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)

            return self.connected

        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    def publish_discovery(self):
        """Publish Home Assistant MQTT discovery messages."""
        device_info = {
            "identifiers": ["thelia_heater"],
            "name": self.config.get('heater.name'),
            "manufacturer": self.config.get('heater.manufacturer'),
            "model": self.config.get('heater.model')
        }

        # Climate entity
        climate_config = {
            "name": "Thelia Heating",
            "unique_id": "thelia_climate",
            "device": device_info,
            "mode_state_topic": f"{self.base_topic}/mode",
            "mode_command_topic": f"{self.base_topic}/mode/set",
            "temperature_state_topic": f"{self.base_topic}/target_temp",
            "temperature_command_topic": f"{self.base_topic}/target_temp/set",
            "current_temperature_topic": f"{self.base_topic}/current_temp",
            "modes": ["off", "heat"],
            "min_temp": 5,
            "max_temp": 30,
            "temp_step": 0.5,
            "temperature_unit": "C"
        }

        self.client.publish(
            f"{self.discovery_prefix}/climate/thelia/config",
            json.dumps(climate_config),
            retain=True
        )

        # Sensor entities
        sensors = [
            ("Flow Temperature", "flow_temp", "°C", "temperature"),
            ("Return Temperature", "return_temp", "°C", "temperature"),
            ("Water Pressure", "water_pressure", "bar", "pressure"),
            ("DHW Temperature", "dhw_temp", "°C", "temperature"),
            ("Modulation Level", "modulation", "%", None),
        ]

        for name, key, unit, device_class in sensors:
            sensor_config = {
                "name": f"Thelia {name}",
                "unique_id": f"thelia_{key}",
                "device": device_info,
                "state_topic": f"{self.base_topic}/{key}",
                "unit_of_measurement": unit,
            }
            if device_class:
                sensor_config["device_class"] = device_class

            self.client.publish(
                f"{self.discovery_prefix}/sensor/thelia_{key}/config",
                json.dumps(sensor_config),
                retain=True
            )

        self.logger.info("Published Home Assistant discovery messages")

    def publish_state(self, status):
        """Publish heater state to MQTT."""
        if not self.connected:
            self.logger.warning("Not connected to MQTT broker")
            return

        # Publish individual sensor values
        for key, value in status.items():
            if value is not None:
                self.client.publish(f"{self.base_topic}/{key}", str(value))

        self.logger.debug(f"Published state: {status}")

    def set_message_callback(self, callback):
        """Set callback for incoming messages."""
        self.message_callback = callback

    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()