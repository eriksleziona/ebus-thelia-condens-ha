"""
eBus Telegram structure and parsing.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List
import logging
import time

from .crc import EbusCRC


logger = logging.getLogger(__name__)


class TelegramType(Enum):
    """eBus telegram types."""
    BROADCAST = auto()
    MASTER_MASTER = auto()
    MASTER_SLAVE = auto()


@dataclass
class EbusTelegram:
    """Represents an eBus telegram."""

    # Master part
    source: int = 0
    destination: int = 0
    primary_command: int = 0
    secondary_command: int = 0
    data: bytes = field(default_factory=bytes)
    crc: int = 0

    # Slave response
    slave_ack: Optional[int] = None
    response_data: Optional[bytes] = None
    response_crc: Optional[int] = None
    master_ack: Optional[int] = None

    # Metadata
    telegram_type: TelegramType = TelegramType.BROADCAST
    valid: bool = True  # Default to True, set False only on structural errors
    raw_bytes: bytes = field(default_factory=bytes)
    timestamp: float = field(default_factory=time.time)

    BROADCAST_ADDR = 0xFE
    ACK = 0x00
    NAK = 0xFF

    @property
    def command(self) -> tuple:
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        return f"{self.primary_command:02X}{self.secondary_command:02X}"

    def __repr__(self) -> str:
        resp = f" resp={self.response_data.hex()}" if self.response_data else ""
        return (
            f"Telegram(src=0x{self.source:02X} dst=0x{self.destination:02X} "
            f"cmd={self.command_hex} data={self.data.hex()}{resp})"
        )


class TelegramParser:
    """Parser for eBus telegrams."""

    SYNC_BYTE = 0xAA
    MIN_LENGTH = 6

    def __init__(self, validate_crc: bool = False):
        """
        Initialize parser.

        Args:
            validate_crc: If True, reject telegrams with bad CRC.
                         Default False for Vaillant/SD devices.
        """
        self._buffer = bytearray()
        self._validate_crc = validate_crc
        self._logger = logging.getLogger(self.__class__.__name__)

    def feed(self, data: bytes) -> List[EbusTelegram]:
        """Feed raw bytes and extract complete telegrams."""
        self._buffer.extend(data)
        return self._extract_telegrams()

    def _extract_telegrams(self) -> List[EbusTelegram]:
        """Extract telegrams from buffer."""
        telegrams = []

        while True:
            # Skip leading SYNC bytes
            while len(self._buffer) > 0 and self._buffer[0] == self.SYNC_BYTE:
                self._buffer.pop(0)

            if len(self._buffer) == 0:
                break

            # Find next SYNC
            try:
                sync_pos = self._buffer.index(self.SYNC_BYTE)
            except ValueError:
                if len(self._buffer) > 512:
                    self._buffer = self._buffer[-256:]
                break

            if sync_pos > 0:
                raw = bytes(self._buffer[:sync_pos])
                self._buffer = self._buffer[sync_pos:]

                telegram = self.parse(raw)
                if telegram:
                    telegrams.append(telegram)

        return telegrams

    def parse(self, raw_data: bytes, timestamp: float = None) -> Optional[EbusTelegram]:
        """Parse raw bytes into telegram."""
        if timestamp is None:
            timestamp = time.time()

        if len(raw_data) < self.MIN_LENGTH:
            return None

        try:
            telegram = EbusTelegram(
                source=raw_data[0],
                destination=raw_data[1],
                primary_command=raw_data[2],
                secondary_command=raw_data[3],
                raw_bytes=raw_data,
                timestamp=timestamp
            )

            nn = raw_data[4]  # Data length

            # Check minimum length
            if len(raw_data) < 5 + nn + 1:
                return None

            # Extract master data
            telegram.data = bytes(raw_data[5:5 + nn])
            telegram.crc = raw_data[5 + nn]

            # Determine type
            if telegram.destination == EbusTelegram.BROADCAST_ADDR:
                telegram.telegram_type = TelegramType.BROADCAST
            else:
                telegram.telegram_type = TelegramType.MASTER_SLAVE
                # Parse slave response
                slave_start = 5 + nn + 1
                if len(raw_data) > slave_start:
                    self._parse_slave_response(telegram, raw_data[slave_start:])

            telegram.valid = True
            return telegram

        except Exception as e:
            self._logger.debug(f"Parse error: {e}")
            return None

    def _parse_slave_response(self, telegram: EbusTelegram, data: bytes) -> None:
        """Parse slave response."""
        if len(data) < 1:
            return

        telegram.slave_ack = data[0]

        if telegram.slave_ack != EbusTelegram.ACK:
            return

        if len(data) < 2:
            return

        resp_len = data[1]

        if len(data) < 2 + resp_len:
            return

        telegram.response_data = bytes(data[2:2 + resp_len])

        if len(data) > 2 + resp_len:
            telegram.response_crc = data[2 + resp_len]

        if len(data) > 3 + resp_len:
            telegram.master_ack = data[3 + resp_len]

    def reset(self):
        """Clear buffer."""
        self._buffer.clear()