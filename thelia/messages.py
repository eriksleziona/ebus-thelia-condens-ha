"""Thelia Condens message definitions."""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum


class DataType(Enum):
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    INT16_LE = "int16_le"
    DATA1C = "data1c"
    TEMP16 = "temp16"
    BCD = "bcd"
    BIT = "bit"
    BYTES = "bytes"


@dataclass
class FieldDefinition:
    name: str
    offset: int
    data_type: DataType
    length: int = 1
    unit: str = ""
    description: str = ""
    bit_position: int = 0
    factor: float = 1.0
    offset_value: float = 0.0

    def decode(self, data: bytes) -> Any:
        if self.offset >= len(data):
            return None

        try:
            if self.data_type == DataType.UINT8:
                value = data[self.offset]
            elif self.data_type == DataType.INT8:
                value = int.from_bytes([data[self.offset]], 'little', signed=True)
            elif self.data_type == DataType.UINT16_LE:
                if self.offset + 2 > len(data):
                    return None
                value = int.from_bytes(data[self.offset:self.offset+2], 'little')
            elif self.data_type == DataType.INT16_LE:
                if self.offset + 2 > len(data):
                    return None
                value = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
            elif self.data_type == DataType.TEMP16:
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                value = raw / 256.0
            elif self.data_type == DataType.DATA1C:
                value = data[self.offset] / 2.0
            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                value = (raw >> 4) * 10 + (raw & 0x0F)
            elif self.data_type == DataType.BIT:
                value = bool((data[self.offset] >> self.bit_position) & 1)
            elif self.data_type == DataType.BYTES:
                end = min(self.offset + self.length, len(data))
                value = data[self.offset:end].hex()
            else:
                value = data[self.offset]

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = round(value * self.factor + self.offset_value, 2)

            return value
        except Exception:
            return None


@dataclass
class MessageDefinition:
    name: str
    primary_command: int
    secondary_command: int
    description: str = ""
    fields: List[FieldDefinition] = field(default_factory=list)
    response_fields: List[FieldDefinition] = field(default_factory=list)

    @property
    def command(self) -> tuple:
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        return f"{self.primary_command:02X}{self.secondary_command:02X}"


THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}


def register_message(msg: MessageDefinition) -> MessageDefinition:
    THELIA_MESSAGES[msg.command] = msg
    return msg


def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    return THELIA_MESSAGES.get((primary, secondary))


# ============================================
# Saunier Duval Thelia Condens - B5xx Commands
# ============================================

register_message(MessageDefinition(
    name="temperatures_1",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Temperature query type 1",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("flow_temp", 0, DataType.TEMP16, unit="°C"),
        FieldDefinition("unknown1", 2, DataType.UINT8),
        FieldDefinition("value2", 3, DataType.UINT16_LE),
        FieldDefinition("status", 5, DataType.UINT8),
        FieldDefinition("flags", 6, DataType.BYTES, length=3),
    ]
))

register_message(MessageDefinition(
    name="temp_data",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature data",
    fields=[
        FieldDefinition("byte0", 0, DataType.UINT8),
        FieldDefinition("byte1", 1, DataType.UINT8),
        FieldDefinition("temp_value", 2, DataType.DATA1C, unit="°C"),
    ],
    response_fields=[
        FieldDefinition("ack_value", 0, DataType.UINT8),
    ]
))

register_message(MessageDefinition(
    name="modulation",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Modulation data",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%"),
    ]
))

register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature",
    fields=[
        FieldDefinition("room_temp_raw", 0, DataType.UINT8),
        FieldDefinition("byte1", 1, DataType.UINT8),
    ],
))

register_message(MessageDefinition(
    name="datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Date/time broadcast",
    fields=[
        FieldDefinition("status", 0, DataType.UINT8),
        FieldDefinition("seconds", 1, DataType.UINT8),
        FieldDefinition("minutes", 2, DataType.UINT8),
        FieldDefinition("hours", 3, DataType.UINT8),
        FieldDefinition("day", 4, DataType.UINT8),
        FieldDefinition("month", 5, DataType.UINT8),
        FieldDefinition("weekday", 6, DataType.UINT8),
        FieldDefinition("year", 7, DataType.UINT8),
    ]
))

register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device ID query",
    fields=[],
))


def list_messages() -> List[str]:
    return [msg.name for msg in THELIA_MESSAGES.values()]