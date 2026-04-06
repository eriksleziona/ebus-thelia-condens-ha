import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

import paho.mqtt.client as mqtt


class HAMqttClient:
    def __init__(self, broker: str, port: int, username: str = None, password: str = None):
        self.logger = logging.getLogger("MQTT")

        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.connected = False
        self.discovery_sent = False
        self._discovered_entities = set()
        self._loop_started = False
        self._ever_connected = False
        self._last_connect_attempt_monotonic = 0.0
        self._last_restart_monotonic = 0.0
        self._last_publish_attempt_monotonic: Optional[float] = None
        self._last_successful_publish_monotonic: Optional[float] = None
        self._consecutive_publish_failures = 0
        self._heartbeat_topic = "ebus/thelia/bridge_heartbeat"
        self._status_topic = "ebus/thelia/status"
        self.client = self._create_client()

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
            "boiler.room_temperature": {
                "name": "Room Temperature",
                "class": "temperature",
                "icon": "mdi:home-thermometer",
                "state_class": "measurement",
            },
            "mipro.room_temperature": {
                "name": "Room Temperature (MiPro)",
                "class": "temperature",
                "icon": "mdi:sofa-outline",
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
            "boiler.burner_power_percent": {
                "name": "Burner Power",
                "unit": "%",
                "icon": "mdi:fire-circle",
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
            "boiler.dhw_setpoint_active": {
                "name": "DHW Setpoint Active",
                "class": "temperature",
                "icon": "mdi:thermostat",
                "state_class": "measurement",
            },
            "boiler.max_flow_temp_limit": {
                "name": "Max Flow Temp Limit",
                "class": "temperature",
                "icon": "mdi:thermometer-high",
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
            "boiler.state_code": {
                "name": "Boiler State Code",
                "icon": "mdi:counter",
                "state_class": "measurement",
            },
            "boiler.b511_q1_byte3_raw": {
                "name": "B511 Q1 Byte3 Raw",
                "icon": "mdi:code-braces",
                "state_class": "measurement",
            },
            "boiler.b511_q1_byte4_raw": {
                "name": "B511 Q1 Byte4 Raw",
                "icon": "mdi:code-braces",
                "state_class": "measurement",
            },
            "boiler.b511_q2_byte4_raw": {
                "name": "B511 Q2 Byte4 Raw",
                "icon": "mdi:code-braces",
                "state_class": "measurement",
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

    def _create_client(self):
        # paho-mqtt >=2.0 requires explicit callback API version.
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "ebus_thelia_bridge")
        if self.username and self.password:
            client.username_pw_set(self.username, self.password)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.will_set(self._status_topic, "offline", retain=True)
        client.enable_logger(self.logger)
        return client

    def _device_descriptor(self) -> Dict[str, Any]:
        return {
            "identifiers": ["saunier_duval_thelia_condens"],
            "name": "Saunier Duval Thelia Condens",
            "manufacturer": "Saunier Duval",
            "model": "Thelia Condens",
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

    def _publish_result_ok(
        self,
        result,
        *,
        topic: str,
        context: str,
        wait_for_publish: bool = False,
        timeout_s: float = 2.0,
    ) -> bool:
        self._last_publish_attempt_monotonic = time.monotonic()

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.connected = False
            self.discovery_sent = False
            self._consecutive_publish_failures += 1
            self.logger.warning(
                "MQTT %s failed with rc=%s for topic %s",
                context,
                result.rc,
                topic,
            )
            return False

        if wait_for_publish:
            try:
                result.wait_for_publish(timeout=timeout_s)
            except Exception as e:
                self._consecutive_publish_failures += 1
                self.logger.warning("MQTT %s wait_for_publish failed for %s: %s", context, topic, e)
                return False

            if hasattr(result, "is_published") and not result.is_published():
                self._consecutive_publish_failures += 1
                self.logger.warning(
                    "MQTT %s was queued locally but never confirmed for topic %s",
                    context,
                    topic,
                )
                return False

        self._consecutive_publish_failures = 0
        self._last_successful_publish_monotonic = time.monotonic()
        return True

    def ensure_connection(self, reason: str = "runtime") -> bool:
        try:
            client_connected = bool(self.client.is_connected())
        except Exception:
            client_connected = False

        if self.connected and client_connected:
            return True

        self.connected = False
        now = time.monotonic()
        if (now - self._last_connect_attempt_monotonic) < 5.0:
            return False

        self._last_connect_attempt_monotonic = now

        try:
            if self._ever_connected:
                self.logger.warning("Requesting MQTT reconnect (%s)", reason)
                self.client.reconnect()
            else:
                self.logger.warning("Requesting initial MQTT connection (%s)", reason)
                if not self._loop_started:
                    self.client.loop_start()
                    self._loop_started = True
                self.client.connect_async(self.broker, self.port, 60)
        except Exception as e:
            self.logger.warning("MQTT connection request failed during %s: %s", reason, e)
            return False

        return False

    def restart(self, reason: str) -> None:
        now = time.monotonic()
        if (now - self._last_restart_monotonic) < 15.0:
            return

        self._last_restart_monotonic = now
        self.logger.warning("Restarting MQTT client: %s", reason)

        old_client = self.client
        try:
            old_client.disconnect()
        except Exception as e:
            self.logger.warning("MQTT disconnect during restart failed: %s", e)

        try:
            if self._loop_started:
                old_client.loop_stop()
        except Exception as e:
            self.logger.warning("MQTT loop_stop during restart failed: %s", e)

        self.connected = False
        self.discovery_sent = False
        self._discovered_entities.clear()
        self._loop_started = False
        self._ever_connected = False
        self.client = self._create_client()
        self.connect()

    def _publish_message(
        self,
        topic: str,
        payload,
        *,
        retain: bool = False,
        qos: int = 0,
        wait_for_publish: bool = False,
        timeout_s: float = 2.0,
        context: str = "publish",
    ) -> bool:
        if not self.ensure_connection(context):
            return False

        result = self.client.publish(topic, payload, qos=qos, retain=retain)
        return self._publish_result_ok(
            result,
            topic=topic,
            context=context,
            wait_for_publish=wait_for_publish,
            timeout_s=timeout_s,
        )

    def _publish_discovery_for_sensor(self, sensor_key: str, config: Dict[str, Any]) -> bool:
        component, clean_id, payload = self._build_discovery_payload(sensor_key, config)
        disc_topic = f"homeassistant/{component}/ebus_thelia/{clean_id}/config"
        if not self._publish_message(
            disc_topic,
            json.dumps(payload),
            retain=True,
            context=f"discovery:{sensor_key}",
        ):
            return False
        self._discovered_entities.add(sensor_key)
        return True

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
            if not self._loop_started:
                self.client.loop_start()
                self._loop_started = True
            self._last_connect_attempt_monotonic = time.monotonic()
            self.client.connect_async(self.broker, self.port, 60)
            self.logger.info(f"Starting MQTT connection loop for {self.broker}:{self.port}...")
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT: {e}")

    # paho-mqtt 2.0 callback signature includes "properties".
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if int(rc) == 0:
            self.logger.info("Connected to MQTT Broker")
            self.connected = True
            self._ever_connected = True
            self.discovery_sent = False
            self._discovered_entities.clear()
            self._publish_message(
                self._status_topic,
                "online",
                retain=True,
                qos=1,
                context="availability-online",
            )
        else:
            self.logger.error(f"Failed to connect, return code {rc}")

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, properties=None):
        self.connected = False
        self.discovery_sent = False

        if int(rc) == 0:
            self.logger.info("Disconnected from MQTT Broker")
        else:
            self.logger.warning(f"MQTT disconnected unexpectedly, return code {rc}")

    def publish_discovery(self):
        """Send discovery config so Home Assistant can auto-create entities."""
        if not self.connected:
            return False

        self.logger.info("Sending Auto-Discovery Config to Home Assistant...")
        for sensor_key, config in self.entity_map.items():
            if not self._publish_discovery_for_sensor(sensor_key, config):
                return False

        self.discovery_sent = True
        return True

    def publish_sensors(self, sensors: Dict[str, Dict]):
        """Publish sensor values to MQTT, with dynamic HA discovery for new keys."""
        if not sensors:
            return True

        if not self.ensure_connection("sensor publish"):
            return False

        if not self.discovery_sent:
            if not self.publish_discovery():
                self.restart("discovery publish failed")
                return False

        for key, data in sensors.items():
            if not isinstance(data, dict) or "value" not in data:
                continue

            if key not in self._discovered_entities:
                config = self.entity_map.get(key) or self._infer_dynamic_config(key, data)
                if not self._publish_discovery_for_sensor(key, config):
                    self.restart(f"dynamic discovery failed for {key}")
                    return False

            topic = f"ebus/thelia/{key}"
            value = data["value"]
            if isinstance(value, bool):
                payload = "ON" if value else "OFF"
            else:
                payload = str(value)
            if not self._publish_message(topic, payload, context=f"sensor:{key}"):
                self.restart(f"sensor publish failed for {key}")
                return False

        return True

    def publish_healthcheck(self) -> bool:
        payload = str(int(time.time()))
        if self._publish_message(
            self._heartbeat_topic,
            payload,
            qos=1,
            wait_for_publish=True,
            timeout_s=3.0,
            context="healthcheck",
        ):
            return True

        self.restart("healthcheck failed")
        return False

    def seconds_since_last_successful_publish(self, now: Optional[float] = None) -> Optional[float]:
        if self._last_successful_publish_monotonic is None:
            return None

        current = time.monotonic() if now is None else now
        return max(0.0, current - self._last_successful_publish_monotonic)

    def disconnect(self):
        """Disconnect from MQTT broker and stop background loop."""
        try:
            if self.connected:
                self._publish_message(
                    self._status_topic,
                    "offline",
                    retain=True,
                    qos=1,
                    context="availability-offline",
                )
            self.client.disconnect()
        except Exception as e:
            self.logger.warning(f"Failed to disconnect MQTT cleanly: {e}")
        finally:
            if self._loop_started:
                self.client.loop_stop()
                self._loop_started = False
            self.connected = False
