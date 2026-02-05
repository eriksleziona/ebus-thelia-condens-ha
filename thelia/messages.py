"""
Thelia Condens + MiPro Controller message definitions.
Based on actual protocol analysis and user-provided decoding table.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum


class DataType(Enum):
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    INT16_LE = "int16_le"
    DATA1C = "data1c"  # Unsigned byte / 2 (for temperatures)
    DATA1B = "data1b"  # Signed byte / 2
    TEMP16 = "temp16"  # Signed 16-bit LE / 256 (for precise temps)
    PRESSURE = "pressure"  # Unsigned byte / 10 (for bar)
    BCD = "bcd"  # BCD encoded
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
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little')

            elif self.data_type == DataType.INT16_LE:
                if self.offset + 2 > len(data):
                    return None
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little', signed=True)

            elif self.data_type == DataType.DATA1C:
                # Unsigned byte divided by 2 (common for temperatures)
                value = round(data[self.offset] / 2.0, 1)

            elif self.data_type == DataType.DATA1B:
                # Signed byte divided by 2
                raw = int.from_bytes([data[self.offset]], 'little', signed=True)
                value = round(raw / 2.0, 1)

            elif self.data_type == DataType.TEMP16:
                # Signed 16-bit LE divided by 256 (for precise temps including negative)
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset + 2], 'little', signed=True)
                # Check for invalid value (0xFFFF = -1)
                if raw == -1 or raw == 32767 or raw == -32768:
                    return None
                value = round(raw / 256.0, 1)

            elif self.data_type == DataType.PRESSURE:
                # Unsigned byte divided by 10 (for bar pressure)
                value = round(data[self.offset] / 10.0, 1)

            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                high = (raw >> 4) & 0x0F
                low = raw & 0x0F
                if high > 9 or low > 9:
                    value = raw  # Not valid BCD, return raw
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
# CORRECTED MESSAGE DEFINITIONS
# Based on: 10 08 B5 xx patterns
# ============================================

# B511: Status/Temperature Query - Multiple query types
# Query byte determines what data is returned
register_message(MessageDefinition(
    name="status_temps",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Status and temperature queries",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8,
                        description="0=extended, 1=temps, 2=modulation"),
    ],
    response_fields=[
        # For query_type=1 (10 08 b5 11 01 01):
        # Response has 9 bytes:
        # Byte 0: Flow temp (÷2)
        # Byte 1: Return temp (÷2)
        # Byte 2: Unknown
        # Bytes 3-4: Often 0xFFFF
        # Byte 5: Status byte
        # Byte 6: Pressure? or flags
        # Bytes 7-8: Status flags
        FieldDefinition("flow_temp", 0, DataType.DATA1C, unit="°C",
                        description="Flow temperature (Vorlauf)"),
        FieldDefinition("return_temp", 1, DataType.DATA1C, unit="°C",
                        description="Return temperature (Rücklauf)"),
        FieldDefinition("byte2", 2, DataType.UINT8),
        FieldDefinition("bytes3_4", 3, DataType.UINT16_LE),
        FieldDefinition("status_byte", 5, DataType.UINT8),
        FieldDefinition("pressure_raw", 6, DataType.UINT8,
                        description="Might be pressure × 10"),
        FieldDefinition("flags", 7, DataType.BYTES, length=2),
    ]
))

# B504: Modulation and Outdoor Temperature Query
register_message(MessageDefinition(
    name="modulation_outdoor",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Modulation and outdoor temperature",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        # Response for 10 08 b5 04 01 00:
        # Byte 0: Modulation %
        # Bytes 1-2: Outdoor temp (signed 16-bit / 256)
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%",
                        description="Burner modulation 0-100%"),
        FieldDefinition("outdoor_temp", 1, DataType.TEMP16, unit="°C",
                        description="Outdoor temperature"),
        # Rest often 0xFF
        FieldDefinition("byte3", 3, DataType.UINT8),
    ]
))

# B510: Temperature Setpoints (MiPro → Boiler)
register_message(MessageDefinition(
    name="temp_setpoint",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature setpoints from controller",
    fields=[
        # For 10 08 b5 10 09:
        # Byte 0-1: Mode bytes
        # Byte 2: Target flow temp (÷2)
        # Byte 3: DHW setpoint (÷2)
        FieldDefinition("mode1", 0, DataType.UINT8),
        FieldDefinition("mode2", 1, DataType.UINT8),
        FieldDefinition("target_flow_temp", 2, DataType.DATA1C, unit="°C",
                        description="Target flow temperature"),
        FieldDefinition("dhw_setpoint", 3, DataType.DATA1C, unit="°C",
                        description="DHW setpoint temperature"),
        FieldDefinition("byte4", 4, DataType.UINT8),
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("bytes6_8", 6, DataType.BYTES, length=3),
    ],
    response_fields=[
        FieldDefinition("ack", 0, DataType.UINT8),
    ]
))

# B509: Room Temperature from MiPro
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature from MiPro controller",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA1C, unit="°C",
                        description="Room temperature"),
        FieldDefinition("room_setpoint_adjust", 1, DataType.INT8,
                        description="Setpoint adjustment"),
    ],
))

# B516: Date/Time Broadcast from MiPro (to 0xFE)
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
        FieldDefinition("weekday", 6, DataType.UINT8),
        FieldDefinition("year", 7, DataType.BCD),
    ]
))

# B512: Possibly DHW or Pressure related
register_message(MessageDefinition(
    name="b512_query",
    primary_command=0xB5,
    secondary_command=0x12,
    description="B512 query",
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

# 0704: Device Identification
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
    ]
))


def list_messages() -> List[str]:
    return [msg.name for msg in THELIA_MESSAGES.values()]