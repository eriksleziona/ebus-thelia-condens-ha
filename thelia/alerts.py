"""
Alert logic for Thelia Condens.
Checks sensor values against defined thresholds.
"""

import logging
import time
from typing import Dict, List, Callable, Optional
from dataclasses import dataclass


@dataclass
class Alert:
    level: str  # "WARNING", "INFO", "CRITICAL"
    message: str
    sensor: str
    value: float
    timestamp: float


class AlertManager:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._callbacks: List[Callable[[Alert], None]] = []
        self._active_alerts: Dict[str, Alert] = {}

        # Define thresholds matching your live_test.py description
        self.rules = [
            {
                "sensor": "boiler.water_pressure",
                "condition": lambda v: v < 0.8,
                "level": "CRITICAL",
                "msg": "⚠️ Low water pressure (< 0.8 bar)"
            },
            {
                "sensor": "boiler.water_pressure",
                "condition": lambda v: v > 2.5,
                "level": "WARNING",
                "msg": "⚠️ High water pressure (> 2.5 bar)"
            },
            {
                "sensor": "boiler.return_temperature",
                "condition": lambda v: v > 55.0,
                "level": "INFO",
                "msg": "ℹ️ Return temp high - Condensing inefficient (> 55°C)"
            },
            {
                "sensor": "boiler.delta_t",
                "condition": lambda v: v > 20.0,
                "level": "WARNING",
                "msg": "⚠️ High ΔT Flow/Return (> 20°C)"
            },
            {
                "sensor": "boiler.flow_temperature",
                "condition": lambda v: v > 80.0,
                "level": "WARNING",
                "msg": "⚠️ High Flow Temperature (> 80°C)"
            }
        ]

    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        self._callbacks.append(callback)

    def _notify(self, alert: Alert) -> None:
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as e:
                self.logger.error(f"Alert callback error: {e}")

    def check_sensors(self, sensors: Dict[str, Dict]) -> None:
        """
        Iterate through defined rules and check against current sensor values.
        """
        current_time = time.time()

        for rule in self.rules:
            sensor_name = rule["sensor"]

            # Skip if sensor data not available
            if sensor_name not in sensors:
                continue

            sensor_data = sensors[sensor_name]
            value = sensor_data["value"]

            # Skip if data is too old (e.g., > 5 minutes)
            if sensor_data.get("age_seconds", 0) > 300:
                continue

            # Check the condition
            is_triggered = False
            try:
                if isinstance(value, (int, float)):
                    is_triggered = rule["condition"](value)
            except Exception:
                continue

            alert_key = f"{sensor_name}_{rule['level']}"

            if is_triggered:
                # If this is a new alert (not currently active)
                if alert_key not in self._active_alerts:
                    alert = Alert(
                        level=rule["level"],
                        message=rule["msg"],
                        sensor=sensor_name,
                        value=value,
                        timestamp=current_time
                    )
                    self._active_alerts[alert_key] = alert
                    self._notify(alert)
            else:
                # If alert was active but condition is now cleared, remove it
                if alert_key in self._active_alerts:
                    del self._active_alerts[alert_key]
                    # Optional: Notify "Resolved" if you wanted to add that logic here

    def check_sensor_staleness(self, sensors: Dict[str, Dict]) -> None:
        """
        Check if critical sensors have stopped updating.
        """
        critical_sensors = ["boiler.water_pressure", "boiler.flow_temperature"]

        for name in critical_sensors:
            if name in sensors:
                age = sensors[name]["age_seconds"]
                if age > 600:  # 10 minutes
                    key = f"{name}_stale"
                    if key not in self._active_alerts:
                        alert = Alert(
                            level="WARNING",
                            message=f"⚠️ Sensor data stale: {name}",
                            sensor=name,
                            value=age,
                            timestamp=time.time()
                        )
                        self._active_alerts[key] = alert
                        self._notify(alert)
                else:
                    # Clear stale alert if data is fresh again
                    key = f"{name}_stale"
                    if key in self._active_alerts:
                        del self._active_alerts[key]

    def print_status(self) -> None:
        if not self._active_alerts:
            print("\n✅ No active alerts.")
            return

        print("\n" + "!" * 40)
        print(f"🚨 ACTIVE ALERTS ({len(self._active_alerts)})")
        print("!" * 40)
        for alert in self._active_alerts.values():
            print(f"   • [{alert.level}] {alert.message} (Value: {alert.value})")
        print("\n")