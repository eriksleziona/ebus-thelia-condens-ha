"""
eBus Telegram structure and parsing.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
import logging
import time

from .crc import EbusCRC


logger = logging.getLogger(__name__)


class TelegramType(Enum):
    """eBus telegram types."""
    BROADCAST = auto()      # BC - No response expected
    MASTER_MASTER = auto()  # MM - Master to Master
    MASTER_SLAVE = auto()   # MS - Master to Slave with response


class EscapeHandler:
    """eBus escape sequence handling."""

    ESCAPE = 0xA9
    SYNC = 0xAA
    ESCAPE_ESCAPE = 0x00   # 0xA9 0x00 -> 0xA9
    ESCAPE_SYNC = 0x01     # 0xA9 0x01 -> 0xAA

    @classmethod
    def unescape(cls, data: bytes) -> bytes:
        """Remove escape sequences from data."""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == cls.ESCAPE and i + 1 < len(data):
                if data[i + 1] == cls.ESCAPE_ESCAPE:
                    result.append(cls.ESCAPE)
                elif data[i + 1] == cls.ESCAPE_SYNC:
                    result.append(cls.SYNC)
                else:
                    # Invalid escape, keep as-is
                    result.append(data[i])
                    i += 1
                    continue
                i += 2
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    @classmethod
    def escape(cls, data: bytes) -> bytes:
        """Add escape sequences to data for transmission."""
        result = bytearray()
        for byte in data:
            if byte == cls.ESCAPE:
                result.extend([cls.ESCAPE, cls.ESCAPE_ESCAPE])
            elif byte == cls.SYNC:
                result.extend([cls.ESCAPE, cls.ESCAPE_SYNC])
            else:
                result.append(byte)
        return bytes(result)


@dataclass
class EbusTelegram:
    """
    Represents an eBus telegram.

    Structure:
    - QQ: Source address (1 byte)
    - ZZ: Destination address (1 byte)
    - PB: Primary command (1 byte)
    - SB: Secondary command (1 byte)
    - NN: Data length (1 byte)
    - DB1..DBn: Data bytes (NN bytes)
    - CRC: CRC of QQ to DBn (1 byte)

    For Master-Slave:
    - ACK: Acknowledgment from slave
    - NN2: Slave response length
    - DB1..DBm: Slave response data
    - CRC2: Slave CRC
    - ACK2: Master acknowledgment
    """

    # Master part
    source: int = 0
    destination: int = 0
    primary_command: int = 0
    secondary_command: int = 0
    data: bytes = field(default_factory=bytes)
    crc: int = 0

    # Slave response (for MS telegrams)
    slave_ack: Optional[int] = None
    response_data: Optional[bytes] = None
    response_crc: Optional[int] = None
    master_ack: Optional[int] = None

    # Metadata
    telegram_type: TelegramType = TelegramType.BROADCAST
    valid: bool = False
    crc_valid: bool = False
    raw_bytes: bytes = field(default_factory=bytes)
    timestamp: float = field(default_factory=time.time)

    # Constants
    BROADCAST_ADDR = 0xFE
    ACK = 0x00
    NAK = 0xFF

    @property
    def command(self) -> tuple:
        """Return command as (primary, secondary) tuple."""
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        """Return command as hex string like '0507'."""
        return f"{self.primary_command:02X}{self.secondary_command:02X}"

    @property
    def source_hex(self) -> str:
        """Return source address as hex string."""
        return f"{self.source:02X}"

    @property
    def destination_hex(self) -> str:
        """Return destination address as hex string."""
        return f"{self.destination:02X}"

    def __repr__(self) -> str:
        status = "✓" if self.valid else "✗"
        return (
            f"EbusTelegram[{status}](src=0x{self.source:02X}, dst=0x{self.destination:02X}, "
            f"cmd={self.command_hex}, len={len(self.data)}, data={self.data.hex()})"
        )


class TelegramParser:
    """
    Parser for eBus telegrams.

    Handles both raw byte streams and individual telegram extraction.
    """

    SYNC_BYTE = 0xAA
    MIN_TELEGRAM_LENGTH = 6  # QQ ZZ PB SB NN CRC (with NN=0)

    def __init__(self):
        self._buffer = bytearray()
        self._logger = logging.getLogger(self.__class__.__name__)

    def feed(self, data: bytes) -> list:
        """
        Feed raw bytes into the parser.

        Args:
            data: Raw bytes from eBus

        Returns:
            List of complete telegrams extracted
        """
        self._buffer.extend(data)
        return self._extract_telegrams()

    def _extract_telegrams(self) -> list:
        """Extract complete telegrams from buffer."""
        telegrams = []

        while True:
            # Skip leading SYNC bytes
            while len(self._buffer) > 0 and self._buffer[0] == self.SYNC_BYTE:
                self._buffer.pop(0)

            if len(self._buffer) == 0:
                break

            # Find next SYNC byte
            try:
                sync_pos = self._buffer.index(self.SYNC_BYTE)
            except ValueError:
                # No SYNC found, need more data
                # But prevent buffer overflow
                if len(self._buffer) > 512:
                    self._buffer = self._buffer[-256:]
                break

            if sync_pos > 0:
                raw_telegram = bytes(self._buffer[:sync_pos])
                self._buffer = self._buffer[sync_pos:]

                telegram = self.parse(raw_telegram)
                if telegram:
                    telegrams.append(telegram)

        return telegrams

    def parse(self, raw_data: bytes, timestamp: float = None) -> Optional[EbusTelegram]:
        """
        Parse raw bytes into an EbusTelegram.

        Args:
            raw_data: Raw bytes (between SYNC bytes, escaped)
            timestamp: Optional timestamp

        Returns:
            Parsed telegram or None if invalid
        """
        if timestamp is None:
            timestamp = time.time()

        try:
            # Unescape the data
            data = EscapeHandler.unescape(raw_data)

            if len(data) < self.MIN_TELEGRAM_LENGTH:
                self._logger.debug(f"Telegram too short: {len(data)} bytes")
                return None

            telegram = EbusTelegram(
                source=data[0],
                destination=data[1],
                primary_command=data[2],
                secondary_command=data[3],
                raw_bytes=raw_data,
                timestamp=timestamp
            )

            # Data length
            data_length = data[4]

            # Check if we have enough bytes
            expected_len = 5 + data_length + 1  # header + data + crc
            if len(data) < expected_len:
                self._logger.debug(
                    f"Incomplete telegram: have {len(data)}, need {expected_len}"
                )
                return None

            # Extract data bytes
            telegram.data = bytes(data[5:5 + data_length])

            # CRC
            crc_pos = 5 + data_length
            telegram.crc = data[crc_pos]

            # Verify CRC (over QQ to last data byte)
            crc_data = bytes(data[:crc_pos])
            expected_crc = EbusCRC.calculate(crc_data)
            telegram.crc_valid = (telegram.crc == expected_crc)

            if not telegram.crc_valid:
                self._logger.debug(
                    f"CRC mismatch: got 0x{telegram.crc:02X}, "
                    f"expected 0x{expected_crc:02X}"
                )

            # Determine telegram type
            if telegram.destination == EbusTelegram.BROADCAST_ADDR:
                telegram.telegram_type = TelegramType.BROADCAST
            else:
                # Check for slave response
                remaining = data[crc_pos + 1:]
                if len(remaining) > 0:
                    telegram.telegram_type = TelegramType.MASTER_SLAVE
                    self._parse_slave_response(telegram, remaining)
                else:
                    telegram.telegram_type = TelegramType.MASTER_MASTER

            telegram.valid = telegram.crc_valid
            return telegram

        except Exception as e:
            self._logger.error(f"Error parsing telegram: {e}")
            return None

    def _parse_slave_response(self, telegram: EbusTelegram, data: bytes) -> None:
        """Parse slave response portion of telegram."""
        if len(data) < 1:
            return

        telegram.slave_ack = data[0]

        if telegram.slave_ack != EbusTelegram.ACK:
            return

        if len(data) < 2:
            return

        response_len = data[1]

        if len(data) < 2 + response_len + 1:
            return

        telegram.response_data = bytes(data[2:2 + response_len])
        telegram.response_crc = data[2 + response_len]

        # Verify response CRC
        response_crc_data = bytes([data[1]]) + telegram.response_data
        expected_crc = EbusCRC.calculate(response_crc_data)

        if telegram.response_crc != expected_crc:
            self._logger.debug("Slave response CRC mismatch")
            telegram.valid = False

        # Master ACK
        if len(data) > 2 + response_len + 1:
            telegram.master_ack = data[2 + response_len + 1]

    def reset(self) -> None:
        """Clear the internal buffer."""
        self._buffer.clear()