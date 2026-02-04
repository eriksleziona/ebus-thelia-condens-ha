"""
Thelia Condens message definitions.
Based on Saunier Duval/Vaillant eBus protocol.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List
from enum import Enum


class DataType(Enum):
    """Data types for message fields."""
    UINT8 = "uint8"
    INT8 = "int8"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    DATA1B = "data1b"  # Signed byte / 2
    DATA1C = "data1c"  # Unsigned byte / 2
    DATA2B = "data2b"  # Signed word / 256
    DATA2C = "data2c"  # Unsigned word / 256
    BCD = "bcd"  # BCD encoded
    BIT = "bit"  # Single bit
    BYTES = "bytes"  # Raw bytes
    STRING = "string"  # ASCII string


@dataclass
class FieldDefinition:
    """Definition of a data field in a message."""
    name: str
    offset: int
    data_type: DataType
    length: int = 1
    unit: str = ""
    description: str = ""
    bit_position: int = 0  # For BIT type
    factor: float = 1.0
    offset_value: float = 0.0
    values: Dict[int, str] = field(default_factory=dict)  # For enum-like fields

    def decode(self, data: bytes) -> Any:
        """Decode field value from bytes."""
        if self.offset >= len(data):
            return None

        try:
            if self.data_type == DataType.UINT8:
                value = data[self.offset]
            elif self.data_type == DataType.INT8:
                value = int.from_bytes([data[self.offset]], 'little', signed=True)
            elif self.data_type == DataType.UINT16:
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little')
            elif self.data_type == DataType.INT16:
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little', signed=True)
            elif self.data_type == DataType.DATA1B:
                value = int.from_bytes([data[self.offset]], 'little', signed=True) / 2.0
            elif self.data_type == DataType.DATA1C:
                value = data[self.offset] / 2.0
            elif self.data_type == DataType.DATA2B:
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little', signed=True) / 256.0
            elif self.data_type == DataType.DATA2C:
                value = int.from_bytes(data[self.offset:self.offset + 2], 'little') / 256.0
            elif self.data_type == DataType.BIT:
                value = bool((data[self.offset] >> self.bit_position) & 1)
            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                value = (raw >> 4) * 10 + (raw & 0x0F)
            elif self.data_type == DataType.BYTES:
                value = data[self.offset:self.offset + self.length].hex()
            elif self.data_type == DataType.STRING:
                value = data[self.offset:self.offset + self.length].decode('ascii', errors='ignore').strip('\x00')
            else:
                value = data[self.offset]

            # Apply factor and offset
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = value * self.factor + self.offset_value

            # Check for enum mapping
            if self.values and isinstance(value, int) and value in self.values:
                return self.values[value]

            return value

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
    fields: List[FieldDefinition] = field(default_factory=list)

    @property
    def command(self) -> tuple:
        return (self.primary_command, self.secondary_command)


# ============================================
# Thelia Condens Message Definitions
# ============================================

THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}


def register_message(msg: MessageDefinition):
    """Register a message definition."""
    THELIA_MESSAGES[msg.command] = msg
    return msg


# ----- Temperature & Sensor Messages -----

register_message(MessageDefinition(
    name="outside_temp",
    primary_command=0x05,
    secondary_command=0x03,
    description="Outside temperature broadcast",
    fields=[
        FieldDefinition("outside_temp", 0, DataType.DATA2B, unit="°C", description="Outside temperature"),
    ]
))

register_message(MessageDefinition(
    name="flow_temp",
    primary_command=0x05,
    secondary_command=0x07,
    description="Flow temperature",
    fields=[
        FieldDefinition("flow_temp", 0, DataType.DATA2B, unit="°C", description="Flow temperature"),
        FieldDefinition("return_temp", 2, DataType.DATA2B, unit="°C", description="Return temperature"),
    ]
))

register_message(MessageDefinition(
    name="dhw_temp",
    primary_command=0x05,
    secondary_command=0x08,
    description="DHW temperature",
    fields=[
        FieldDefinition("dhw_temp", 0, DataType.DATA2B, unit="°C", description="DHW actual temperature"),
        FieldDefinition("dhw_setpoint", 2, DataType.DATA1C, unit="°C", description="DHW setpoint"),
    ]
))

register_message(MessageDefinition(
    name="room_temp",
    primary_command=0x05,
    secondary_command=0x09,
    description="Room temperature from thermostat",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA2B, unit="°C", description="Room temperature"),
        FieldDefinition("room_setpoint", 2, DataType.DATA1C, unit="°C", description="Room setpoint"),
    ]
))

# ----- Status Messages -----

register_message(MessageDefinition(
    name="burner_status",
    primary_command=0x05,
    secondary_command=0x01,
    description="Burner status",
    fields=[
        FieldDefinition("burner_active", 0, DataType.BIT, bit_position=0, description="Burner on/off"),
        FieldDefinition("pump_active", 0, DataType.BIT, bit_position=1, description="Pump running"),
        FieldDefinition("dhw_active", 0, DataType.BIT, bit_position=2, description="DHW mode active"),
        FieldDefinition("heating_active", 0, DataType.BIT, bit_position=3, description="Heating mode active"),
        FieldDefinition("flame_detected", 0, DataType.BIT, bit_position=4, description="Flame detected"),
    ]
))

register_message(MessageDefinition(
    name="modulation",
    primary_command=0x05,
    secondary_command=0x04,
    description="Burner modulation",
    fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%", description="Burner modulation level"),
        FieldDefinition("power", 1, DataType.DATA1C, unit="kW", description="Current power output"),
    ]
))

register_message(MessageDefinition(
    name="pressure",
    primary_command=0x05,
    secondary_command=0x12,
    description="System pressure",
    fields=[
        FieldDefinition("pressure", 0, DataType.DATA1C, unit="bar", description="System water pressure"),
    ]
))

# ----- Error Messages -----

register_message(MessageDefinition(
    name="error_status",
    primary_command=0x05,
    secondary_command=0x10,
    description="Error and status codes",
    fields=[
        FieldDefinition("error_code", 0, DataType.UINT8, description="Error code"),
        FieldDefinition("error_state", 1, DataType.UINT8, values={
            0: "no_error",
            1: "warning",
            2: "blocking",
            3: "locking"
        }),
    ]
))

# ----- Runtime/Statistics Messages -----

register_message(MessageDefinition(
    name="runtime",
    primary_command=0x05,
    secondary_command=0x14,
    description="Runtime statistics",
    fields=[
        FieldDefinition("burner_starts", 0, DataType.UINT32, description="Total burner starts"),
        FieldDefinition("burner_hours", 4, DataType.UINT32, unit="h", description="Total burner hours"),
    ]
))

# ----- Identity Messages -----

register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification",
    fields=[
        FieldDefinition("manufacturer", 0, DataType.UINT8, values={
            0x11: "Vaillant",
            0x41: "Saunier Duval"
        }),
        FieldDefinition("device_type", 1, DataType.STRING, length=10),
        FieldDefinition("sw_version", 11, DataType.BCD),
    ]
))


def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    """Get message definition by command."""
    return THELIA_MESSAGES.get((primary, secondary))


def get_all_message_names() -> List[str]:
    """Get list of all registered message names."""
    return [msg.name for msg in THELIA_MESSAGES.values()]