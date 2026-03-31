"""eBus serial connection handler."""

import serial
import logging
import time
import threading
from dataclasses import dataclass
from typing import Optional, Generator, Callable, List

from .crc import EbusCRC
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
        self._io_lock = threading.RLock()
        self._last_raw_activity_monotonic: Optional[float] = None
        self._last_telegram_monotonic: Optional[float] = None

    @property
    def connected(self) -> bool:
        return bool(self._connected and self._serial and self._serial.is_open)

    def connect(self) -> bool:
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()

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
            now = time.monotonic()
            self._last_raw_activity_monotonic = now
            self._last_telegram_monotonic = now
            self.logger.info(f"Connected to {self.config.port}")
            return True
        except (serial.SerialException, OSError) as e:
            self.logger.error(f"Connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except (serial.SerialException, OSError) as e:
                self.logger.warning(f"Error while closing serial port: {e}")
        self._connected = False
        self._serial = None
        self._last_raw_activity_monotonic = None
        self._last_telegram_monotonic = None
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
                raw = self._serial.read(self._serial.in_waiting)
                if raw:
                    self._last_raw_activity_monotonic = time.monotonic()
                return raw
            return None
        except (serial.SerialException, OSError) as e:
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
            if telegrams:
                self._last_telegram_monotonic = time.monotonic()

            for telegram in telegrams:
                for callback in self._telegram_callbacks:
                    try:
                        callback(telegram)
                    except Exception as e:
                        self.logger.error(f"Telegram callback error: {e}")

            return telegrams

        return []

    @staticmethod
    def build_query_frame(
        source: int,
        destination: int,
        primary_command: int,
        secondary_command: int,
        data: bytes = b"",
    ) -> bytes:
        """Build a master query frame (without SYNC bytes)."""
        payload = bytes(data or b"")
        if len(payload) > 255:
            raise ValueError("eBUS payload too long (>255 bytes)")

        header = bytes(
            [
                source & 0xFF,
                destination & 0xFF,
                primary_command & 0xFF,
                secondary_command & 0xFF,
                len(payload) & 0xFF,
            ]
        ) + payload
        crc = EbusCRC.calculate(header)
        return header + bytes([crc])

    def send_query(
        self,
        source: int,
        destination: int,
        primary_command: int,
        secondary_command: int,
        data: bytes = b"",
        prepend_sync: bool = True,
        append_sync: bool = True,
        flush_input: bool = False,
    ) -> bool:
        """
        Send an eBUS query frame on the serial interface.
        Returns True when write succeeds.
        """
        if not self.connected:
            self.logger.warning("send_query called while disconnected")
            return False

        frame = self.build_query_frame(source, destination, primary_command, secondary_command, data=data)
        wire_data = frame
        if prepend_sync:
            wire_data = bytes([TelegramParser.SYNC_BYTE]) + wire_data
        if append_sync:
            wire_data = wire_data + bytes([TelegramParser.SYNC_BYTE])

        try:
            with self._io_lock:
                if flush_input and self._serial is not None:
                    try:
                        self._serial.reset_input_buffer()
                    except Exception:
                        # Some serial backends may not implement it; query can still proceed.
                        pass

                if self._serial is None:
                    return False
                self._serial.write(wire_data)
                self._serial.flush()

            self.logger.debug(
                "Sent query src=0x%02X dst=0x%02X cmd=%02X%02X data=%s",
                source & 0xFF,
                destination & 0xFF,
                primary_command & 0xFF,
                secondary_command & 0xFF,
                (data or b"").hex(),
            )
            return True
        except (serial.SerialException, OSError) as e:
            self.logger.error(f"Write error: {e}")
            self._connected = False
            return False

    def seconds_since_last_activity(self, now: Optional[float] = None) -> Optional[float]:
        """Return seconds since the last raw byte was received from the bus."""
        if self._last_raw_activity_monotonic is None:
            return None

        current = time.monotonic() if now is None else now
        return max(0.0, current - self._last_raw_activity_monotonic)

    def seconds_since_last_telegram(self, now: Optional[float] = None) -> Optional[float]:
        """Return seconds since the last parsed telegram was received."""
        if self._last_telegram_monotonic is None:
            return None

        current = time.monotonic() if now is None else now
        return max(0.0, current - self._last_telegram_monotonic)

    def query_once(
        self,
        source: int,
        destination: int,
        primary_command: int,
        secondary_command: int,
        data: bytes = b"",
        timeout_s: float = 1.5,
        flush_input: bool = False,
    ) -> Optional[EbusTelegram]:
        """
        Send one query and wait for a matching telegram.
        Matching is based on source, destination, command and query payload.
        """
        payload = bytes(data or b"")
        sent = self.send_query(
            source=source,
            destination=destination,
            primary_command=primary_command,
            secondary_command=secondary_command,
            data=payload,
            flush_input=flush_input,
        )
        if not sent:
            return None

        deadline = time.monotonic() + max(0.0, timeout_s)
        while self.connected and time.monotonic() <= deadline:
            telegrams = self.read_telegrams()
            for telegram in telegrams:
                if telegram.source != source:
                    continue
                if telegram.destination != destination:
                    continue
                if telegram.primary_command != primary_command or telegram.secondary_command != secondary_command:
                    continue
                if telegram.data != payload:
                    continue
                return telegram

            time.sleep(0.01)

        return None

    def telegram_generator(self) -> Generator[EbusTelegram, None, None]:
        while self.connected:
            telegrams = self.read_telegrams()
            for telegram in telegrams:
                yield telegram

            if not telegrams:
                time.sleep(0.01)


def create_connection(config: ConnectionConfig) -> SerialConnection:
    return SerialConnection(config)
