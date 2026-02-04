"""eBus serial connection handler."""

import serial
import logging
import time
from dataclasses import dataclass
from typing import Optional, Generator, Callable, List

from .telegram import TelegramParser, EbusTelegram


@dataclass
class ConnectionConfig:
    """Connection configuration."""
    port: str = "/dev/ttyAMA0"
    baudrate: int = 2400
    timeout: float = 0.1
    reconnect_delay: float = 5.0


class SerialConnection:
    """Serial connection to eBus adapter."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

        self._serial: Optional[serial.Serial] = None
        self._parser = TelegramParser()
        self._connected = False

        self._telegram_callbacks: List[Callable[[EbusTelegram], None]] = []
        self._raw_callbacks: List[Callable[[bytes], None]] = []

    @property
    def connected(self) -> bool:
        return self._connected and self._serial and self._serial.is_open

    def connect(self) -> bool:
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
            self.logger.info(f"Connected to {self.config.port}")
            return True
        except serial.SerialException as e:
            self.logger.error(f"Connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        self.logger.info("Disconnected")

    def register_telegram_callback(self, callback: Callable[[EbusTelegram], None]) -> None:
        self._telegram_callbacks.append(callback)

    def register_raw_callback(self, callback: Callable[[bytes], None]) -> None:
        self._raw_callbacks.append(callback)

    def read_raw(self) -> Optional[bytes]:
        if not self.connected:
            return None

        try:
            if self._serial.in_waiting > 0:
                return self._serial.read(self._serial.in_waiting)
            return None
        except serial.SerialException as e:
            self.logger.error(f"Read error: {e}")
            self._connected = False
            return None

    def read_telegrams(self) -> List[EbusTelegram]:
        raw = self.read_raw()
        if raw:
            for callback in self._raw_callbacks:
                try:
                    callback(raw)
                except Exception as e:
                    self.logger.error(f"Raw callback error: {e}")

            telegrams = self._parser.feed(raw)

            for telegram in telegrams:
                for callback in self._telegram_callbacks:
                    try:
                        callback(telegram)
                    except Exception as e:
                        self.logger.error(f"Telegram callback error: {e}")

            return telegrams

        return []

    def telegram_generator(self) -> Generator[EbusTelegram, None, None]:
        while self.connected:
            telegrams = self.read_telegrams()
            for telegram in telegrams:
                yield telegram

            if not telegrams:
                time.sleep(0.01)


def create_connection(config: ConnectionConfig) -> SerialConnection:
    return SerialConnection(config)