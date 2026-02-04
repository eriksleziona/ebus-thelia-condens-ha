"""
Thelia Condens / Saunier Duval message definitions.
Based on actual captured traffic analysis.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum


class DataType(Enum):
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    INT16_LE = "int16_le"
    DATA1C = "data1c"        # Unsigned byte / 2
    TEMP16 = "temp16"        # Temperature: int16 LE / 256
    BCD = "bcd"              # BCD encoded byte (0x26 → 26)
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

            elif self.data_type == DataType.BCD:
                # BCD: 0x26 → 26, 0x59 → 59
                raw = data[self.offset]
                high = (raw >> 4) & 0x0F
                low = raw & 0x0F
                # Validate BCD (each nibble should be 0-9)
                if high > 9 or low > 9:
                    value = raw  # Return raw if not valid BCD
                else:
                    value = high * 10 + low

            elif self.data_type == DataType.BIT:
                value = bool((data[self.offset] >> self.bit_position) & 1)

            elif self.data_type == DataType.BYTES:
                end = min(self.offset + self.length, len(data))
                value = data[self.offset:end].hex()

            else:
                value = data[self.offset]

            # Apply factor and offset
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
# Saunier Duval Thelia Condens Messages
# ============================================

# B511: Multi-purpose status/temperature query
# Different query_type values return different data:
#   query_type=0: Extended status (outdoor temp, DHW, etc.)
#   query_type=1: Flow temperature (actual)
#   query_type=2: Setpoints (outdoor cutoff, etc.)
register_message(MessageDefinition(
    name="status_temps",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Status and temperature data",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("temp1", 0, DataType.TEMP16, unit="°C"),
        FieldDefinition("byte2", 2, DataType.UINT8),
        FieldDefinition("temp2_raw", 3, DataType.UINT16_LE),
        FieldDefinition("status_byte", 5, DataType.UINT8),
        FieldDefinition("flags", 6, DataType.BYTES, length=3),
    ]
))

# B510: Temperature setpoint exchange
register_message(MessageDefinition(
    name="temp_setpoint",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature setpoint",
    fields=[
        FieldDefinition("mode1", 0, DataType.UINT8),
        FieldDefinition("mode2", 1, DataType.UINT8),
        FieldDefinition("flow_setpoint", 2, DataType.DATA1C, unit="°C"),
        FieldDefinition("byte3", 3, DataType.UINT8),
        FieldDefinition("byte4", 4, DataType.UINT8),
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("bytes6_8", 6, DataType.BYTES, length=3),
    ],
    response_fields=[
        FieldDefinition("ack", 0, DataType.UINT8),
    ]
))

# B504: Modulation
register_message(MessageDefinition(
    name="modulation",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Burner modulation",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%"),
        FieldDefinition("power_byte", 8, DataType.UINT8),
    ]
))

# B509: Room temperature from thermostat
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA1C, unit="°C"),
        FieldDefinition("byte1", 1, DataType.UINT8),
    ],
))

# B516: Date/Time broadcast - using BCD for time fields
register_message(MessageDefinition(
    name="datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Date/time broadcast",
    fields=[
        FieldDefinition("flags", 0, DataType.UINT8),
        FieldDefinition("seconds", 1, DataType.BCD),
        FieldDefinition("minutes", 2, DataType.BCD),
        FieldDefinition("hours", 3, DataType.BCD),
        FieldDefinition("day", 4, DataType.BCD),
        FieldDefinition("month", 5, DataType.BCD),
        FieldDefinition("weekday", 6, DataType.UINT8),  # Not BCD
        FieldDefinition("year", 7, DataType.BCD),
    ]
))

# B512: Pressure or other parameter
register_message(MessageDefinition(
    name="command_b512",
    primary_command=0xB5,
    secondary_command=0x12,
    description="B512 command (possibly pressure)",
    fields=[
        FieldDefinition("data", 0, DataType.BYTES, length=10),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# B513: Unknown
register_message(MessageDefinition(
    name="command_b513",
    primary_command=0xB5,
    secondary_command=0x13,
    description="B513 command",
    fields=[
        FieldDefinition("data", 0, DataType.BYTES, length=10),
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=10),
    ]
))

# 0704: Device identification - occurs often during bus scan
register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification query",
    fields=[],
))


def list_messages() -> List[str]:
    return [msg.name for msg in THELIA_MESSAGES.values()]