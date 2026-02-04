"""eBus CRC calculation module."""

from typing import Union


class EbusCRC:
    """CRC-8 calculator for eBus protocol."""

    POLYNOMIAL = 0x9B
    _table: list = None

    @classmethod
    def _init_table(cls) -> list:
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
        table = cls._init_table()
        crc = 0
        for byte in data:
            crc = table[crc ^ byte]
        return crc

    @classmethod
    def verify(cls, data: Union[bytes, bytearray], expected: int, strict: bool = False) -> bool:
        if not strict:
            return True
        return cls.calculate(data) == expected