"""
Thelia Condens + MiPro Controller message definitions.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum


class DataType(Enum):
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    INT16_LE = "int16_le"
    DATA1C = "data1c"
    DATA2B = "data2b"
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
                value = round(raw / 256.0, 1)
            elif self.data_type == DataType.DATA1C:
                value = round(data[self.offset] / 2.0, 1)
            elif self.data_type == DataType.DATA2B:
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                value = round(raw / 256.0, 1)
            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                high = (raw >> 4) & 0x0F
                low = raw & 0x0F
                if high > 9 or low > 9:
                    value = raw
                else:
                    value = high * 10 + low
            elif self.data_type == DataType.BIT:
                value = bool((data[self.offset] >> self.bit_position) & 1)
            elif self.data_type == DataType.BYTES:
                end = min(self.offset + self.length, len(data))
                value = data[self.offset:end].hex()
            else:
                value = data[self.offset]

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if self.factor != 1.0 or self.offset_value != 0.0:
                    value = round(value * self.factor + self.offset_value, 1)

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
# BOILER + MIPRO MESSAGES
# ============================================

# B509: Room Temperature (MiPro → Boiler)
# The MiPro sends the current room temperature and possibly setpoint adjustment
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature from MiPro controller",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA1C, unit="°C",
                       description="Current room temperature"),
        FieldDefinition("room_setpoint_adjust", 1, DataType.INT8, unit="",
                       description="Room setpoint adjustment"),
    ],
))

# B510: Temperature Setpoint Exchange (MiPro ↔ Boiler)
register_message(MessageDefinition(
    name="temp_setpoint",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature setpoint",
    fields=[
        FieldDefinition("mode1", 0, DataType.UINT8, description="Mode byte 1"),
        FieldDefinition("mode2", 1, DataType.UINT8, description="Mode byte 2"),
        FieldDefinition("flow_setpoint", 2, DataType.DATA1C, unit="°C",
                       description="Requested flow temperature"),
        FieldDefinition("byte3", 3, DataType.UINT8),
        FieldDefinition("byte4", 4, DataType.UINT8),
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("bytes6_8", 6, DataType.BYTES, length=3),
    ],
    response_fields=[
        FieldDefinition("ack", 0, DataType.UINT8),
    ]
))

# B511: Multi-purpose Status Query (MiPro → Boiler)
register_message(MessageDefinition(
    name="status_temps",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Status and temperature queries",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8,
                       description="0=extended, 1=flow temp, 2=setpoints"),
    ],
    response_fields=[
        FieldDefinition("temp1", 0, DataType.TEMP16, unit="°C"),
        FieldDefinition("byte2", 2, DataType.UINT8),
        FieldDefinition("temp2_raw", 3, DataType.UINT16_LE),
        FieldDefinition("status_byte", 5, DataType.UINT8),
        FieldDefinition("flags", 6, DataType.BYTES, length=3),
    ]
))

# B504: Modulation Query (MiPro → Boiler)
register_message(MessageDefinition(
    name="modulation",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Burner modulation query",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%",
                       description="Burner modulation 0-100%"),
        FieldDefinition("power_byte", 8, DataType.UINT8),
    ]
))

# B516: Date/Time Broadcast (MiPro → Broadcast)
register_message(MessageDefinition(
    name="datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Date/time broadcast from MiPro",
    fields=[
        FieldDefinition("flags", 0, DataType.UINT8),
        FieldDefinition("seconds", 1, DataType.BCD),
        FieldDefinition("minutes", 2, DataType.BCD),
        FieldDefinition("hours", 3, DataType.BCD),
        FieldDefinition("day", 4, DataType.BCD),
        FieldDefinition("month", 5, DataType.BCD),
        FieldDefinition("weekday", 6, DataType.UINT8),
        FieldDefinition("year", 7, DataType.BCD),
    ]
))

# B512: Possibly Pressure or DHW
register_message(MessageDefinition(
    name="b512_query",
    primary_command=0xB5,
    secondary_command=0x12,
    description="B512 query (pressure/DHW?)",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
        FieldDefinition("data", 1, DataType.BYTES, length=9),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# B513: Unknown
register_message(MessageDefinition(
    name="b513_query",
    primary_command=0xB5,
    secondary_command=0x13,
    description="B513 query",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
        FieldDefinition("data", 1, DataType.BYTES, length=9),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# B514: Possibly Schedule/Program
register_message(MessageDefinition(
    name="b514_query",
    primary_command=0xB5,
    secondary_command=0x14,
    description="B514 query (schedule?)",
    fields=[
        FieldDefinition("data", 0, DataType.BYTES, length=10),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# B515: Possibly Errors/History
register_message(MessageDefinition(
    name="b515_query",
    primary_command=0xB5,
    secondary_command=0x15,
    description="B515 query (errors/history?)",
    fields=[
        FieldDefinition("data", 0, DataType.BYTES, length=10),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# 0704: Device Identification (scan)
register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification query",
    fields=[],
    response_fields=[
        FieldDefinition("manufacturer", 0, DataType.UINT8),
        FieldDefinition("device_id", 1, DataType.BYTES, length=5),
        FieldDefinition("sw_version", 6, DataType.UINT16_LE),
        FieldDefinition("hw_version", 8, DataType.UINT16_LE),
    ]
))

# 0700: Device Presence
register_message(MessageDefinition(
    name="device_presence",
    primary_command=0x07,
    secondary_command=0x00,
    description="Device presence query",
    fields=[],
))


def list_messages() -> List[str]:
    return [msg.name for msg in THELIA_MESSAGES.values()]