"""Thelia Condens message parser with corrected sensor interpretation."""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram
from .messages import MessageDefinition, get_message_definition, THELIA_MESSAGES


@dataclass
class ParsedMessage:
    name: str
    timestamp: datetime
    source: int
    destination: int
    command: tuple
    query_data: Dict[str, Any] = field(default_factory=dict)
    response_data: Dict[str, Any] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)
    raw_telegram: Optional[EbusTelegram] = None

    def __repr__(self) -> str:
        parts = []
        all_data = {**self.query_data, **self.response_data}
        for k, v in all_data.items():
            unit = self.units.get(k, "")
            if isinstance(v, float):
                parts.append(f"{k}={v:.1f}{unit}")
            elif isinstance(v, bool):
                parts.append(f"{k}={'ON' if v else 'OFF'}")
            else:
                parts.append(f"{k}={v}{unit}")
        return f"{self.name}: {', '.join(parts)}"

    def get(self, key: str, default=None) -> Any:
        if key in self.response_data:
            return self.response_data[key]
        return self.query_data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "source": f"0x{self.source:02X}",
            "destination": f"0x{self.destination:02X}",
            "command": f"{self.command[0]:02X}{self.command[1]:02X}",
            "query": self.query_data,
            "response": self.response_data,
            "units": self.units,
        }


class TheliaParser:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._callbacks: List[Callable[[ParsedMessage], None]] = []
        self.stats = {"total": 0, "parsed": 0, "unknown": 0}

    def register_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        self._callbacks.append(callback)

    def _notify(self, message: ParsedMessage) -> None:
        for cb in self._callbacks:
            try:
                cb(message)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    def parse(self, telegram: EbusTelegram) -> ParsedMessage:
        self.stats["total"] += 1
        ts = datetime.fromtimestamp(telegram.timestamp)

        msg_def = get_message_definition(telegram.primary_command, telegram.secondary_command)

        if not msg_def:
            self.stats["unknown"] += 1
            raw_resp = telegram.response_data.hex() if telegram.response_data else ""
            msg = ParsedMessage(
                name="unknown",
                timestamp=ts,
                source=telegram.source,
                destination=telegram.destination,
                command=telegram.command,
                query_data={"raw": telegram.data.hex()},
                response_data={"raw": raw_resp} if raw_resp else {},
                raw_telegram=telegram,
            )
            self._notify(msg)
            return msg

        query_values = {}
        response_values = {}
        units = {}

        for field_def in msg_def.fields:
            value = field_def.decode(telegram.data)
            if value is not None:
                query_values[field_def.name] = value
                if field_def.unit:
                    units[field_def.name] = field_def.unit

        if telegram.response_data and msg_def.response_fields:
            for field_def in msg_def.response_fields:
                value = field_def.decode(telegram.response_data)
                if value is not None:
                    response_values[field_def.name] = value
                    if field_def.unit:
                        units[field_def.name] = field_def.unit

        self.stats["parsed"] += 1

        msg = ParsedMessage(
            name=msg_def.name,
            timestamp=ts,
            source=telegram.source,
            destination=telegram.destination,
            command=telegram.command,
            query_data=query_values,
            response_data=response_values,
            units=units,
            raw_telegram=telegram,
        )

        self._notify(msg)
        return msg

    def get_stats(self) -> Dict[str, int]:
        return dict(self.stats)


