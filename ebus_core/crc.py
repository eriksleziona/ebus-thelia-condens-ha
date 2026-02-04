"""
eBus CRC calculation module.
The eBus protocol uses CRC-8 with polynomial 0x9B.
"""

from typing import Union


class EbusCRC:
    """CRC-8 calculator for eBus protocol."""

    CRC_POLYNOMIAL = 0x9B
    _table: list = None

    @classmethod
    def _generate_table(cls) -> list:
        """Generate CRC lookup table (cached)."""
        if cls._table is not None:
            return cls._table

        table = []
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ cls.CRC_POLYNOMIAL) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
            table.append(crc)
        cls._table = table
        return table

    @classmethod
    def calculate(cls, data: Union[bytes, bytearray]) -> int:
        """
        Calculate CRC-8 for given data.

        Args:
            data: Bytes to calculate CRC for

        Returns:
            CRC-8 value (0-255)
        """
        table = cls._generate_table()
        crc = 0
        for byte in data:
            crc = table[crc ^ byte]
        return crc

    @classmethod
    def verify(cls, data: Union[bytes, bytearray], expected_crc: int) -> bool:
        """
        Verify CRC matches expected value.

        Args:
            data: Bytes to verify
            expected_crc: Expected CRC value

        Returns:
            True if CRC matches
        """
        return cls.calculate(data) == expected_crc