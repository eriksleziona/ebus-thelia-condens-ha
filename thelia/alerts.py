"""
Alert system for Thelia Condens boiler.
Monitors sensor values and triggers alerts.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    FLAME_FAILURE = "flame_failure"
    PUMP_FAILURE = "pump_failure"
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
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    severity: AlertSeverity = AlertSeverity.WARNING
    message_template: str = ""
    cooldown_seconds: float = 300.0  # Don't repeat same alert within this time


class AlertManager:
    """
    Manages alerts based on sensor values.

    Default thresholds:
    - Water pressure: < 0.8 bar (warning), < 0.5 bar (critical)
    - Water pressure: > 2.5 bar (warning), > 3.0 bar (critical)
    - Return temperature: > 55°C (warning - no condensing)
    - Return temperature: > 65°C (critical)
    """

    # Known fault codes for Saunier Duval / Vaillant
    FAULT_CODES = {
        0: "No fault",
        1: "Ignition failure",
        2: "Flame loss during operation",
        3: "Overheating",
        4: "Low water pressure",
        5: "Flue sensor fault",
        6: "Fan fault",
        10: "Flow sensor fault",
        11: "Return sensor fault",
        12: "Outdoor sensor fault",
        20: "Communication error",
        28: "Flue temperature too high",
        29: "Return temperature too high",
        # Add more as discovered
    }

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._thresholds: List[AlertThreshold] = []
        self._active_alerts: Dict[str, Alert] = {}
        self._alert_history: List[Alert] = []
        self._last_alert_time: Dict[str, datetime] = {}
        self._callbacks: List[Callable[[Alert], None]] = []

        # Setup default thresholds
        self._setup_default_thresholds()

    def _setup_default_thresholds(self) -> None:
        """Setup default alert thresholds."""

        # Pressure alerts
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.LOW_PRESSURE,
            sensor_name="boiler.water_pressure",
            max_value=0.8,
            severity=AlertSeverity.WARNING,
            message_template="Low water pressure: {value} bar (threshold: {threshold} bar)",
            cooldown_seconds=600,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.LOW_PRESSURE,
            sensor_name="boiler.water_pressure",
            max_value=0.5,
            severity=AlertSeverity.CRITICAL,
            message_template="CRITICAL: Very low water pressure: {value} bar",
            cooldown_seconds=300,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_PRESSURE,
            sensor_name="boiler.water_pressure",
            min_value=2.5,
            severity=AlertSeverity.WARNING,
            message_template="High water pressure: {value} bar (threshold: {threshold} bar)",
            cooldown_seconds=600,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_PRESSURE,
            sensor_name="boiler.water_pressure",
            min_value=3.0,
            severity=AlertSeverity.CRITICAL,
            message_template="CRITICAL: Very high water pressure: {value} bar - check expansion vessel!",
            cooldown_seconds=300,
        ))

        # Return temperature alerts (condensing efficiency)
        self.add_threshold(AlertThreshold(
            alert_type=AlertType.NO_CONDENSING,
            sensor_name="boiler.return_temperature",
            min_value=55.0,
            severity=AlertSeverity.INFO,
            message_template="Return temp {value}°C - boiler not condensing (efficiency reduced)",
            cooldown_seconds=1800,  # Only every 30 min
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_RETURN_TEMP,
            sensor_name="boiler.return_temperature",
            min_value=65.0,
            severity=AlertSeverity.WARNING,
            message_template="High return temperature: {value}°C - check system balance",
            cooldown_seconds=900,
        ))

        self.add_threshold(AlertThreshold(
            alert_type=AlertType.HIGH_RETURN_TEMP,
            sensor_name="boiler.return_temperature",
            min_value=75.0,
            severity=AlertSeverity.CRITICAL,
            message_template="CRITICAL: Very high return temperature: {value}°C",
            cooldown_seconds=300,
        ))

    def add_threshold(self, threshold: AlertThreshold) -> None:
        """Add an alert threshold."""
        self._thresholds.append(threshold)

    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register callback for new alerts."""
        self._callbacks.append(callback)

    def check_sensors(self, sensors: Dict[str, Dict]) -> List[Alert]:
        """
        Check all sensors against thresholds.

        Args:
            sensors: Dict from DataAggregator.get_all_sensors()

        Returns:
            List of new alerts triggered
        """
        new_alerts = []
        now = datetime.now()

        for threshold in self._thresholds:
            sensor_key = threshold.sensor_name

            if sensor_key not in sensors:
                continue

            sensor_data = sensors[sensor_key]
            value = sensor_data.get("value")

            if value is None:
                continue

            # Check if threshold is violated
            triggered = False
            trigger_threshold = None

            if threshold.min_value is not None and value >= threshold.min_value:
                triggered = True
                trigger_threshold = threshold.min_value

            if threshold.max_value is not None and value <= threshold.max_value:
                triggered = True
                trigger_threshold = threshold.max_value

            if triggered:
                # Check cooldown
                alert_key = f"{threshold.alert_type.value}_{threshold.severity.value}"
                last_time = self._last_alert_time.get(alert_key)

                if last_time and (now - last_time).total_seconds() < threshold.cooldown_seconds:
                    continue  # Still in cooldown

                # Create alert
                message = threshold.message_template.format(
                    value=value,
                    threshold=trigger_threshold
                )

                alert = Alert(
                    alert_type=threshold.alert_type,
                    severity=threshold.severity,
                    message=message,
                    value=value,
                    threshold=trigger_threshold,
                    timestamp=now,
                )

                new_alerts.append(alert)
                self._active_alerts[alert_key] = alert
                self._alert_history.append(alert)
                self._last_alert_time[alert_key] = now

                # Notify callbacks
                for callback in self._callbacks:
                    try:
                        callback(alert)
                    except Exception as e:
                        self.logger.error(f"Alert callback error: {e}")

        return new_alerts

    def check_fault_code(self, status_code: int) -> Optional[Alert]:
        """
        Check for fault codes in status byte.

        Args:
            status_code: Raw status code from boiler

        Returns:
            Alert if fault detected, None otherwise
        """
        # Extract fault code (implementation depends on your boiler)
        # This is a simplified example
        fault_code = status_code & 0x0F  # Lower nibble might be fault

        if fault_code == 0:
            # Clear any existing fault alert
            if "fault_code" in self._active_alerts:
                del self._active_alerts["fault_code"]
            return None

        # Check cooldown
        now = datetime.now()
        last_time = self._last_alert_time.get("fault_code")
        if last_time and (now - last_time).total_seconds() < 300:
            return None

        fault_message = self.FAULT_CODES.get(fault_code, f"Unknown fault code: {fault_code}")

        alert = Alert(
            alert_type=AlertType.FAULT_CODE,
            severity=AlertSeverity.CRITICAL,
            message=f"BOILER FAULT: {fault_message} (code: {fault_code})",
            value=fault_code,
            timestamp=now,
        )

        self._active_alerts["fault_code"] = alert
        self._alert_history.append(alert)
        self._last_alert_time["fault_code"] = now

        for callback in self._callbacks:
            try:
                callback(alert)
            except Exception as e:
                self.logger.error(f"Alert callback error: {e}")

        return alert

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
                self._last_alert_time[alert_key] = now

                for callback in self._callbacks:
                    try:
                        callback(alert)
                    except Exception as e:
                        self.logger.error(f"Alert callback error: {e}")

        return new_alerts

    def get_active_alerts(self) -> List[Alert]:
        """Get list of currently active alerts."""
        return list(self._active_alerts.values())

    def get_alert_history(self, max_count: int = 100) -> List[Alert]:
        """Get recent alert history."""
        return self._alert_history[-max_count:]

    def acknowledge_alert(self, alert_key: str) -> bool:
        """Acknowledge an alert."""
        if alert_key in self._active_alerts:
            self._active_alerts[alert_key].acknowledged = True
            return True
        return False

    def clear_alert(self, alert_key: str) -> bool:
        """Clear an active alert."""
        if alert_key in self._active_alerts:
            del self._active_alerts[alert_key]
            return True
        return False

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
            if alert.value is not None:
                print(f"   Value: {alert.value}")

        print("\n" + "=" * 50)