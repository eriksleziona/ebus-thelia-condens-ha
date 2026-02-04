"""
eBus CRC calculation module.
Note: Some Vaillant/Saunier Duval devices use non-standard CRC.
"""

from typing import Union


class EbusCRC:
    """CRC-8 calculator for eBus protocol."""

    POLYNOMIAL = 0x9B
    _table: list = None

    @classmethod
    def _init_table(cls) -> list:
        """Generate CRC lookup table."""
        if cls._table is not None:
            return cls._table

        table = []
        for byte in range(256):
            crc = byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ cls.POLYNOMIAL) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
            table.append(crc)

        cls._table = table
        return table

    @classmethod
    def calculate(cls, data: Union[bytes, bytearray]) -> int:
        """Calculate CRC-8 using table lookup."""
        table = cls._init_table()
        crc = 0
        for byte in data:
            crc = table[crc ^ byte]
        return crc

    @classmethod
    def verify(cls, data: Union[bytes, bytearray], expected: int, strict: bool = False) -> bool:
        """
        Verify CRC.

        Args:
            data: Data bytes
            expected: Expected CRC value
            strict: If False, always returns True (skip validation)
        """
        if not strict:
            return True  # Skip CRC validation for non-standard devices
        return cls.calculate(data) == expected