"""
Alert logic for Thelia Condens.
Checks sensor values against defined thresholds.
"""

import logging
import time
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field
from enum import Enum, auto


# ==========================================
# 1. Define the missing Types and Enums
# ==========================================

class AlertSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(Enum):
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    SYSTEM = "system"
    COMMUNICATION = "communication"


@dataclass
class AlertThreshold:
    """Defines a rule for triggering an alert."""
    sensor: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    severity: AlertSeverity = AlertSeverity.WARNING
    alert_type: AlertType = AlertType.SYSTEM
    message: str = "Alert triggered"
    condition: Optional[Callable[[Any], bool]] = None


@dataclass
class Alert:
    """The actual alert instance generated when a rule is broken."""
    severity: AlertSeverity
    alert_type: AlertType
    message: str
    sensor: str
    value: Any
    timestamp: float

    def __str__(self):
        icon = "ℹ️"
        if self.severity == AlertSeverity.WARNING:
            icon = "⚠️"
        elif self.severity == AlertSeverity.CRITICAL:
            icon = "🚨"
        return f"{icon} [{self.severity.value}] {self.message} (Value: {self.value})"


# ==========================================
# 2. The Alert Manager
# ==========================================

class AlertManager:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._callbacks: List[Callable[[Alert], None]] = []
        self._active_alerts: Dict[str, Alert] = {}
        self.rules: List[AlertThreshold] = []

        # Load default rules matching your live_test requirements
        self._load_default_rules()

    def _load_default_rules(self):
        """Define the logic for Pressure, Delta T, etc."""
        self.rules = [
            # Low Pressure (< 0.8 bar)
            AlertThreshold(
                sensor="boiler.water_pressure",
                min_value=0.8,
                severity=AlertSeverity.CRITICAL,
                alert_type=AlertType.PRESSURE,
                message="Low water pressure"
            ),
            # High Pressure (> 2.5 bar)
            AlertThreshold(
                sensor="boiler.water_pressure",
                max_value=2.5,
                severity=AlertSeverity.WARNING,
                alert_type=AlertType.PRESSURE,
                message="High water pressure"
            ),
            # Condensing Inefficiency (Return > 55°C)
            AlertThreshold(
                sensor="boiler.return_temperature",
                max_value=55.0,
                severity=AlertSeverity.INFO,
                alert_type=AlertType.TEMPERATURE,
                message="Return temp high - Condensing inefficient"
            ),
            # High Delta T (> 20°C)
            AlertThreshold(
                sensor="boiler.delta_t",
                max_value=20.0,
                severity=AlertSeverity.WARNING,
                alert_type=AlertType.TEMPERATURE,
                message="High ΔT Flow/Return"
            ),
            # High Flow Temp (> 80°C)
            AlertThreshold(
                sensor="boiler.flow_temperature",
                max_value=80.0,
                severity=AlertSeverity.WARNING,
                alert_type=AlertType.TEMPERATURE,
                message="High Flow Temperature"
            )
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
            # Skip if sensor data not available
            if rule.sensor not in sensors:
                continue

            sensor_data = sensors[rule.sensor]
            value = sensor_data["value"]

            # Skip if data is too old (e.g., > 5 minutes)
            if sensor_data.get("age_seconds", 0) > 300:
                continue

            # Check logic
            is_triggered = False
            try:
                if isinstance(value, (int, float)):
                    if rule.min_value is not None and value < rule.min_value:
                        is_triggered = True
                    if rule.max_value is not None and value > rule.max_value:
                        is_triggered = True
                    if rule.condition and rule.condition(value):
                        is_triggered = True
            except Exception:
                continue

            # Create a unique key for this specific alert rule
            # e.g. "boiler.water_pressure_CRITICAL"
            alert_key = f"{rule.sensor}_{rule.severity.name}_{rule.alert_type.name}"

            if is_triggered:
                # Only notify if this is a NEW alert
                if alert_key not in self._active_alerts:
                    alert = Alert(
                        severity=rule.severity,
                        alert_type=rule.alert_type,
                        message=rule.message,
                        sensor=rule.sensor,
                        value=value,
                        timestamp=current_time
                    )
                    self._active_alerts[alert_key] = alert
                    self._notify(alert)
            else:
                # Clear alert if condition is resolved
                if alert_key in self._active_alerts:
                    del self._active_alerts[alert_key]

    def check_sensor_staleness(self, sensors: Dict[str, Dict]) -> None:
        """
        Check if critical sensors have stopped updating.
        """
        critical_sensors = ["boiler.water_pressure", "boiler.flow_temperature"]

        for name in critical_sensors:
            if name in sensors:
                age = sensors[name]["age_seconds"]
                key = f"{name}_stale"

                if age > 600:  # 10 minutes
                    if key not in self._active_alerts:
                        alert = Alert(
                            severity=AlertSeverity.WARNING,
                            alert_type=AlertType.COMMUNICATION,
                            message=f"Sensor data stale (>10m)",
                            sensor=name,
                            value=f"{age:.0f}s",
                            timestamp=time.time()
                        )
                        self._active_alerts[key] = alert
                        self._notify(alert)
                else:
                    if key in self._active_alerts:
                        del self._active_alerts[key]

    def print_status(self) -> None:
        if not self._active_alerts:
            print("\n✅ No active alerts.")
            return

        print("\n" + "!" * 50)
        print(f"🚨 ACTIVE ALERTS ({len(self._active_alerts)})")
        print("!" * 50)
        for alert in self._active_alerts.values():
            print(f"   {alert}")
        print("\n")