"""
Thelia Condens + MiPro Controller message definitions.
With proper handling of 0xFF = not available.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum

# Invalid/Not Available markers
INVALID_UINT8 = 0xFF
INVALID_UINT16 = 0xFFFF
INVALID_INT16 = -1  # 0xFFFF as signed

class DataType(Enum):
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    INT16_LE = "int16_le"
    DATA1C = "data1c"        # Unsigned byte / 2 (temperatures)
    DATA1B = "data1b"        # Signed byte / 2
    TEMP16 = "temp16"        # Signed 16-bit LE / 256
    PRESSURE = "pressure"    # Unsigned byte / 10
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
    ignore_invalid: bool = True  # Filter 0xFF values

    def decode(self, data: bytes) -> Any:
        if self.offset >= len(data):
            return None

        try:
            raw_byte = data[self.offset]

            if self.data_type == DataType.UINT8:
                if self.ignore_invalid and raw_byte == INVALID_UINT8:
                    return None
                value = raw_byte

            elif self.data_type == DataType.INT8:
                if self.ignore_invalid and raw_byte == INVALID_UINT8:
                    return None
                value = int.from_bytes([raw_byte], 'little', signed=True)

            elif self.data_type == DataType.UINT16_LE:
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little')
                if self.ignore_invalid and raw == INVALID_UINT16:
                    return None
                value = raw

            elif self.data_type == DataType.INT16_LE:
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                if self.ignore_invalid and (raw == INVALID_INT16 or raw == -32768 or raw == 32767):
                    return None
                value = raw

            elif self.data_type == DataType.DATA1C:
                # Unsigned byte / 2 - common for temperatures
                if self.ignore_invalid and raw_byte == INVALID_UINT8:
                    return None
                value = round(raw_byte / 2.0, 1)

            elif self.data_type == DataType.DATA1B:
                # Signed byte / 2
                if self.ignore_invalid and raw_byte == INVALID_UINT8:
                    return None
                raw = int.from_bytes([raw_byte], 'little', signed=True)
                value = round(raw / 2.0, 1)

            elif self.data_type == DataType.TEMP16:
                # Signed 16-bit / 256 for precise temps
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                # Filter invalid values
                if self.ignore_invalid and (raw == INVALID_INT16 or raw == -32768 or raw == 32767):
                    return None
                value = round(raw / 256.0, 1)

            elif self.data_type == DataType.PRESSURE:
                # Unsigned byte / 10 for bar
                if self.ignore_invalid and raw_byte == INVALID_UINT8:
                    return None
                value = round(raw_byte / 10.0, 1)

            elif self.data_type == DataType.BCD:
                high = (raw_byte >> 4) & 0x0F
                low = raw_byte & 0x0F
                if high > 9 or low > 9:
                    return None  # Invalid BCD
                value = high * 10 + low

            elif self.data_type == DataType.BIT:
                value = bool((raw_byte >> self.bit_position) & 1)

            elif self.data_type == DataType.BYTES:
                end = min(self.offset + self.length, len(data))
                value = data[self.offset:end].hex()

            else:
                value = raw_byte

            # Apply factor and offset (only for valid numeric values)
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


THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}

def register_message(msg: MessageDefinition) -> MessageDefinition:
    THELIA_MESSAGES[msg.command] = msg
    return msg

def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    return THELIA_MESSAGES.get((primary, secondary))


# ============================================
# MESSAGE DEFINITIONS
# ============================================

# B511: Status/Temperature Query (Polymorphic)
# We map bytes as Generic UINT8 because meanings change by Type (0,1,2).
# The detailed decoding happens in parser.py -> DataAggregator
register_message(MessageDefinition(
    name="status_temps",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Status and temperature queries",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8, ignore_invalid=False),
    ],
    response_fields=[
        FieldDefinition("byte0", 0, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte1", 1, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte2", 2, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte3", 3, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte4", 4, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte5", 5, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte6", 6, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte7", 7, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("byte8", 8, DataType.UINT8, ignore_invalid=False),
    ]
))

# B504: Modulation and Outdoor Temperature
register_message(MessageDefinition(
    name="modulation_outdoor",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Modulation and outdoor temperature",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8, ignore_invalid=False),
    ],
    response_fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%"),
        FieldDefinition("outdoor_temp_raw", 1, DataType.INT16_LE),
        # Sometimes outdoor temp is in byte 1 as Data2c on older firmwares
        FieldDefinition("outdoor_temp_backup", 1, DataType.DATA1B),
        FieldDefinition("byte3", 3, DataType.UINT8, ignore_invalid=False),
    ]
))

# B510: Temperature Setpoints (Write Command)
register_message(MessageDefinition(
    name="temp_setpoint",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature setpoints",
    fields=[
        FieldDefinition("mode1", 0, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("mode2", 1, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("target_flow_temp", 2, DataType.DATA1C, unit="°C"),
        FieldDefinition("dhw_setpoint", 3, DataType.DATA1C, unit="°C"),
        FieldDefinition("byte4", 4, DataType.UINT8),
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("bytes6_8", 6, DataType.BYTES, length=3, ignore_invalid=False),
    ],
    response_fields=[
        FieldDefinition("ack", 0, DataType.UINT8, ignore_invalid=False),
    ]
))

# B509: Room Temperature from MiPro
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature from MiPro",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA1C, unit="°C"),
        FieldDefinition("room_setpoint_adjust", 1, DataType.INT8),
    ],
))

# B516: Date/Time Broadcast
register_message(MessageDefinition(
    name="datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Date/time broadcast",
    fields=[
        FieldDefinition("flags", 0, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("seconds", 1, DataType.BCD),
        FieldDefinition("minutes", 2, DataType.BCD),
        FieldDefinition("hours", 3, DataType.BCD),
        FieldDefinition("day", 4, DataType.BCD),
        FieldDefinition("month", 5, DataType.BCD),
        FieldDefinition("weekday", 6, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("year", 7, DataType.BCD),
    ]
))

# B512: Unknown / DHW Stats
register_message(MessageDefinition(
    name="b512_data",
    primary_command=0xB5,
    secondary_command=0x12,
    description="B512 - possibly DHW or pressure",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8, ignore_invalid=False),
        FieldDefinition("data", 1, DataType.BYTES, length=9, ignore_invalid=False),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10, ignore_invalid=False),
    ]
))

# 0704: Device ID
register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification",
    fields=[],
))

def list_messages() -> List[str]:
    return [msg.name for msg in THELIA_MESSAGES.values()]