"""
Alert system for Thelia Condens boiler.
Monitors sensor values and triggers alerts.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(Enum):
    LOW_PRESSURE = "low_pressure"
    HIGH_PRESSURE = "high_pressure"
    HIGH_RETURN_TEMP = "high_return_temp"
    NO_CONDENSING = "no_condensing"
    HIGH_DELTA_T = "high_delta_t"
    COMMUNICATION_ERROR = "communication_error"
    FAULT_CODE = "fault_code"
    SENSOR_STALE = "sensor_stale"


@dataclass
class Alert:
    """Represents an alert."""
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    value: Any = None
    threshold: Any = None
    timestamp: datetime = field(default_factory=datetime.now)
    acknowledged: bool = False

    def __repr__(self) -> str:
        icon = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨"
        }.get(self.severity, "❓")
        return f"{icon} [{self.severity.value.upper()}] {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.alert_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged,
        }


@dataclass
class AlertThreshold:
    """Defines an alert threshold."""
    alert_type: AlertType
    sensor_name: str
    condition: str  # "less_than", "greater_than", "equals"
    threshold_value: float
    severity: AlertSeverity = AlertSeverity.WARNING
    message_template: str = ""
    cooldown_seconds: float = 300.0

    def is_triggered(self, value: Any) -> bool:
        """Check if threshold is triggered."""
        if value is None:
            return False

        try:
            if self.condition == "less_than":
                return float(value) < self.threshold_value
            elif self.condition == "greater_than":
                return float(value) > self.threshold_value
            elif self.condition == "equals":
                return value == self.threshold_value
            else:
                return False
        except (TypeError, ValueError):
            return False


class AlertManager:
    """
    Manages alerts based on sensor values.

    Default thresholds:
    - Water pressure: < 0.8 bar (warning), < 0.5 bar (critical)
    - Water pressure: > 2.5 bar (warning), > 3.0 bar (critical)
    - Return temperature: > 55°C (info - not condensing)
    - Return temperature: > 65°C (warning)
    - Delta T: > 20°C (warning - system might be undersized)
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._thresholds: List[AlertThreshold] = []
        self._active_alerts: Dict[str, Alert] = {}
        self._alert_history: List[Alert] = []
        self._last_alert_time: Dict[str, datetime] = {}
        self._callbacks: List[Callable[[Alert], None]] = []

        self._setup_default_thresholds()

    def _setup_default_thresholds(self) -> None:
        """Setup default alert thresholds."""

        # Low pressure alerts
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.LOW_PRESSURE,
            sensor_name="boiler.water_pressure",
            condition="less_than",
            threshold_value=0.8,
            severity=AlertSeverity.WARNING,
            message_template="Low water pressure: {value:.1f} bar (min: 0.8 bar)",
            cooldown_seconds=600,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.LOW_PRESSURE,
            sensor_name="boiler.water_pressure",
            condition="less_than",
            threshold_value=0.5,
            severity=AlertSeverity.CRITICAL,
            message_template="CRITICAL: Very low pressure {value:.1f} bar!",
            cooldown_seconds=300,
        ))

        # High pressure alerts
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_PRESSURE,
            sensor_name="boiler.water_pressure",
            condition="greater_than",
            threshold_value=2.5,
            severity=AlertSeverity.WARNING,
            message_template="High water pressure: {value:.1f} bar",
            cooldown_seconds=600,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_PRESSURE,
            sensor_name="boiler.water_pressure",
            condition="greater_than",
            threshold_value=3.0,
            severity=AlertSeverity.CRITICAL,
            message_template="CRITICAL: Pressure {value:.1f} bar - check expansion vessel!",
            cooldown_seconds=300,
        ))

        # Return temperature - condensing efficiency
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.NO_CONDENSING,
            sensor_name="boiler.return_temperature",
            condition="greater_than",
            threshold_value=55.0,
            severity=AlertSeverity.INFO,
            message_template="Return temp {value:.1f}°C - boiler not condensing",
            cooldown_seconds=3600,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_RETURN_TEMP,
            sensor_name="boiler.return_temperature",
            condition="greater_than",
            threshold_value=70.0,
            severity=AlertSeverity.WARNING,
            message_template="High return temperature: {value:.1f}°C",
            cooldown_seconds=900,
        ))

        # High Delta T
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_DELTA_T,
            sensor_name="boiler.delta_t",
            condition="greater_than",
            threshold_value=20.0,
            severity=AlertSeverity.WARNING,
            message_template="High ΔT: {value:.1f}°C - check pump speed",
            cooldown_seconds=1800,
        ))

    def add_threshold(self, threshold: AlertThreshold) -> None:
        """Add an alert threshold."""
        self._thresholds.append(threshold)

    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register callback for new alerts."""
        self._callbacks.append(callback)

    def check_sensors(self, sensors: Dict[str, Dict]) -> List[Alert]:
        """Check all sensors against thresholds."""
        new_alerts = []
        now = datetime.now()

        for threshold in self._thresholds:
            sensor_key = threshold.sensor_name

            if sensor_key not in sensors:
                continue

            sensor_data = sensors[sensor_key]
            value = sensor_data.get("value")

            if value is None or isinstance(value, bool):
                continue

            if not threshold.is_triggered(value):
                continue

            # Check cooldown
            alert_key = f"{threshold.alert_type.value}_{threshold.severity.value}"
            last_time = self._last_alert_time.get(alert_key)

            if last_time and (now - last_time).total_seconds() < threshold.cooldown_seconds:
                continue

            # Create alert
            message = threshold.message_template.format(value=value)

            alert = Alert(
                alert_type=threshold.alert_type,
                severity=threshold.severity,
                message=message,
                value=value,
                threshold=threshold.threshold_value,
                timestamp=now,
            )

            new_alerts.append(alert)
            self._active_alerts[alert_key] = alert
            self._alert_history.append(alert)
            self._last_alert_time[alert_key] = now

            for callback in self._callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    self.logger.error(f"Alert callback error: {e}")

        return new_alerts

    def check_sensor_staleness(self, sensors: Dict[str, Dict], max_age: float = 300.0) -> List[Alert]:
        """Check if critical sensors have gone stale."""
        critical_sensors = [
            "boiler.flow_temperature",
            "boiler.water_pressure",
        ]

        new_alerts = []
        now = datetime.now()

        for sensor_name in critical_sensors:
            if sensor_name not in sensors:
                continue

            age = sensors[sensor_name].get("age_seconds", 0)

            if age > max_age:
                alert_key = f"stale_{sensor_name}"
                last_time = self._last_alert_time.get(alert_key)

                if last_time and (now - last_time).total_seconds() < 600:
                    continue

                alert = Alert(
                    alert_type=AlertType.SENSOR_STALE,
                    severity=AlertSeverity.WARNING,
                    message=f"Sensor {sensor_name} data is stale ({age:.0f}s old)",
                    value=age,
                    threshold=max_age,
                    timestamp=now,
                )

                new_alerts.append(alert)
                self._alert_history.append(alert)
                self._last_alert_time[alert_key] = now

                for callback in self._callbacks:
                    try:
                        callback(alert)
                    except Exception as e:
                        self.logger.error(f"Alert callback error: {e}")

        return new_alerts

    def report_fault(self, fault_code: int, fault_message: str) -> Alert:
        """Report an actual fault code from the boiler."""
        now = datetime.now()

        alert = Alert(
            alert_type=AlertType.FAULT_CODE,
            severity=AlertSeverity.CRITICAL,
            message=f"BOILER FAULT: {fault_message} (code: {fault_code})",
            value=fault_code,
            timestamp=now,
        )

        self._active_alerts["fault_code"] = alert
        self._alert_history.append(alert)

        for callback in self._callbacks:
            try:
                callback(alert)
            except Exception as e:
                self.logger.error(f"Alert callback error: {e}")

        return alert

    def clear_fault(self) -> None:
        """Clear fault code alert."""
        if "fault_code" in self._active_alerts:
            del self._active_alerts["fault_code"]

    def get_active_alerts(self) -> List[Alert]:
        """Get list of currently active alerts."""
        return list(self._active_alerts.values())

    def get_alert_history(self, max_count: int = 100) -> List[Alert]:
        """Get recent alert history."""
        return self._alert_history[-max_count:]

    def clear_all(self) -> None:
        """Clear all active alerts."""
        self._active_alerts.clear()

    def print_status(self) -> None:
        """Print current alert status."""
        active = self.get_active_alerts()

        if not active:
            print("\n✅ No active alerts")
            return

        print("\n" + "=" * 50)
        print("🚨 ACTIVE ALERTS")
        print("=" * 50)

        for alert in sorted(active, key=lambda a: a.severity.value, reverse=True):
            print(f"\n{alert}")
            print(f"   Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

        print("\n" + "=" * 50)