"""Thelia Condens message parser with improved sensor extraction."""

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

        # Decode master/query data
        for field_def in msg_def.fields:
            value = field_def.decode(telegram.data)
            if value is not None:
                query_values[field_def.name] = value
                if field_def.unit:
                    units[field_def.name] = field_def.unit

        # Decode slave/response data
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
    """Aggregates parsed messages into sensor values."""

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self._raw_messages: Dict[str, ParsedMessage] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        """Update sensors from parsed message."""
        if message.name == "unknown":
            return

        # Store raw message
        self._raw_messages[message.name] = message

        # Extract sensor values based on message type
        self._extract_sensors(message)

    def _extract_sensors(self, msg: ParsedMessage) -> None:
        """Extract sensor values from message."""
        ts = msg.timestamp

        # status_temps (B511) - multiple query types
        if msg.name == "status_temps":
            query_type = msg.query_data.get("query_type", -1)

            if query_type == 1:
                # Flow temperature (actual)
                if "temp1" in msg.response_data:
                    self._set_sensor("flow_temperature", msg.response_data["temp1"], "°C", ts)
                if "status_byte" in msg.response_data:
                    self._set_sensor("status_byte", msg.response_data["status_byte"], "", ts)

            elif query_type == 2:
                # Outdoor/secondary temperature
                if "temp1" in msg.response_data:
                    self._set_sensor("outdoor_temperature", msg.response_data["temp1"], "°C", ts)

            elif query_type == 0:
                # Extended status - might contain DHW temp
                if "temp1" in msg.response_data:
                    val = msg.response_data["temp1"]
                    # Only store if reasonable (not the weird 2.1°C we saw)
                    if val > 10:
                        self._set_sensor("dhw_temperature", val, "°C", ts)

        # temp_setpoint (B510) - flow setpoint
        elif msg.name == "temp_setpoint":
            if "flow_setpoint" in msg.query_data:
                self._set_sensor("flow_setpoint", msg.query_data["flow_setpoint"], "°C", ts)

        # modulation (B504)
        elif msg.name == "modulation":
            if "modulation" in msg.response_data:
                self._set_sensor("burner_modulation", msg.response_data["modulation"], "%", ts)

        # room_temp (B509)
        elif msg.name == "room_temp":
            if "room_temp" in msg.query_data:
                self._set_sensor("room_temperature", msg.query_data["room_temp"], "°C", ts)

        # datetime (B516)
        elif msg.name == "datetime":
            if all(k in msg.query_data for k in ["hours", "minutes", "seconds"]):
                h = msg.query_data["hours"]
                m = msg.query_data["minutes"]
                s = msg.query_data["seconds"]
                self._set_sensor("boiler_time", f"{h:02d}:{m:02d}:{s:02d}", "", ts)

            if all(k in msg.query_data for k in ["day", "month", "year"]):
                d = msg.query_data["day"]
                mo = msg.query_data["month"]
                y = msg.query_data["year"]
                year_full = 2000 + y if y < 100 else y
                self._set_sensor("boiler_date", f"{year_full}-{mo:02d}-{d:02d}", "", ts)

    def _set_sensor(self, name: str, value: Any, unit: str, timestamp: datetime) -> None:
        """Set a sensor value."""
        self._sensors[name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp,
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
                }

        return result

    def print_status(self) -> None:
        """Print current sensor status."""
        sensors = self.get_all_sensors()

        print("\n" + "=" * 50)
        print("📊 CURRENT SENSOR VALUES")
        print("=" * 50)

        # Group by category
        temps = {k: v for k, v in sensors.items() if "temp" in k.lower()}
        other = {k: v for k, v in sensors.items() if "temp" not in k.lower()}

        if temps:
            print("\n🌡️  Temperatures:")
            for name, data in sorted(temps.items()):
                print(f"   {name}: {data['value']}{data['unit']}")

        if other:
            print("\n📈 Other:")
            for name, data in sorted(other.items()):
                print(f"   {name}: {data['value']}{data['unit']}")

        print("=" * 50)