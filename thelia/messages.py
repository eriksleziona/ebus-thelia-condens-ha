"""
Thelia Condens / Saunier Duval message definitions.
Based on actual captured traffic analysis.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum


class DataType(Enum):
    """Data types for message fields."""
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT16_LE = "uint16_le"  # Little-endian
    INT16_LE = "int16_le"
    DATA1C = "data1c"        # Unsigned byte / 2
    DATA2B = "data2b"        # Signed word / 256
    DATA2C = "data2c"        # Unsigned word / 256
    TEMP16 = "temp16"        # Temperature: int16 LE / 256
    BCD = "bcd"
    BIT = "bit"
    BYTES = "bytes"


@dataclass
class FieldDefinition:
    """Field definition for message parsing."""
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
        """Decode field from bytes."""
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
                # Temperature as signed 16-bit little-endian / 256
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                value = raw / 256.0

            elif self.data_type == DataType.DATA1C:
                value = data[self.offset] / 2.0

            elif self.data_type == DataType.DATA2B:
                if self.offset + 2 > len(data):
                    return None
                raw = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
                value = raw / 256.0

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

            # Apply factor and offset
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = round(value * self.factor + self.offset_value, 2)

            return value

        except Exception:
            return None


@dataclass
class MessageDefinition:
    """Message definition."""
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


# Message Registry
THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}


def register_message(msg: MessageDefinition) -> MessageDefinition:
    THELIA_MESSAGES[msg.command] = msg
    return msg


def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    return THELIA_MESSAGES.get((primary, secondary))


# ============================================
# Saunier Duval Thelia Condens Messages
# Based on captured traffic
# ============================================

# ----- B511: Status/Temperature Query -----
# This has multiple sub-types based on the query byte

# B511 Type 01: Flow/Return Temperatures
register_message(MessageDefinition(
    name="temperatures_1",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Temperature query type 1 (flow/return)",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8, description="Query sub-type"),
    ],
    response_fields=[
        # Response: 5D 4C 20 FF FF 53 01 00 FF (9 bytes)
        # Bytes 0-1: Flow temp (little-endian / 256)
        FieldDefinition("flow_temp", 0, DataType.TEMP16, unit="°C", description="Flow temperature"),
        # Byte 2: Unknown (often 0x20 = 32)
        FieldDefinition("unknown1", 2, DataType.UINT8),
        # Bytes 3-4: Often 0xFFFF (not available)
        FieldDefinition("value2", 3, DataType.UINT16_LE),
        # Byte 5: Status? (0x53 = 83)
        FieldDefinition("status", 5, DataType.UINT8),
        # Bytes 6-8: Flags?
        FieldDefinition("flags", 6, DataType.BYTES, length=3),
    ]
))

# B511 Type 02: Secondary Temperatures
register_message(MessageDefinition(
    name="temperatures_2",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Temperature query type 2",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
    ],
    response_fields=[
        # Response: 03 14 96 5A 78 5A (6 bytes)
        FieldDefinition("byte0", 0, DataType.UINT8),
        FieldDefinition("outdoor_temp", 1, DataType.TEMP16, unit="°C", description="Outdoor temperature?"),
        FieldDefinition("setpoint", 3, DataType.TEMP16, unit="°C", description="Setpoint?"),
        FieldDefinition("byte5", 5, DataType.UINT8),
    ]
))

# B511 Type 00: Extended Status
register_message(MessageDefinition(
    name="status_extended",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Extended status query",
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8),
    ],
    response_fields=[
        # Response: E6 02 0E 28 04 0F 20 81 00 (9 bytes)
        FieldDefinition("status1", 0, DataType.UINT8),
        FieldDefinition("status2", 1, DataType.UINT8),
        FieldDefinition("byte2", 2, DataType.UINT8),
        FieldDefinition("dhw_temp", 3, DataType.DATA1C, unit="°C", description="DHW temperature?"),
        FieldDefinition("byte4", 4, DataType.UINT8),
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("dhw_setpoint", 6, DataType.DATA1C, unit="°C", description="DHW setpoint?"),
        FieldDefinition("flags", 7, DataType.BYTES, length=2),
    ]
))

# ----- B510: Temperature Data -----
register_message(MessageDefinition(
    name="temp_data",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature data exchange",
    fields=[
        # Master sends: 00 00 59 FF FF FF 00 00 00 (9 bytes)
        FieldDefinition("byte0", 0, DataType.UINT8),
        FieldDefinition("byte1", 1, DataType.UINT8),
        FieldDefinition("temp_value", 2, DataType.DATA1C, unit="°C"),  # 0x59 = 89 → 44.5°C
        FieldDefinition("bytes3_5", 3, DataType.BYTES, length=3),
        FieldDefinition("bytes6_8", 6, DataType.BYTES, length=3),
    ],
    response_fields=[
        # Response: 01 (1 byte) - simple ACK
        FieldDefinition("ack_value", 0, DataType.UINT8),
    ]
))

# ----- B504: Modulation/Power -----
register_message(MessageDefinition(
    name="modulation",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Modulation and power data",
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        # Response: 00 FF FF FF FF FF FF FF 20 FF (10 bytes)
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%", description="Burner modulation"),
        # Rest often 0xFF (not available) or specific values
        FieldDefinition("byte8", 8, DataType.UINT8),  # 0x20 = 32
    ]
))

# ----- B509: Room Temperature -----
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature from thermostat",
    fields=[
        # Master: 28 02 (2 bytes)
        FieldDefinition("room_temp_raw", 0, DataType.UINT8),  # 0x28 = 40 → 20.0°C?
        FieldDefinition("byte1", 1, DataType.UINT8),
    ],
))

# ----- B516: Date/Time Broadcast -----
register_message(MessageDefinition(
    name="datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Date/time broadcast",
    fields=[
        # Long format: 00 21 47 14 04 02 03 26 (8 bytes)
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

# B516 Short: Status broadcast
register_message(MessageDefinition(
    name="status_broadcast",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Status broadcast (short)",
    fields=[
        # Short format: 01 20 FF (3 bytes)
        FieldDefinition("mode", 0, DataType.UINT8),
        FieldDefinition("value", 1, DataType.UINT8),  # 0x20 = 32 → 16.0°C setpoint?
        FieldDefinition("flags", 2, DataType.UINT8),
    ]
))

# ----- 0704: Device ID Query -----
register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification query",
    fields=[],  # No data in query
))


def list_messages() -> List[str]:
    """List all registered message names."""
    return [msg.name for msg in THELIA_MESSAGES.values()]