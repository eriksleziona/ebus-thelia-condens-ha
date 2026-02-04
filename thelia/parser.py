"""
Thelia Condens eBus message parser.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram, TelegramParser
from .messages import (
    MessageDefinition,
    get_message_definition,
    THELIA_MESSAGES,
    FieldDefinition
)


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


class TheliaParser:
    """
    Parser for Thelia Condens eBus messages.

    Decodes raw eBus telegrams into meaningful sensor values.
    """

    def __init__(self, custom_messages: Dict[tuple, MessageDefinition] = None):
        """
        Initialize parser.

        Args:
            custom_messages: Optional custom message definitions to merge
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.telegram_parser = TelegramParser()

        # Merge custom messages with defaults
        self.messages = dict(THELIA_MESSAGES)
        if custom_messages:
            self.messages.update(custom_messages)

        # Callbacks for parsed messages
        self._callbacks: List[Callable[[ParsedMessage], None]] = []

        # Statistics
        self.stats = {
            "total_telegrams": 0,
            "parsed_ok": 0,
            "parse_errors": 0,
            "unknown_messages": 0
        }

    def register_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        """Register a callback for parsed messages."""
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        """Unregister a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(self, message: ParsedMessage) -> None:
        """Notify all registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(message)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    def parse_raw(self, raw_data: bytes, timestamp: float = None) -> Optional[ParsedMessage]:
        """
        Parse raw bytes into a ParsedMessage.

        Args:
            raw_data: Raw bytes from eBus
            timestamp: Optional timestamp (uses current time if not provided)

        Returns:
            ParsedMessage or None if parsing fails
        """
        if timestamp is None:
            timestamp = datetime.now().timestamp()

        self.stats["total_telegrams"] += 1

        # First parse the telegram structure
        telegram = self.telegram_parser.parse(raw_data, timestamp)
        if not telegram:
            self.stats["parse_errors"] += 1
            return None

        return self.parse_telegram(telegram)

    def parse_telegram(self, telegram: EbusTelegram) -> Optional[ParsedMessage]:
        """
        Parse an EbusTelegram into a ParsedMessage.

        Args:
            telegram: Parsed eBus telegram

        Returns:
            ParsedMessage with decoded values
        """
        if not telegram.valid:
            self.stats["parse_errors"] += 1
            return ParsedMessage(
                name="invalid",
                timestamp=datetime.fromtimestamp(telegram.timestamp),
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
            self.stats["unknown_messages"] += 1
            self.logger.debug(
                f"Unknown message: cmd={telegram.command_hex}, "
                f"data={telegram.data.hex()}"
            )
            return ParsedMessage(
                name="unknown",
                timestamp=datetime.fromtimestamp(telegram.timestamp),
                source=telegram.source,
                destination=telegram.destination,
                command=telegram.command,
                raw_telegram=telegram,
                values={"raw_data": telegram.data.hex()},
                valid=True
            )

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
                self.logger.warning(f"Error decoding field {field_def.name}: {e}")

        self.stats["parsed_ok"] += 1

        parsed = ParsedMessage(
            name=msg_def.name,
            timestamp=datetime.fromtimestamp(telegram.timestamp),
            source=telegram.source,
            destination=telegram.destination,
            command=telegram.command,
            values=values,
            units=units,
            raw_telegram=telegram,
            valid=True
        )

        # Notify callbacks
        self._notify_callbacks(parsed)

        return parsed

    def get_stats(self) -> Dict[str, int]:
        """Get parsing statistics."""
        return dict(self.stats)

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        for key in self.stats:
            self.stats[key] = 0


class MessageAggregator:
    """
    Aggregates parsed messages and maintains current state.

    Useful for getting the latest known values of all sensors.
    """

    def __init__(self, max_age_seconds: float = 300.0):
        """
        Initialize aggregator.

        Args:
            max_age_seconds: Maximum age for values to be considered valid
        """
        self.max_age = max_age_seconds
        self._state: Dict[str, ParsedMessage] = {}
        self._value_cache: Dict[str, Any] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        """Update state with new message."""
        if message.valid and message.name != "unknown":
            self._state[message.name] = message
            # Update individual value cache
            for key, value in message.values.items():
                cache_key = f"{message.name}.{key}"
                self._value_cache[cache_key] = {
                    "value": value,
                    "unit": message.units.get(key, ""),
                    "timestamp": message.timestamp
                }

    def get_value(self, message_name: str, field_name: str = None) -> Optional[Any]:
        """
        Get a specific value.

        Args:
            message_name: Name of the message (e.g., "flow_temp")
            field_name: Optional field name (returns dict if not specified)
        """
        if message_name not in self._state:
            return None

        message = self._state[message_name]

        # Check age
        age = (datetime.now() - message.timestamp).total_seconds()
        if age > self.max_age:
            return None

        if field_name:
            return message.values.get(field_name)
        else:
            return message.values

    def get_all_current_values(self) -> Dict[str, Dict[str, Any]]:
        """Get all current values as a nested dictionary."""
        result = {}
        now = datetime.now()

        for name, message in self._state.items():
            age = (now - message.timestamp).total_seconds()
            if age <= self.max_age:
                result[name] = {
                    "values": message.values,
                    "units": message.units,
                    "age_seconds": age,
                    "timestamp": message.timestamp.isoformat()
                }

        return result

    def get_flat_values(self) -> Dict[str, Any]:
        """Get all current values as a flat dictionary with dotted keys."""
        result = {}
        now = datetime.now()

        for cache_key, data in self._value_cache.items():
            age = (now - data["timestamp"]).total_seconds()
            if age <= self.max_age:
                result[cache_key] = data["value"]

        return result