class DataAggregator:
    """Aggregates parsed messages into meaningful sensor values."""

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

        # Message counters for filtering spam
        self._msg_counts: Dict[str, int] = {}

    def update(self, message: ParsedMessage) -> None:
        """Update sensors from parsed message."""
        if message.name == "unknown":
            return

        # Count messages
        self._msg_counts[message.name] = self._msg_counts.get(message.name, 0) + 1

        # Extract sensor values
        self._extract_sensors(message)

    def _extract_sensors(self, msg: ParsedMessage) -> None:
        """Extract sensor values based on message type and query_type."""
        ts = msg.timestamp

        # status_temps (B511) - different meaning based on query_type
        if msg.name == "status_temps":
            query_type = msg.query_data.get("query_type", -1)

            if query_type == 1:
                # Query type 1: FLOW TEMPERATURE (actual water temp leaving boiler)
                if "temp1" in msg.response_data:
                    self._set_sensor("flow_temperature", msg.response_data["temp1"], "°C", ts,
                                    "Actual flow water temperature")
                # Status byte indicates boiler state
                if "status_byte" in msg.response_data:
                    status = msg.response_data["status_byte"]
                    self._set_sensor("boiler_status_code", status, "", ts)
                    # Interpret status (these are guesses - need verification)
                    self._set_sensor("burner_active", (status & 0x01) != 0, "", ts)
                    self._set_sensor("pump_active", (status & 0x10) != 0, "", ts)

            elif query_type == 2:
                # Query type 2: SETPOINTS (outdoor cutoff threshold, NOT actual outdoor temp!)
                if "temp1" in msg.response_data:
                    self._set_sensor("outdoor_cutoff_setpoint", msg.response_data["temp1"], "°C", ts,
                                    "Heating disabled when outdoor temp exceeds this")

            elif query_type == 0:
                # Query type 0: EXTENDED STATUS (may contain actual outdoor temp!)
                if "temp1" in msg.response_data:
                    temp = msg.response_data["temp1"]
                    # Only if value is reasonable for outdoor temp (-40 to +50)
                    if -40 <= temp <= 50:
                        self._set_sensor("outdoor_temperature", temp, "°C", ts,
                                        "Actual outdoor temperature")
                # Try to extract DHW temperature from other bytes
                if "status_byte" in msg.response_data:
                    self._set_sensor("status_type0", msg.response_data["status_byte"], "", ts)

        # temp_setpoint (B510)
        elif msg.name == "temp_setpoint":
            mode1 = msg.query_data.get("mode1", 0)
            # Only extract main setpoint (mode1=0), not other variants
            if mode1 == 0 and "flow_setpoint" in msg.query_data:
                setpoint = msg.query_data["flow_setpoint"]
                if setpoint > 0:  # Filter out weird values
                    self._set_sensor("flow_setpoint", setpoint, "°C", ts,
                                    "Configured flow temperature setpoint")

        # modulation (B504)
        elif msg.name == "modulation":
            if "modulation" in msg.response_data:
                self._set_sensor("burner_modulation", msg.response_data["modulation"], "%", ts,
                                "Burner modulation level (0=off)")

        # room_temp (B509) - from thermostat
        elif msg.name == "room_temp":
            if "room_temp" in msg.query_data:
                self._set_sensor("room_temperature", msg.query_data["room_temp"], "°C", ts,
                                "Room temperature from thermostat")

        # datetime (B516)
        elif msg.name == "datetime":
            qd = msg.query_data
            flags = qd.get("flags", 0)

            # Only process full datetime (flags=0, all fields present)
            if flags == 0 and len(qd) >= 7:
                hours = qd.get("hours", 0)
                minutes = qd.get("minutes", 0)
                seconds = qd.get("seconds", 0)

                # Validate (minutes/seconds should be 0-59)
                if minutes < 60 and seconds < 60 and hours < 24:
                    self._set_sensor("boiler_time", f"{hours:02d}:{minutes:02d}:{seconds:02d}", "", ts)

                day = qd.get("day", 0)
                month = qd.get("month", 0)
                year = qd.get("year", 0)

                if 1 <= day <= 31 and 1 <= month <= 12:
                    year_full = 2000 + year if year < 100 else year
                    self._set_sensor("boiler_date", f"{year_full}-{month:02d}-{day:02d}", "", ts)

    def _set_sensor(self, name: str, value: Any, unit: str, timestamp: datetime, description: str = "") -> None:
        """Set a sensor value."""
        self._sensors[name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp,
            "description": description,
        }

    def get_sensor(self, name: str) -> Optional[Any]:
        """Get sensor value by name."""
        if name not in self._sensors:
            return None

        data = self._sensors[name]
        age = (datetime.now() - data["timestamp"]).total_seconds()

        if age > self.max_age:
            return None

        return data["value"]

    def get_all_sensors(self) -> Dict[str, Dict]:
        """Get all current sensor values."""
        result = {}
        now = datetime.now()

        for name, data in self._sensors.items():
            age = (now - data["timestamp"]).total_seconds()
            if age <= self.max_age:
                result[name] = {
                    "value": data["value"],
                    "unit": data["unit"],
                    "age_seconds": round(age, 1),
                    "description": data.get("description", ""),
                }

        return result

    def print_status(self) -> None:
        """Print current sensor status with descriptions."""
        sensors = self.get_all_sensors()

        print("\n" + "=" * 60)
        print("📊 CURRENT SENSOR VALUES")
        print("=" * 60)

        # Categorize sensors
        categories = {
            "🌡️  Temperatures": ["flow_temperature", "room_temperature", "outdoor_temperature",
                                 "flow_setpoint", "outdoor_cutoff_setpoint"],
            "🔥 Burner": ["burner_modulation", "burner_active", "pump_active", "boiler_status_code"],
            "🕐 Date/Time": ["boiler_time", "boiler_date"],
            "📈 Other": []
        }

        categorized = set()
        for cat_sensors in categories.values():
            categorized.update(cat_sensors)

        # Add uncategorized to "Other"
        for name in sensors.keys():
            if name not in categorized:
                categories["📈 Other"].append(name)

        for category, sensor_names in categories.items():
            cat_sensors = {k: sensors[k] for k in sensor_names if k in sensors}
            if cat_sensors:
                print(f"\n{category}:")
                for name, data in cat_sensors.items():
                    val = data["value"]
                    unit = data["unit"]
                    desc = data.get("description", "")

                    if isinstance(val, bool):
                        val_str = "✅ ON" if val else "❌ OFF"
                    elif isinstance(val, float):
                        val_str = f"{val:.1f}{unit}"
                    else:
                        val_str = f"{val}{unit}"

                    if desc:
                        print(f"   {name}: {val_str}")
                        print(f"      └─ {desc}")
                    else:
                        print(f"   {name}: {val_str}")

        print("\n" + "=" * 60)