import json
import logging
import time
from typing import Dict, Any
import paho.mqtt.client as mqtt


class HAMqttClient:
    def __init__(self, broker: str, port: int, username: str = None, password: str = None):
        self.logger = logging.getLogger("MQTT")

        # FIX for paho-mqtt 2.0: Define Callback API Version
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "ebus_thelia_bridge")

        if username and password:
            self.client.username_pw_set(username, password)

        self.broker = broker
        self.port = port
        self.connected = False
        self.discovery_sent = False

        # Define Home Assistant Entity Configurations
        self.entity_map = {
            "boiler.flow_temperature": {
                "name": "Boiler Flow Temperature",
                "class": "temperature",
                "icon": "mdi:thermometer-chevron-up",
                "state_class": "measurement"
            },
            "boiler.return_temperature": {
                "name": "Boiler Return Temperature",
                "class": "temperature",
                "icon": "mdi:thermometer-chevron-down",
                "state_class": "measurement"
            },
            "boiler.dhw_tank_temperature": {
                "name": "DHW Cylinder Temp",
                "class": "temperature",
                "icon": "mdi:water-boiler",
                "state_class": "measurement"
            },
            "boiler.outdoor_temperature": {
                "name": "Outdoor Temperature",
                "class": "temperature",
                "icon": "mdi:sun-thermometer",
                "state_class": "measurement"
            },
            "boiler.water_pressure": {
                "name": "System Pressure",
                "class": "pressure",
                "icon": "mdi:gauge",
                "state_class": "measurement"
            },
            "boiler.burner_modulation": {
                "name": "Burner Modulation",
                "unit": "%",
                "icon": "mdi:fire",
                "state_class": "measurement"
            },
            "boiler.delta_t": {
                "name": "Flow-Return Delta",
                "class": "temperature",
                "icon": "mdi:vector-difference-ba",
                "state_class": "measurement"
            },
            "mipro.dhw_setpoint": {
                "name": "DHW Setpoint (Target)",
                "class": "temperature",
                "icon": "mdi:thermostat",
                "state_class": "measurement"
            },
            "mipro.room_temperature": {
                "name": "MiPro Room Temperature",
                "class": "temperature",
                "icon": "mdi:sofa",
                "state_class": "measurement"
            },
            "boiler.flame_on": {
                "name": "Burner Flame",
                "type": "binary_sensor",
                "icon": "mdi:fire-alert"
            },
            "boiler.pump_running": {
                "name": "Pump Status",
                "type": "binary_sensor",
                "icon": "mdi:pump"
            },
            "boiler.heating_active": {
                "name": "Heating Mode",
                "type": "binary_sensor",
                "icon": "mdi:radiator"
            },
            "boiler.dhw_active": {
                "name": "DHW Charging Mode",
                "type": "binary_sensor",
                "icon": "mdi:water-sync"
            }
        }

    def connect(self):
        try:
            self.client.on_connect = self._on_connect
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            self.logger.info(f"Connecting to MQTT Broker {self.broker}...")
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT: {e}")

    # FIX for paho-mqtt 2.0: Added 'properties' argument
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.logger.info("✅ Connected to MQTT Broker!")
            self.connected = True
        else:
            self.logger.error(f"Failed to connect, return code {rc}")

    def publish_discovery(self):
        """Sends JSON config to Home Assistant so sensors appear automatically."""
        if not self.connected:
            return

        self.logger.info("Sending Auto-Discovery Config to Home Assistant...")

        for sensor_key, config in self.entity_map.items():
            # Determine type (sensor vs binary_sensor)
            component = config.get("type", "sensor")

            # Create unique ID based on key
            clean_id = sensor_key.replace(".", "_")

            # HA Discovery Topic
            disc_topic = f"homeassistant/{component}/ebus_thelia/{clean_id}/config"

            payload = {
                "name": f"Thelia {config['name']}",
                "unique_id": f"thelia_ebus_{clean_id}",
                "state_topic": f"ebus/thelia/{sensor_key}",
                "availability_topic": "ebus/thelia/status",
                "device": {
                    "identifiers": ["saunier_duval_thelia_condens"],
                    "name": "Saunier Duval Thelia Condens",
                    "manufacturer": "Saunier Duval",
                    "model": "Thelia Condens + MiPro"
                }
            }

            # Add optional fields for Charts/UI
            if "class" in config:
                payload["device_class"] = config["class"]
            if "unit" in config:
                payload["unit_of_measurement"] = config["unit"]
            # Auto-detect unit for temperature/pressure if not specified
            elif config.get("class") == "temperature":
                payload["unit_of_measurement"] = "°C"
            elif config.get("class") == "pressure":
                payload["unit_of_measurement"] = "bar"

            if "icon" in config:
                payload["icon"] = config["icon"]
            if "state_class" in config:
                payload["state_class"] = config["state_class"]

            # Publish with Retain=True so HA finds it on reboot
            self.client.publish(disc_topic, json.dumps(payload), retain=True)

        # Publish availability
        self.client.publish("ebus/thelia/status", "online", retain=True)
        self.discovery_sent = True

    def publish_sensors(self, sensors: Dict[str, Dict]):
        """Publishes the actual sensor values."""
        if not self.connected:
            return

        # If we haven't sent discovery config yet, do it once
        if not self.discovery_sent:
            self.publish_discovery()

        for key, data in sensors.items():
            topic = f"ebus/thelia/{key}"
            value = data["value"]

            # Convert booleans to payloads HA expects
            if isinstance(value, bool):
                payload = "ON" if value else "OFF"
            else:
                payload = str(value)

            self.client.publish(topic, payload)