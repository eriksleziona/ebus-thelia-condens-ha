"""
Thelia Condens message definitions.
Saunier Duval / Vaillant eBus protocol using B5xx commands.
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
    UINT32 = "uint32"
    DATA1B = "data1b"      # Signed byte / 2
    DATA1C = "data1c"      # Unsigned byte / 2
    DATA2B = "data2b"      # Signed word / 256
    DATA2C = "data2c"      # Unsigned word / 256
    BCD = "bcd"            # BCD encoded
    BIT = "bit"            # Single bit
    BITS = "bits"          # Multiple bits
    BYTES = "bytes"        # Raw bytes
    STRING = "string"      # ASCII string


@dataclass
class FieldDefinition:
    """Definition of a data field in a message."""
    name: str
    offset: int
    data_type: DataType
    length: int = 1
    unit: str = ""
    description: str = ""
    bit_position: int = 0
    bit_count: int = 1
    factor: float = 1.0
    offset_value: float = 0.0
    values: Dict[int, str] = field(default_factory=dict)

    def decode(self, data: bytes) -> Any:
        """Decode field value from bytes."""
        if self.offset >= len(data):
            return None

        try:
            raw_value = None

            if self.data_type == DataType.UINT8:
                raw_value = data[self.offset]
            elif self.data_type == DataType.INT8:
                raw_value = int.from_bytes([data[self.offset]], 'little', signed=True)
            elif self.data_type == DataType.UINT16:
                if self.offset + 2 > len(data):
                    return None
                raw_value = int.from_bytes(data[self.offset:self.offset+2], 'little')
            elif self.data_type == DataType.INT16:
                if self.offset + 2 > len(data):
                    return None
                raw_value = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True)
            elif self.data_type == DataType.DATA1B:
                raw_value = int.from_bytes([data[self.offset]], 'little', signed=True) / 2.0
            elif self.data_type == DataType.DATA1C:
                raw_value = data[self.offset] / 2.0
            elif self.data_type == DataType.DATA2B:
                if self.offset + 2 > len(data):
                    return None
                raw_value = int.from_bytes(data[self.offset:self.offset+2], 'little', signed=True) / 256.0
            elif self.data_type == DataType.DATA2C:
                if self.offset + 2 > len(data):
                    return None
                raw_value = int.from_bytes(data[self.offset:self.offset+2], 'little') / 256.0
            elif self.data_type == DataType.BIT:
                raw_value = bool((data[self.offset] >> self.bit_position) & 1)
            elif self.data_type == DataType.BITS:
                mask = (1 << self.bit_count) - 1
                raw_value = (data[self.offset] >> self.bit_position) & mask
            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                raw_value = (raw >> 4) * 10 + (raw & 0x0F)
            elif self.data_type == DataType.BYTES:
                end = min(self.offset + self.length, len(data))
                raw_value = data[self.offset:end].hex()
            elif self.data_type == DataType.STRING:
                end = min(self.offset + self.length, len(data))
                raw_value = data[self.offset:end].decode('ascii', errors='ignore').strip('\x00')
            else:
                raw_value = data[self.offset]

            # Apply factor and offset
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                raw_value = raw_value * self.factor + self.offset_value

            # Map enum values
            if self.values and isinstance(raw_value, int) and raw_value in self.values:
                return self.values[raw_value]

            return raw_value

        except Exception:
            return None


@dataclass
class MessageDefinition:
    """Complete message definition."""
    name: str
    primary_command: int
    secondary_command: int
    description: str = ""
    source_address: Optional[int] = None
    is_master_slave: bool = False  # True if expects slave response
    fields: List[FieldDefinition] = field(default_factory=list)
    response_fields: List[FieldDefinition] = field(default_factory=list)  # For slave response

    @property
    def command(self) -> tuple:
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        return f"{self.primary_command:02X}{self.secondary_command:02X}"


# Message Registry
THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}


def register_message(msg: MessageDefinition) -> MessageDefinition:
    """Register a message definition."""
    THELIA_MESSAGES[msg.command] = msg
    return msg


def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    """Get message definition by command bytes."""
    return THELIA_MESSAGES.get((primary, secondary))


# ============================================
# Saunier Duval Thelia Condens - B5xx Commands
# Based on captured traffic analysis
# ============================================

# ----- B510: Temperature Request -----
# Master sends to boiler (0x08), gets temperature response
register_message(MessageDefinition(
    name="temp_request",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Temperature data request (thermostat → boiler)",
    is_master_slave=True,
    fields=[
        # Master data: 9 bytes - 000059ffffff000000
        FieldDefinition("unknown1", 0, DataType.UINT8),
        FieldDefinition("unknown2", 1, DataType.UINT8),
        FieldDefinition("temp_byte", 2, DataType.DATA1C, unit="°C", description="Temperature value?"),
        # Bytes 3-8 are often 0xFF (not available) or 0x00
    ],
    response_fields=[
        FieldDefinition("response", 0, DataType.BYTES, length=1),
    ]
))

# ----- B511: Status Query -----
# Multiple sub-queries based on data byte
register_message(MessageDefinition(
    name="status_query",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Status query (different sub-types based on data)",
    is_master_slave=True,
    fields=[
        FieldDefinition("query_type", 0, DataType.UINT8,
                       values={0: "type_0", 1: "type_1", 2: "type_2"},
                       description="Query sub-type"),
    ],
    response_fields=[
        # Response varies by query type
        # Type 01 → 5f4e20ffff530100ff (9 bytes)
        # Type 02 → 0314965a785a (6 bytes)
        # Type 00 → fb020e28040f208100 (9 bytes)
        FieldDefinition("response_data", 0, DataType.BYTES, length=9),
    ]
))

# ----- B504: Modulation/Power Query -----
register_message(MessageDefinition(
    name="modulation_query",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Modulation/power query",
    is_master_slave=True,
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        # Response: 00ffffffffffffff20ff (10 bytes)
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%"),
        FieldDefinition("response_data", 1, DataType.BYTES, length=9),
    ]
))

# ----- B509: Room Temperature -----
register_message(MessageDefinition(
    name="room_temp",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Room temperature data",
    is_master_slave=True,
    fields=[
        FieldDefinition("query", 0, DataType.UINT8),
    ],
    response_fields=[
        FieldDefinition("room_temp", 0, DataType.DATA2B, unit="°C"),
        FieldDefinition("setpoint", 2, DataType.DATA1C, unit="°C"),
    ]
))

# ----- B516: Broadcast (Date/Time or Status) -----
register_message(MessageDefinition(
    name="broadcast_datetime",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Broadcast message (possibly date/time)",
    is_master_slave=False,  # Broadcast - no response
    fields=[
        # Data: 0017251404020326 (8 bytes)
        # Possibly: status, hour, minute, day, month, weekday, year?
        FieldDefinition("byte0", 0, DataType.UINT8),
        FieldDefinition("byte1", 1, DataType.UINT8),  # Could be hour (0x17 = 23, but BCD would be 17)
        FieldDefinition("byte2", 2, DataType.UINT8),  # Could be minute
        FieldDefinition("byte3", 3, DataType.UINT8),  # Could be day
        FieldDefinition("byte4", 4, DataType.UINT8),  # Could be month
        FieldDefinition("byte5", 5, DataType.UINT8),
        FieldDefinition("byte6", 6, DataType.UINT8),
        FieldDefinition("byte7", 7, DataType.UINT8),  # Could be year (0x26 = 38 or 2026?)
    ]
))

# ----- Short B516 variant -----
register_message(MessageDefinition(
    name="broadcast_status",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Short broadcast status (3 bytes)",
    is_master_slave=False,
    fields=[
        FieldDefinition("status", 0, DataType.UINT8),
        FieldDefinition("value", 1, DataType.UINT8),
        FieldDefinition("flags", 2, DataType.UINT8),
    ]
))


def list_all_messages() -> List[str]:
    """Get list of all registered message names."""
    return [msg.name for msg in THELIA_MESSAGES.values()]