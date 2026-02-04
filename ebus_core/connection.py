"""
eBus serial connection handler.
Supports both direct serial and ebusd adapter connections.
"""

import serial
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import time


class ConnectionType(Enum):
    """Connection type enumeration."""
    SERIAL = "serial"
    EBUSD = "ebusd"
    TCP = "tcp"


@dataclass
class ConnectionConfig:
    """Connection configuration."""
    type: ConnectionType = ConnectionType.SERIAL
    port: str = "/dev/ttyUSB0"
    baudrate: int = 2400
    host: str = "localhost"
    tcp_port: int = 8888
    timeout: float = 1.0
    reconnect_delay: float = 5.0


class EbusConnection(ABC):
    """Abstract base class for eBus connections."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._connected = False
        self._callbacks: list[Callable[[bytes], None]] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    async def read_telegram(self) -> Optional[bytes]:
        """Read a single telegram from the bus."""
        pass

    @abstractmethod
    async def write(self, data: bytes) -> bool:
        """Write data to the bus."""
        pass

    def register_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for incoming data."""
        self._callbacks.append(callback)

    async def read_loop(self) -> AsyncIterator[bytes]:
        """Async generator yielding telegrams."""
        while self._connected:
            telegram = await self.read_telegram()
            if telegram:
                yield telegram


class SerialConnection(EbusConnection):
    """Direct serial connection to eBus adapter."""

    SYNC_BYTE = 0xAA

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        self._serial: Optional[serial.Serial] = None
        self._buffer = bytearray()
        self._read_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Open serial port connection."""
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
            self.logger.info(f"Connected to {self.config.port}")
            return True
        except serial.SerialException as e:
            self.logger.error(f"Failed to connect: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close serial connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        self.logger.info("Disconnected")

    async def read_telegram(self) -> Optional[bytes]:
        """
        Read a complete telegram from the serial port.
        Telegrams are delimited by SYNC bytes (0xAA).
        """
        if not self._serial or not self._serial.is_open:
            return None

        async with self._read_lock:
            try:
                # Read available bytes
                loop = asyncio.get_event_loop()

                # Use run_in_executor for blocking serial read
                available = await loop.run_in_executor(
                    None, lambda: self._serial.in_waiting
                )

                if available > 0:
                    data = await loop.run_in_executor(
                        None, lambda: self._serial.read(available)
                    )
                    self._buffer.extend(data)

                # Look for complete telegram between SYNC bytes
                return self._extract_telegram()

            except serial.SerialException as e:
                self.logger.error(f"Read error: {e}")
                self._connected = False
                return None

    def _extract_telegram(self) -> Optional[bytes]:
        """Extract a complete telegram from the buffer."""
        # Find SYNC bytes
        while len(self._buffer) > 0 and self._buffer[0] == self.SYNC_BYTE:
            self._buffer.pop(0)

        # Look for next SYNC byte (end of telegram)
        try:
            sync_pos = self._buffer.index(self.SYNC_BYTE)
            if sync_pos > 0:
                telegram = bytes(self._buffer[:sync_pos])
                self._buffer = self._buffer[sync_pos:]
                return telegram
        except ValueError:
            pass  # No SYNC found yet

        # Prevent buffer overflow
        if len(self._buffer) > 1024:
            self._buffer = self._buffer[-512:]

        return None

    async def write(self, data: bytes) -> bool:
        """Write data to serial port."""
        if not self._serial or not self._serial.is_open:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: self._serial.write(data)
            )
            return True
        except serial.SerialException as e:
            self.logger.error(f"Write error: {e}")
            return False


class EbusdConnection(EbusConnection):
    """Connection through ebusd daemon."""

    def __init__(self, config: ConnectionConfig):
        super().__init__(config)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> bool:
        """Connect to ebusd TCP port."""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.config.host,
                self.config.tcp_port
            )
            self._connected = True
            self.logger.info(f"Connected to ebusd at {self.config.host}:{self.config.tcp_port}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to ebusd: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from ebusd."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._connected = False

    async def read_telegram(self) -> Optional[bytes]:
        """Read from ebusd (line-based protocol)."""
        if not self._reader:
            return None

        try:
            line = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self.config.timeout
            )
            return line.strip() if line else None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self.logger.error(f"Read error: {e}")
            self._connected = False
            return None

    async def write(self, data: bytes) -> bool:
        """Send command to ebusd."""
        if not self._writer:
            return False

        try:
            self._writer.write(data + b'\n')
            await self._writer.drain()
            return True
        except Exception as e:
            self.logger.error(f"Write error: {e}")
            return False

    async def send_command(self, command: str) -> Optional[str]:
        """Send a command to ebusd and get response."""
        if await self.write(command.encode()):
            response = await self.read_telegram()
            if response:
                return response.decode('utf-8', errors='ignore')
        return None


def create_connection(config: ConnectionConfig) -> EbusConnection:
    """Factory function to create appropriate connection type."""
    if config.type == ConnectionType.SERIAL:
        return SerialConnection(config)
    elif config.type == ConnectionType.EBUSD:
        return EbusdConnection(config)
    else:
        raise ValueError(f"Unknown connection type: {config.type}")