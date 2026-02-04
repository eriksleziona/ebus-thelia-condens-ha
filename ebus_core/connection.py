"""
eBus serial connection handler for C6 adapter.
"""

import serial
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Generator, Callable, List
from threading import Thread, Event

from .telegram import TelegramParser, EbusTelegram


class ConnectionType(Enum):
    """Connection type enumeration."""
    SERIAL = "serial"


@dataclass
class ConnectionConfig:
    """Connection configuration."""
    type: ConnectionType = ConnectionType.SERIAL
    port: str = "/dev/ttyAMA0"
    baudrate: int = 2400
    timeout: float = 0.1
    reconnect_delay: float = 5.0


class SerialConnection:
    """
    Serial connection to eBus C6 adapter.

    Handles:
    - Connection management
    - Raw byte reading
    - Telegram extraction via parser
    - Reconnection on failure
    """

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        self._serial: Optional[serial.Serial] = None
        self._parser = TelegramParser()
        self._connected = False

        # Callbacks
        self._telegram_callbacks: List[Callable[[EbusTelegram], None]] = []
        self._raw_callbacks: List[Callable[[bytes], None]] = []

        # Background reading
        self._read_thread: Optional[Thread] = None
        self._stop_event = Event()

    @property
    def connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._serial and self._serial.is_open

    def connect(self) -> bool:
        """
        Open serial port connection.

        Returns:
            True if connection successful
        """
        try:
            self._serial = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.timeout
            )
            self._connected = True
            self._parser.reset()
            self.logger.info(f"Connected to {self.config.port} at {self.config.baudrate} baud")
            return True

        except serial.SerialException as e:
            self.logger.error(f"Failed to connect to {self.config.port}: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close serial connection."""
        self._stop_event.set()

        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=2.0)

        if self._serial and self._serial.is_open:
            self._serial.close()

        self._connected = False
        self.logger.info("Disconnected")

    def register_telegram_callback(self, callback: Callable[[EbusTelegram], None]) -> None:
        """Register callback for parsed telegrams."""
        self._telegram_callbacks.append(callback)

    def register_raw_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register callback for raw bytes."""
        self._raw_callbacks.append(callback)

    def read_raw(self) -> Optional[bytes]:
        """
        Read available raw bytes from serial port.

        Returns:
            Bytes read or None if error/no data
        """
        if not self.connected:
            return None

        try:
            if self._serial.in_waiting > 0:
                data = self._serial.read(self._serial.in_waiting)
                return data
            return None

        except serial.SerialException as e:
            self.logger.error(f"Read error: {e}")
            self._connected = False
            return None

    def read_telegrams(self) -> List[EbusTelegram]:
        """
        Read and parse available telegrams.

        Returns:
            List of parsed telegrams
        """
        raw = self.read_raw()
        if raw:
            # Notify raw callbacks
            for callback in self._raw_callbacks:
                try:
                    callback(raw)
                except Exception as e:
                    self.logger.error(f"Raw callback error: {e}")

            # Parse telegrams
            telegrams = self._parser.feed(raw)

            # Notify telegram callbacks
            for telegram in telegrams:
                for callback in self._telegram_callbacks:
                    try:
                        callback(telegram)
                    except Exception as e:
                        self.logger.error(f"Telegram callback error: {e}")

            return telegrams

        return []

    def telegram_generator(self) -> Generator[EbusTelegram, None, None]:
        """
        Generator that yields telegrams as they arrive.

        Yields:
            EbusTelegram objects
        """
        while self.connected:
            telegrams = self.read_telegrams()
            for telegram in telegrams:
                yield telegram

            if not telegrams:
                time.sleep(0.01)

    def start_reading(self) -> None:
        """Start background reading thread."""
        if self._read_thread and self._read_thread.is_alive():
            return

        self._stop_event.clear()
        self._read_thread = Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()
        self.logger.info("Started background reading")

    def stop_reading(self) -> None:
        """Stop background reading thread."""
        self._stop_event.set()
        if self._read_thread:
            self._read_thread.join(timeout=2.0)
        self.logger.info("Stopped background reading")

    def _read_loop(self) -> None:
        """Background read loop."""
        while not self._stop_event.is_set():
            if self.connected:
                self.read_telegrams()
            else:
                # Try to reconnect
                self.logger.info("Attempting reconnection...")
                if self.connect():
                    self.logger.info("Reconnected")
                else:
                    time.sleep(self.config.reconnect_delay)

            time.sleep(0.01)


class EbusConnection(SerialConnection):
    """Alias for SerialConnection."""
    pass


def create_connection(config: ConnectionConfig) -> SerialConnection:
    """
    Factory function to create connection.

    Args:
        config: Connection configuration

    Returns:
        Connection instance
    """
    return SerialConnection(config)