"""eBus Core - Protocol and connection handling."""

from .crc import EbusCRC
from .telegram import EbusTelegram, TelegramParser, TelegramType
from .connection import (
    ConnectionConfig,
    ConnectionType,
    EbusConnection,
    SerialConnection,
    create_connection
)

__all__ = [
    "EbusCRC",
    "EbusTelegram",
    "TelegramParser",
    "TelegramType",
    "ConnectionConfig",
    "ConnectionType",
    "EbusConnection",
    "SerialConnection",
    "create_connection",
]