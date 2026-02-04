"""
eBus CRC calculation module.
The eBus protocol uses CRC-8 with polynomial 0x9B.
"""


class EbusCRC:
    """CRC-8 calculator for eBus protocol."""

    CRC_POLYNOMIAL = 0x9B
    CRC_TABLE = None

    @classmethod
    def _generate_table(cls) -> list:
        """Generate CRC lookup table."""
        if cls.CRC_TABLE is not None:
            return cls.CRC_TABLE

        table = []
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ cls.CRC_POLYNOMIAL) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
            table.append(crc)
        cls.CRC_TABLE = table
        return table

    @classmethod
    def calculate(cls, data: bytes) -> int:
        """Calculate CRC-8 for given data."""
        table = cls._generate_table()
        crc = 0
        for byte in data:
            crc = table[crc ^ byte]
        return crc

    @classmethod
    def verify(cls, data: bytes, expected_crc: int) -> bool:
        """Verify CRC matches expected value."""
        return cls.calculate(data) == expected_crc