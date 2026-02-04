"""
Thelia Condens eBus message parser.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram
from .messages import MessageDefinition, get_message_definition, THELIA_MESSAGES


@dataclass
class ParsedMessage:
    """Represents a parsed eBus message with decoded values."""
    name: str
    timestamp: datetime
    source: int
    destination: int
    command: tuple
    values: Dict[str, Any] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)
    raw_telegram: Optional[EbusTelegram] = None
    valid: bool = True
    error: Optional[str] = None

    def __repr__(self) -> str:
        values_str = ", ".join(
            f"{k}={v}{self.units.get(k, '')}"
            for k, v in self.values.items()
        )
        return f"ParsedMessage({self.name}: {values_str})"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "source": f"0x{self.source:02X}",
            "destination": f"0x{self.destination:02X}",
            "command": f"0x{self.command[0]:02X}{self.command[1]:02X}",
            "values": self.values,
            "units": self.units,
            "valid": self.valid,
            "error": self.error
        }

    def get_value(self, field_name: str) -> Any:
        """Get a specific field value."""
        return self.values.get(field_name)


class TheliaParser:
    """
    Parser for Thelia Condens eBus messages.

    Decodes raw eBus telegrams into meaningful sensor values.
    """

    def __init__(self, custom_messages: Dict[tuple, MessageDefinition] = None):
        """
        Initialize parser.

        Args:
            custom_messages: Optional custom message definitions
        """
        self.logger = logging.getLogger(self.__class__.__name__)

        # Merge custom messages with defaults
        self.messages = dict(THELIA_MESSAGES)
        if custom_messages:
            self.messages.update(custom_messages)

        # Callbacks
        self._callbacks: List[Callable[[ParsedMessage], None]] = []

        # Statistics
        self.stats = {
            "total": 0,
            "parsed": 0,
            "errors": 0,
            "unknown": 0
        }

    def register_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        """Register callback for parsed messages."""
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        """Unregister a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self, message: ParsedMessage) -> None:
        """Notify all callbacks."""
        for callback in self._callbacks:
            try:
                callback(message)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    def parse(self, telegram: EbusTelegram) -> ParsedMessage:
        """
        Parse an EbusTelegram into a ParsedMessage.

        Args:
            telegram: Parsed eBus telegram

        Returns:
            ParsedMessage with decoded values
        """
        self.stats["total"] += 1

        ts = datetime.fromtimestamp(telegram.timestamp)

        # Check telegram validity
        if not telegram.valid:
            self.stats["errors"] += 1
            return ParsedMessage(
                name="invalid",
                timestamp=ts,
                source=telegram.source,
                destination=telegram.destination,
                command=telegram.command,
                raw_telegram=telegram,
                valid=False,
                error="Invalid telegram (CRC error)"
            )

        # Look up message definition
        msg_def = self.messages.get(telegram.command)

        if not msg_def:
            self.stats["unknown"] += 1
            self.logger.debug(
                f"Unknown: cmd={telegram.command_hex} data={telegram.data.hex()}"
            )
            msg = ParsedMessage(
                name="unknown",
                timestamp=ts,
                source=telegram.source,
                destination=telegram.destination,
                command=telegram.command,
                raw_telegram=telegram,
                values={"raw_data": telegram.data.hex()},
                valid=True
            )
            self._notify(msg)
            return msg

        # Decode fields
        values = {}
        units = {}

        for field_def in msg_def.fields:
            try:
                value = field_def.decode(telegram.data)
                if value is not None:
                    values[field_def.name] = value
                    if field_def.unit:
                        units[field_def.name] = field_def.unit
            except Exception as e:
                self.logger.warning(f"Error decoding {field_def.name}: {e}")

        self.stats["parsed"] += 1

        msg = ParsedMessage(
            name=msg_def.name,
            timestamp=ts,
            source=telegram.source,
            destination=telegram.destination,
            command=telegram.command,
            values=values,
            units=units,
            raw_telegram=telegram,
            valid=True
        )

        self._notify(msg)
        return msg

    def get_stats(self) -> Dict[str, int]:
        """Get parsing statistics."""
        return dict(self.stats)

    def reset_stats(self) -> None:
        """Reset statistics."""
        for key in self.stats:
            self.stats[key] = 0


class MessageAggregator:
    """
    Aggregates parsed messages and maintains current state.

    Provides easy access to the latest values of all sensors.
    """

    def __init__(self, max_age_seconds: float = 300.0):
        """
        Initialize aggregator.

        Args:
            max_age_seconds: Max age before values are stale
        """
        self.max_age = max_age_seconds
        self._messages: Dict[str, ParsedMessage] = {}
        self._values: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        """Update state with new message."""
        if not message.valid or message.name in ("unknown", "invalid"):
            return

        self._messages[message.name] = message

        # Update flat value cache
        for key, value in message.values.items():
            cache_key = f"{message.name}.{key}"
            self._values[cache_key] = {
                "value": value,
                "unit": message.units.get(key, ""),
                "timestamp": message.timestamp
            }

    def get(self, message_name: str, field_name: str = None) -> Any:
        """
        Get value(s) for a message.

        Args:
            message_name: Message name (e.g., "flow_return_temp")
            field_name: Optional specific field

        Returns:
            Field value, dict of values, or None if stale/missing
        """
        if message_name not in self._messages:
            return None

        msg = self._messages[message_name]
        age = (datetime.now() - msg.timestamp).total_seconds()

        if age > self.max_age:
            return None

        if field_name:
            return msg.values.get(field_name)
        return msg.values

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get all current values as nested dict."""
        result = {}
        now = datetime.now()

        for name, msg in self._messages.items():
            age = (now - msg.timestamp).total_seconds()
            if age <= self.max_age:
                result[name] = {
                    "values": msg.values,
                    "units": msg.units,
                    "age": round(age, 1),
                    "timestamp": msg.timestamp.isoformat()
                }

        return result

    def get_flat(self) -> Dict[str, Any]:
        """Get all values as flat dict with dotted keys."""
        result = {}
        now = datetime.now()

        for key, data in self._values.items():
            age = (now - data["timestamp"]).total_seconds()
            if age <= self.max_age:
                result[key] = data["value"]

        return result