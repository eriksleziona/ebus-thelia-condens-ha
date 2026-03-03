import json
import logging
from typing import Any, Dict, Tuple

import paho.mqtt.client as mqtt


class HAMqttClient:
    def __init__(self, broker: str, port: int, username: str = None, password: str = None):
        self.logger = logging.getLogger("MQTT")

        # paho-mqtt >=2.0 requires explicit callback API version.
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "ebus_thelia_bridge")
        if username and password:
            self.client.username_pw_set(username, password)

        self.broker = broker
        self.port = port
        self.connected = False
        self.discovery_sent = False
        self._discovered_entities = set()

        self.entity_map = {
            "boiler.flow_temperature": {
                "name": "Boiler Flow Temperature",
                "class": "temperature",
                "icon": "mdi:thermometer-chevron-up",
                "state_class": "measurement",
            },
            "boiler.return_temperature": {
                "name": "Boiler Return Temperature",
                "class": "temperature",
                "icon": "mdi:thermometer-chevron-down",
                "state_class": "measurement",
            },
            "boiler.dhw_tank_temperature": {
                "name": "DHW Cylinder Temp",
                "class": "temperature",
                "icon": "mdi:water-boiler",
                "state_class": "measurement",
            },
            "boiler.outdoor_temperature": {
                "name": "Outdoor Temperature",
                "class": "temperature",
                "icon": "mdi:sun-thermometer",
                "state_class": "measurement",
            },
            "boiler.water_pressure": {
                "name": "System Pressure",
                "class": "pressure",
                "icon": "mdi:gauge",
                "state_class": "measurement",
            },
            "boiler.burner_modulation": {
                "name": "Burner Modulation",
                "unit": "%",
                "icon": "mdi:fire",
                "state_class": "measurement",
            },
            "boiler.burner_modulation_q2": {
                "name": "Burner Modulation (B511 Q2)",
                "unit": "%",
                "icon": "mdi:chart-bell-curve-cumulative",
                "state_class": "measurement",
            },
            "boiler.modulation_source": {
                "name": "Modulation Source",
                "icon": "mdi:source-branch",
            },
            "boiler.modulation_raw_hex": {
                "name": "Modulation Raw Hex",
                "icon": "mdi:code-braces",
            },
            "boiler.modulation_last_update_s": {
                "name": "Modulation Last Update Age",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:update",
                "state_class": "measurement",
            },
            "boiler.delta_t": {
                "name": "Flow-Return Delta",
                "class": "temperature",
                "icon": "mdi:vector-difference-ba",
                "state_class": "measurement",
            },
            "mipro.dhw_setpoint": {
                "name": "DHW Setpoint (Target)",
                "class": "temperature",
                "icon": "mdi:thermostat",
                "state_class": "measurement",
            },
            "mipro.room_temperature": {
                "name": "MiPro Room Temperature",
                "class": "temperature",
                "icon": "mdi:sofa",
                "state_class": "measurement",
            },
            "boiler.flame_on": {
                "name": "Burner Flame",
                "type": "binary_sensor",
                "icon": "mdi:fire-alert",
            },
            "boiler.burner_start_count": {
                "name": "Burner Start Count",
                "icon": "mdi:counter",
                "state_class": "total_increasing",
            },
            "boiler.burner_starts_today": {
                "name": "Burner Starts Today",
                "icon": "mdi:calendar-today",
                "state_class": "measurement",
            },
            "boiler.burner_starts_24h": {
                "name": "Burner Starts 24h",
                "icon": "mdi:calendar-clock",
                "state_class": "measurement",
            },
            "boiler.burner_starts_7d": {
                "name": "Burner Starts 7d",
                "icon": "mdi:calendar-week",
                "state_class": "measurement",
            },
            "boiler.burner_runtime_total_s": {
                "name": "Burner Runtime Total",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:timer-outline",
                "state_class": "total_increasing",
            },
            "boiler.burner_runtime_current_cycle_s": {
                "name": "Burner Runtime Current Cycle",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:timer-play-outline",
                "state_class": "measurement",
            },
            "boiler.burner_last_cycle_s": {
                "name": "Burner Runtime Last Cycle",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:history",
                "state_class": "measurement",
            },
            "boiler.last_flame_on": {
                "name": "Last Burner ON",
                "class": "timestamp",
                "icon": "mdi:clock-start",
            },
            "boiler.last_flame_off": {
                "name": "Last Burner OFF",
                "class": "timestamp",
                "icon": "mdi:clock-end",
            },
            "boiler.ebus_last_seen_s": {
                "name": "eBUS Last Seen Age",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:lan",
                "state_class": "measurement",
            },
            "boiler.status_last_update_s": {
                "name": "Status Last Update Age",
                "class": "duration",
                "unit": "s",
                "icon": "mdi:clock-alert-outline",
                "state_class": "measurement",
            },
            "boiler.status_stale": {
                "name": "Status Stale",
                "type": "binary_sensor",
                "icon": "mdi:alert-circle-outline",
            },
            "boiler.pump_status": {
                "name": "Pump Status",
                "type": "binary_sensor",
                "icon": "mdi:pump",
            },
            "boiler.heating_active": {
                "name": "Heating Mode",
                "type": "binary_sensor",
                "icon": "mdi:radiator",
            },
            "boiler.dhw_active": {
                "name": "DHW Charging Mode",
                "type": "binary_sensor",
                "icon": "mdi:water-sync",
            },
        }

    def _device_descriptor(self) -> Dict[str, Any]:
        return {
            "identifiers": ["saunier_duval_thelia_condens"],
            "name": "Saunier Duval Thelia Condens",
            "manufacturer": "Saunier Duval",
            "model": "Thelia Condens + MiPro",
        }

    def _build_discovery_payload(self, sensor_key: str, config: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
        component = config.get("type", "sensor")
        clean_id = sensor_key.replace(".", "_")
        payload = {
            "name": f"Thelia {config['name']}",
            "unique_id": f"thelia_ebus_{clean_id}",
            "state_topic": f"ebus/thelia/{sensor_key}",
            "availability_topic": "ebus/thelia/status",
            "device": self._device_descriptor(),
        }

        if "class" in config:
            payload["device_class"] = config["class"]

        if "unit" in config and config["unit"]:
            payload["unit_of_measurement"] = config["unit"]
        elif config.get("class") == "temperature":
            payload["unit_of_measurement"] = "°C"
        elif config.get("class") == "pressure":
            payload["unit_of_measurement"] = "bar"
        elif config.get("class") == "duration":
            payload["unit_of_measurement"] = "s"

        if "icon" in config:
            payload["icon"] = config["icon"]

        if config.get("type", "sensor") == "sensor" and "state_class" in config:
            payload["state_class"] = config["state_class"]

        return component, clean_id, payload

    def _publish_discovery_for_sensor(self, sensor_key: str, config: Dict[str, Any]) -> None:
        component, clean_id, payload = self._build_discovery_payload(sensor_key, config)
        disc_topic = f"homeassistant/{component}/ebus_thelia/{clean_id}/config"
        self.client.publish(disc_topic, json.dumps(payload), retain=True)
        self._discovered_entities.add(sensor_key)

    @staticmethod
    def _friendly_name(sensor_key: str) -> str:
        return sensor_key.replace(".", " ").replace("_", " ").title()

    def _infer_dynamic_config(self, sensor_key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        value = data.get("value")
        unit = data.get("unit") or ""
        config: Dict[str, Any] = {
            "name": self._friendly_name(sensor_key),
            "icon": "mdi:chart-line",
        }

        if isinstance(value, bool):
            config["type"] = "binary_sensor"
            config["icon"] = "mdi:toggle-switch-outline"
            return config

        if isinstance(value, (int, float)):
            config["state_class"] = "measurement"
            if (
                sensor_key.endswith("_count")
                or sensor_key.endswith("_total")
                or sensor_key.endswith("_sent")
                or sensor_key.endswith("_received")
            ):
                config["state_class"] = "total_increasing"

        if isinstance(value, str) and sensor_key.endswith("_at"):
            config["class"] = "timestamp"
        elif sensor_key.endswith("_s"):
            config["class"] = "duration"
            if not unit:
                unit = "s"
        elif "temperature" in sensor_key:
            config["class"] = "temperature"
        elif "pressure" in sensor_key:
            config["class"] = "pressure"

        if unit:
            config["unit"] = unit

        return config

    def connect(self):
        try:
            self.client.on_connect = self._on_connect
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            self.logger.info(f"Connecting to MQTT Broker {self.broker}...")
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT: {e}")

    # paho-mqtt 2.0 callback signature includes "properties".
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.logger.info("Connected to MQTT Broker")
            self.connected = True
        else:
            self.logger.error(f"Failed to connect, return code {rc}")

    def publish_discovery(self):
        """Send discovery config so Home Assistant can auto-create entities."""
        if not self.connected:
            return

        self.logger.info("Sending Auto-Discovery Config to Home Assistant...")
        for sensor_key, config in self.entity_map.items():
            self._publish_discovery_for_sensor(sensor_key, config)

        self.client.publish("ebus/thelia/status", "online", retain=True)
        self.discovery_sent = True

    def publish_sensors(self, sensors: Dict[str, Dict]):
        """Publish sensor values to MQTT, with dynamic HA discovery for new keys."""
        if not self.connected:
            return

        if not self.discovery_sent:
            self.publish_discovery()

        for key, data in sensors.items():
            if not isinstance(data, dict) or "value" not in data:
                continue

            if key not in self._discovered_entities:
                config = self.entity_map.get(key) or self._infer_dynamic_config(key, data)
                self._publish_discovery_for_sensor(key, config)

            topic = f"ebus/thelia/{key}"
            value = data["value"]
            if isinstance(value, bool):
                payload = "ON" if value else "OFF"
            else:
                payload = str(value)
            self.client.publish(topic, payload)
