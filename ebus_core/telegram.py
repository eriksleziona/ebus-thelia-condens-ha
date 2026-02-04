"""
eBus Telegram structure and parsing.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List
import logging

from .crc import EbusCRC

logger = logging.getLogger(__name__)


class TelegramType(Enum):
    """eBus telegram types."""
    BROADCAST = auto()  # BC - No response expected
    MASTER_MASTER = auto()  # MM - Master to Master
    MASTER_SLAVE = auto()  # MS - Master to Slave with response


class EscapeSequence:
    """eBus escape sequence handling."""
    ESCAPE = 0xA9
    SYNC = 0xAA

    ESCAPE_ESCAPE = 0x00  # 0xA9 0x00 -> 0xA9
    ESCAPE_SYNC = 0x01  # 0xA9 0x01 -> 0xAA

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
                    result.append(data[i])
                    result.append(data[i + 1])
                i += 2
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    @classmethod
    def escape(cls, data: bytes) -> bytes:
        """Add escape sequences to data."""
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
    - ACK: Acknowledgment (for MS/MM telegrams)
    - NN2: Slave response length (for MS telegrams)
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
    response_data: Optional[bytes] = None
    response_crc: Optional[int] = None

    # Metadata
    telegram_type: TelegramType = TelegramType.BROADCAST
    valid: bool = False
    raw_bytes: bytes = field(default_factory=bytes)
    timestamp: float = 0.0

    # Known broadcast address
    BROADCAST_ADDR = 0xFE

    @property
    def command(self) -> tuple:
        """Return command as (primary, secondary) tuple."""
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        """Return command as hex string."""
        return f"{self.primary_command:02X}{self.secondary_command:02X}"

    def __repr__(self) -> str:
        return (
            f"EbusTelegram(src={self.source:02X}, dst={self.destination:02X}, "
            f"cmd={self.command_hex}, data={self.data.hex()}, valid={self.valid})"
        )


class TelegramParser:
    """Parser for eBus telegrams."""

    SYNC_BYTE = 0xAA
    ACK = 0x00
    NAK = 0xFF

    def __init__(self):
        self._buffer = bytearray()
        self._logger = logging.getLogger(self.__class__.__name__)

    def parse(self, raw_data: bytes, timestamp: float = 0.0) -> Optional[EbusTelegram]:
        """
        Parse raw bytes into an EbusTelegram.

        Args:
            raw_data: Raw bytes from eBus (unescaped)
            timestamp: Timestamp when data was received

        Returns:
            Parsed telegram or None if invalid
        """
        try:
            # Unescape the data first
            data = EscapeSequence.unescape(raw_data)

            if len(data) < 6:  # Minimum: QQ ZZ PB SB NN CRC
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

            if len(data) < 6 + data_length:
                self._logger.debug(f"Incomplete telegram data")
                return None

            # Extract data bytes
            telegram.data = bytes(data[5:5 + data_length])

            # CRC position
            crc_pos = 5 + data_length
            telegram.crc = data[crc_pos]

            # Verify CRC
            crc_data = bytes(data[:crc_pos])
            expected_crc = EbusCRC.calculate(crc_data)

            if telegram.crc != expected_crc:
                self._logger.warning(
                    f"CRC mismatch: got {telegram.crc:02X}, expected {expected_crc:02X}"
                )
                telegram.valid = False
                return telegram

            # Determine telegram type
            if telegram.destination == EbusTelegram.BROADCAST_ADDR:
                telegram.telegram_type = TelegramType.BROADCAST
            else:
                # Check for slave response
                if len(data) > crc_pos + 1:
                    # Has more data - could be MS with response
                    telegram.telegram_type = TelegramType.MASTER_SLAVE
                    # TODO: Parse slave response
                else:
                    telegram.telegram_type = TelegramType.MASTER_MASTER

            telegram.valid = True
            return telegram

        except Exception as e:
            self._logger.error(f"Error parsing telegram: {e}")
            return None