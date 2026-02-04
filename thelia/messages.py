"""
Thelia Condens message definitions.
Based on Saunier Duval / Vaillant eBus protocol.
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
    bit_position: int = 0      # For BIT type
    bit_count: int = 1         # For BITS type
    factor: float = 1.0
    offset_value: float = 0.0
    values: Dict[int, str] = field(default_factory=dict)  # Enum mapping

    def decode(self, data: bytes) -> Any:
        """
        Decode field value from bytes.

        Args:
            data: Raw data bytes

        Returns:
            Decoded value
        """
        if self.offset >= len(data):
            return None

        if self.offset + self.length > len(data) and self.data_type not in (DataType.BIT, DataType.BITS):
            return None

        try:
            raw_value = None

            if self.data_type == DataType.UINT8:
                raw_value = data[self.offset]

            elif self.data_type == DataType.INT8:
                raw_value = int.from_bytes(
                    [data[self.offset]], 'little', signed=True
                )

            elif self.data_type == DataType.UINT16:
                raw_value = int.from_bytes(
                    data[self.offset:self.offset + 2], 'little'
                )

            elif self.data_type == DataType.INT16:
                raw_value = int.from_bytes(
                    data[self.offset:self.offset + 2], 'little', signed=True
                )

            elif self.data_type == DataType.UINT32:
                raw_value = int.from_bytes(
                    data[self.offset:self.offset + 4], 'little'
                )

            elif self.data_type == DataType.DATA1B:
                # Signed byte divided by 2
                raw = int.from_bytes([data[self.offset]], 'little', signed=True)
                raw_value = raw / 2.0

            elif self.data_type == DataType.DATA1C:
                # Unsigned byte divided by 2
                raw_value = data[self.offset] / 2.0

            elif self.data_type == DataType.DATA2B:
                # Signed word divided by 256
                raw = int.from_bytes(
                    data[self.offset:self.offset + 2], 'little', signed=True
                )
                raw_value = raw / 256.0

            elif self.data_type == DataType.DATA2C:
                # Unsigned word divided by 256
                raw = int.from_bytes(
                    data[self.offset:self.offset + 2], 'little'
                )
                raw_value = raw / 256.0

            elif self.data_type == DataType.BIT:
                raw_value = bool((data[self.offset] >> self.bit_position) & 1)

            elif self.data_type == DataType.BITS:
                mask = (1 << self.bit_count) - 1
                raw_value = (data[self.offset] >> self.bit_position) & mask

            elif self.data_type == DataType.BCD:
                raw = data[self.offset]
                raw_value = (raw >> 4) * 10 + (raw & 0x0F)

            elif self.data_type == DataType.BYTES:
                raw_value = data[self.offset:self.offset + self.length].hex()

            elif self.data_type == DataType.STRING:
                raw_value = data[self.offset:self.offset + self.length].decode(
                    'ascii', errors='ignore'
                ).strip('\x00')

            else:
                raw_value = data[self.offset]

            # Apply factor and offset for numeric types
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                raw_value = raw_value * self.factor + self.offset_value

            # Map to enum value if defined
            if self.values and isinstance(raw_value, int):
                if raw_value in self.values:
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
    fields: List[FieldDefinition] = field(default_factory=list)

    @property
    def command(self) -> tuple:
        """Return command as tuple."""
        return (self.primary_command, self.secondary_command)

    @property
    def command_hex(self) -> str:
        """Return command as hex string."""
        return f"{self.primary_command:02X}{self.secondary_command:02X}"


# ============================================
# Message Registry
# ============================================

THELIA_MESSAGES: Dict[tuple, MessageDefinition] = {}


def register_message(msg: MessageDefinition) -> MessageDefinition:
    """Register a message definition."""
    THELIA_MESSAGES[msg.command] = msg
    return msg


def get_message_definition(primary: int, secondary: int) -> Optional[MessageDefinition]:
    """Get message definition by command bytes."""
    return THELIA_MESSAGES.get((primary, secondary))


# ============================================
# Thelia Condens Message Definitions
# ============================================

# ----- Broadcast Messages (destination 0xFE) -----

register_message(MessageDefinition(
    name="datetime",
    primary_command=0x07,
    secondary_command=0x00,
    description="Date and time broadcast",
    fields=[
        FieldDefinition("second", 0, DataType.BCD, description="Seconds"),
        FieldDefinition("minute", 1, DataType.BCD, description="Minutes"),
        FieldDefinition("hour", 2, DataType.BCD, description="Hours"),
        FieldDefinition("day", 3, DataType.BCD, description="Day"),
        FieldDefinition("month", 4, DataType.BCD, description="Month"),
        FieldDefinition("year", 5, DataType.BCD, description="Year (0-99)"),
        FieldDefinition("weekday", 6, DataType.UINT8, description="Day of week"),
    ]
))

register_message(MessageDefinition(
    name="outside_temp",
    primary_command=0x05,
    secondary_command=0x03,
    description="Outside temperature",
    fields=[
        FieldDefinition(
            "outside_temp", 0, DataType.DATA2B,
            unit="°C", description="Outside temperature"
        ),
    ]
))

register_message(MessageDefinition(
    name="flow_return_temp",
    primary_command=0x05,
    secondary_command=0x07,
    description="Flow and return temperatures",
    fields=[
        FieldDefinition(
            "flow_temp", 0, DataType.DATA2B,
            unit="°C", description="Flow temperature"
        ),
        FieldDefinition(
            "return_temp", 2, DataType.DATA2B,
            unit="°C", description="Return temperature"
        ),
    ]
))

register_message(MessageDefinition(
    name="dhw_temps",
    primary_command=0x05,
    secondary_command=0x08,
    description="DHW temperatures",
    fields=[
        FieldDefinition(
            "dhw_temp", 0, DataType.DATA2B,
            unit="°C", description="DHW actual temperature"
        ),
        FieldDefinition(
            "dhw_setpoint", 2, DataType.DATA1C,
            unit="°C", description="DHW setpoint"
        ),
    ]
))

register_message(MessageDefinition(
    name="room_temp",
    primary_command=0x05,
    secondary_command=0x09,
    description="Room temperature from thermostat",
    fields=[
        FieldDefinition(
            "room_temp", 0, DataType.DATA2B,
            unit="°C", description="Room temperature"
        ),
        FieldDefinition(
            "room_setpoint", 2, DataType.DATA1C,
            unit="°C", description="Room setpoint"
        ),
    ]
))

# ----- Status Messages -----

register_message(MessageDefinition(
    name="status_flags",
    primary_command=0x05,
    secondary_command=0x01,
    description="Boiler status flags",
    fields=[
        FieldDefinition(
            "burner", 0, DataType.BIT, bit_position=0,
            description="Burner on/off"
        ),
        FieldDefinition(
            "pump", 0, DataType.BIT, bit_position=1,
            description="Pump running"
        ),
        FieldDefinition(
            "dhw_mode", 0, DataType.BIT, bit_position=2,
            description="DHW mode active"
        ),
        FieldDefinition(
            "heating_mode", 0, DataType.BIT, bit_position=3,
            description="Heating mode active"
        ),
        FieldDefinition(
            "flame", 0, DataType.BIT, bit_position=4,
            description="Flame detected"
        ),
    ]
))

register_message(MessageDefinition(
    name="modulation",
    primary_command=0x05,
    secondary_command=0x04,
    description="Burner modulation",
    fields=[
        FieldDefinition(
            "modulation", 0, DataType.UINT8,
            unit="%", description="Modulation level"
        ),
        FieldDefinition(
            "power", 1, DataType.DATA1C,
            unit="kW", description="Current power"
        ),
    ]
))

register_message(MessageDefinition(
    name="pressure",
    primary_command=0x05,
    secondary_command=0x12,
    description="System water pressure",
    fields=[
        FieldDefinition(
            "pressure", 0, DataType.DATA1C,
            unit="bar", description="Water pressure"
        ),
    ]
))

register_message(MessageDefinition(
    name="error",
    primary_command=0x05,
    secondary_command=0x10,
    description="Error status",
    fields=[
        FieldDefinition("error_code", 0, DataType.UINT8, description="Error code"),
        FieldDefinition(
            "error_state", 1, DataType.UINT8,
            values={0: "ok", 1: "warning", 2: "blocking", 3: "locking"},
            description="Error state"
        ),
    ]
))

# ----- Identification -----

register_message(MessageDefinition(
    name="device_id",
    primary_command=0x07,
    secondary_command=0x04,
    description="Device identification",
    fields=[
        FieldDefinition(
            "manufacturer", 0, DataType.UINT8,
            values={0x11: "Vaillant", 0x41: "Saunier Duval"},
            description="Manufacturer ID"
        ),
        FieldDefinition(
            "device_id", 1, DataType.BYTES, length=5,
            description="Device ID"
        ),
        FieldDefinition(
            "sw_version", 6, DataType.UINT16,
            description="Software version"
        ),
    ]
))

# Add these at the end of the file, before or after the existing definitions:

# ============================================
# Vaillant/Saunier Duval B5xx Commands
# These are manufacturer-specific versions
# ============================================

register_message(MessageDefinition(
    name="vaillant_status",
    primary_command=0xB5,
    secondary_command=0x11,
    description="Vaillant status message",
    fields=[
        FieldDefinition("status_byte", 0, DataType.UINT8, description="Status flags"),
    ]
))

register_message(MessageDefinition(
    name="vaillant_temps",
    primary_command=0xB5,
    secondary_command=0x10,
    description="Vaillant temperature data",
    fields=[
        FieldDefinition("temp1", 0, DataType.DATA2B, unit="°C"),
        FieldDefinition("temp2", 2, DataType.DATA2B, unit="°C"),
    ]
))

register_message(MessageDefinition(
    name="vaillant_room",
    primary_command=0xB5,
    secondary_command=0x09,
    description="Vaillant room temperature",
    fields=[
        FieldDefinition("room_temp", 0, DataType.DATA2B, unit="°C"),
        FieldDefinition("setpoint", 2, DataType.DATA1C, unit="°C"),
    ]
))

register_message(MessageDefinition(
    name="vaillant_modulation",
    primary_command=0xB5,
    secondary_command=0x04,
    description="Vaillant modulation",
    fields=[
        FieldDefinition("modulation", 0, DataType.UINT8, unit="%"),
    ]
))

register_message(MessageDefinition(
    name="vaillant_pressure",
    primary_command=0xB5,
    secondary_command=0x16,
    description="Vaillant pressure",
    fields=[
        FieldDefinition("pressure", 0, DataType.DATA1C, unit="bar"),
    ]
))

def list_all_messages() -> List[str]:
    """Get list of all registered message names."""
    return [msg.name for msg in THELIA_MESSAGES.values()]