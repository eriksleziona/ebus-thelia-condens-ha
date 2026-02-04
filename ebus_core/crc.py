"""
eBus CRC calculation module.
"""

from typing import Union


class EbusCRC:
    """
    CRC-8 calculator for eBus protocol.

    Polynomial: 0x9B (x^8 + x^7 + x^4 + x^3 + x + 1)
    """

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
        """Calculate CRC-8 for given data."""
        table = cls._init_table()
        crc = 0
        for byte in data:
            crc = table[crc ^ byte]
        return crc

    @classmethod
    def calculate_alt(cls, data: Union[bytes, bytearray]) -> int:
        """
        Alternative CRC calculation (bit-by-bit).
        Used to verify against table method.
        """
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ cls.POLYNOMIAL) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    @classmethod
    def verify(cls, data: Union[bytes, bytearray], expected: int) -> bool:
        """Verify CRC matches."""
        return cls.calculate(data) == expected


# Quick test
if __name__ == "__main__":
    # Test both methods produce same result
    test_data = bytes([0x10, 0x08, 0xB5, 0x11, 0x01, 0x01])

    crc1 = EbusCRC.calculate(test_data)
    crc2 = EbusCRC.calculate_alt(test_data)

    print(f"Test data: {test_data.hex()}")
    print(f"CRC (table): 0x{crc1:02X}")
    print(f"CRC (bit):   0x{crc2:02X}")
    print(f"Match: {crc1 == crc2}")