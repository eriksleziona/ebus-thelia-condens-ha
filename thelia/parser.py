"""
Thelia Condens message parser.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram
from .messages import MessageDefinition, get_message_definition, THELIA_MESSAGES


@dataclass
class ParsedMessage:
    """Parsed eBus message with decoded values."""
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
        for k, v in {**self.query_data, **self.response_data}.items():
            unit = self.units.get(k, "")
            if isinstance(v, float):
                parts.append(f"{k}={v:.1f}{unit}")
            else:
                parts.append(f"{k}={v}{unit}")
        return f"{self.name}: {', '.join(parts)}"

    def get(self, key: str, default=None) -> Any:
        """Get value by field name."""
        if key in self.response_data:
            return self.response_data[key]
        return self.query_data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
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
    """Parser for Thelia Condens messages."""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._callbacks: List[Callable[[ParsedMessage], None]] = []

        self.stats = {
            "total": 0,
            "parsed": 0,
            "unknown": 0,
        }

    def register_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        """Register callback for parsed messages."""
        self._callbacks.append(callback)

    def _notify(self, message: ParsedMessage) -> None:
        """Notify callbacks."""
        for cb in self._callbacks:
            try:
                cb(message)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    def parse(self, telegram: EbusTelegram) -> ParsedMessage:
        """Parse telegram into message."""
        self.stats["total"] += 1

        ts = datetime.fromtimestamp(telegram.timestamp)

        # Find message definition
        msg_def = self._find_definition(telegram)

        if not msg_def:
            self.stats["unknown"] += 1
            msg = ParsedMessage(
                name="unknown",
                timestamp=ts,
                source=telegram.source,
                destination=telegram.destination,
                command=telegram.command,
                query_data={"raw": telegram.data.hex()},
                response_data={"raw": telegram.response_data.hex() if telegram.response_data else ""},
                raw_telegram=telegram,
            )
            self._notify(msg)
            return msg

        # Decode fields
        query_values = {}
        response_values = {}
        units = {}

        # Decode master data
        for field_def in msg_def.fields:
            value = field_def.decode(telegram.data)
            if value is not None:
                query_values[field_def.name] = value
                if field_def.unit:
                    units[field_def.name] = field_def.unit

        # Decode slave response
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

    def _find_definition(self, telegram: EbusTelegram) -> Optional[MessageDefinition]:
        """Find matching message definition."""
        # First try exact match
        msg_def = get_message_definition(telegram.primary_command, telegram.secondary_command)

        if msg_def:
            # For B511, we have multiple sub-types - could add logic here
            return msg_def

        return None

    def get_stats(self) -> Dict[str, int]:
        return dict(self.stats)


class DataAggregator:
    """Aggregates sensor data from parsed messages."""

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._data: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        """Update with new message data."""
        if message.name == "unknown":
            return

        # Store message
        self._data[message.name] = {
            "query": message.query_data,
            "response": message.response_data,
            "units": message.units,
            "timestamp": message.timestamp,
        }

        # Extract specific sensor values
        self._extract_sensors(message)

    def _extract_sensors(self, msg: ParsedMessage) -> None:
        """Extract known sensor values."""
        # Flow temperature from temperatures_1
        if msg.name == "temperatures_1" and "flow_temp" in msg.response_data:
            self._data["sensor.flow_temp"] = {
                "value": msg.response_data["flow_temp"],
                "unit": "°C",
                "timestamp": msg.timestamp,
            }

        # Modulation
        if msg.name == "modulation" and "modulation" in msg.response_data:
            self._data["sensor.modulation"] = {
                "value": msg.response_data["modulation"],
                "unit": "%",
                "timestamp": msg.timestamp,
            }

        # DateTime
        if msg.name == "datetime":
            self._data["sensor.datetime"] = {
                "hours": msg.query_data.get("hours"),
                "minutes": msg.query_data.get("minutes"),
                "seconds": msg.query_data.get("seconds"),
                "day": msg.query_data.get("day"),
                "month": msg.query_data.get("month"),
                "year": msg.query_data.get("year"),
                "timestamp": msg.timestamp,
            }

    def get_sensor(self, name: str) -> Optional[Dict]:
        """Get sensor value by name."""
        if name not in self._data:
            return None

        data = self._data[name]
        age = (datetime.now() - data["timestamp"]).total_seconds()

        if age > self.max_age:
            return None

        return data

    def get_all_sensors(self) -> Dict[str, Any]:
        """Get all current sensor values."""
        result = {}
        now = datetime.now()

        for key, data in self._data.items():
            if key.startswith("sensor."):
                age = (now - data["timestamp"]).total_seconds()
                if age <= self.max_age:
                    result[key] = data

        return result