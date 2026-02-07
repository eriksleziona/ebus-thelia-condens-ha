"""
Thelia + MiPro parser with all sensors.
FIXED VERSION: Handles Instant Writes, Pump Status, Ghost Data Filtering, and Room Temp Priority.
"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram
from .messages import MessageDefinition, get_message_definition

EBUS_ADDRESSES = {
    0x00: "broadcast_0",
    0x08: "boiler",
    0x10: "mipro",
    0x15: "room_unit",
    0xFE: "broadcast",
}


def get_device_name(addr: int) -> str:
    return EBUS_ADDRESSES.get(addr, f"device_{addr:02X}")


@dataclass
class ParsedMessage:
    name: str
    timestamp: datetime
    source: int
    destination: int
    source_name: str
    dest_name: str
    command: tuple
    query_data: Dict[str, Any] = field(default_factory=dict)
    response_data: Dict[str, Any] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)
    raw_telegram: Optional[EbusTelegram] = None

    def __repr__(self) -> str:
        parts = []
        all_data = {**self.query_data, **self.response_data}
        for k, v in all_data.items():
            if v is None:
                continue
            unit = self.units.get(k, "")
            if isinstance(v, float):
                parts.append(f"{k}={v:.1f}{unit}")
            elif isinstance(v, bool):
                parts.append(f"{k}={'ON' if v else 'OFF'}")
            else:
                parts.append(f"{k}={v}{unit}")
        direction = f"{self.source_name}→{self.dest_name}"
        return f"{self.name} [{direction}]: {', '.join(parts)}"

    def get(self, key: str, default=None) -> Any:
        if key in self.response_data and self.response_data[key] is not None:
            return self.response_data[key]
        return self.query_data.get(key, default)


class TheliaParser:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._callbacks: List[Callable[[ParsedMessage], None]] = []
        self.stats = {"total": 0, "parsed": 0, "unknown": 0}

    def register_callback(self, callback: Callable[[ParsedMessage], None]) -> None:
        self._callbacks.append(callback)

    def _notify(self, message: ParsedMessage) -> None:
        for cb in self._callbacks:
            try:
                cb(message)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    def parse(self, telegram: EbusTelegram) -> ParsedMessage:
        self.stats["total"] += 1
        ts = datetime.fromtimestamp(telegram.timestamp)

        source_name = get_device_name(telegram.source)
        dest_name = get_device_name(telegram.destination)

        msg_def = get_message_definition(telegram.primary_command, telegram.secondary_command)

        if not msg_def:
            self.stats["unknown"] += 1
            raw_resp = telegram.response_data.hex() if telegram.response_data else ""
            msg = ParsedMessage(
                name="unknown",
                timestamp=ts,
                source=telegram.source,
                destination=telegram.destination,
                source_name=source_name,
                dest_name=dest_name,
                command=telegram.command,
                query_data={"raw": telegram.data.hex()},
                response_data={"raw": raw_resp} if raw_resp else {},
                raw_telegram=telegram,
            )
            self._notify(msg)
            return msg

        query_values = {}
        response_values = {}
        units = {}

        # Decode Query Fields
        for field_def in msg_def.fields:
            value = field_def.decode(telegram.data)
            if value is not None:
                query_values[field_def.name] = value
                if field_def.unit:
                    units[field_def.name] = field_def.unit

        # Decode Response Fields
        if telegram.response_data and msg_def.response_fields:
            for field_def in msg_def.response_fields:
                value = field_def.decode(telegram.response_data)
                if value is not None:
                    response_values[field_def.name] = value
                    if field_def.unit:
                        units[field_def.name] = field_def.unit

        self.stats["parsed"] += 1

        msg = ParsedMessage(
            name=msg_def.name,
            timestamp=ts,
            source=telegram.source,
            destination=telegram.destination,
            source_name=source_name,
            dest_name=dest_name,
            command=telegram.command,
            query_data=query_values,
            response_data=response_values,
            units=units,
            raw_telegram=telegram,
        )

        self._notify(msg)
        return msg

    def get_stats(self) -> Dict[str, int]:
        return dict(self.stats)


class DataAggregator:
    """
    Aggregates sensor values for Thelia Condens + MiPro.
    FIXED: Prioritizes B509 (Thermostat) for Room Temp and ignores B511 (Boiler) if 0.0.
    """

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        if message.name in ("unknown", "device_id"):
            return

        telegram = message.raw_telegram
        if telegram is None:
            return

        self._extract_sensors(message, telegram)

    def _extract_sensors(self, msg: ParsedMessage, telegram: EbusTelegram) -> None:
        ts = msg.timestamp
        data = telegram.data or b''
        resp = telegram.response_data or b''

        # === B511: Status/Temps ===
        if msg.name == "status_temps" and len(data) >= 1:
            query_type = data[0]

            if query_type == 1 and len(resp) >= 6:
                # Type 1: Live Temperatures
                if resp[0] != 0xFF:
                    self._set_sensor("boiler.flow_temperature", resp[0] / 2.0, "°C", ts,
                                   "Flow temperature", min_v=5.0, max_v=95.0)

                if resp[1] != 0xFF:
                    self._set_sensor("boiler.return_temperature", resp[1] / 2.0, "°C", ts,
                                   "Return temperature", min_v=5.0, max_v=95.0)

                # DHW Tank (Try Byte 5 first, then Byte 2)
                if resp[5] != 0xFF:
                    self._set_sensor("boiler.dhw_tank_temperature", resp[5] / 2.0, "°C", ts,
                                   "DHW Cylinder Temp", min_v=5.0, max_v=85.0)
                elif resp[2] != 0xFF:
                    self._set_sensor("boiler.dhw_tank_temperature", resp[2] / 2.0, "°C", ts,
                                   "DHW Cylinder Temp (Aux)", min_v=5.0, max_v=85.0)

                # Calc Delta T (Only if we have valid Flow/Return)
                flow_val = self.get_sensor("boiler.flow_temperature")
                ret_val = self.get_sensor("boiler.return_temperature")
                if flow_val is not None and ret_val is not None:
                    delta = flow_val - ret_val
                    self._set_sensor("boiler.delta_t", round(delta, 1), "°C", ts, "Flow-Return Delta")

            elif query_type == 0 and len(resp) >= 8:
                # Type 0: Status/Pressure/State

                # --- FIX: Only accept Room Temp from Boiler if > 1.0 (Ignores 0.0) ---
                if resp[3] != 0xFF:
                    self._set_sensor("mipro.room_temperature", resp[3] / 2.0, "°C", ts,
                                   "Room Temperature (Boiler Reading)", min_v=1.0, max_v=40.0)

                # Pump Status (from State Code Byte 4)
                if resp[4] != 0xFF:
                    state_code = resp[4]
                    # Common Saunier Duval States:
                    # S.00 (0) = Standby
                    # S.02-S.08 = Heating (Pump Running)
                    # S.10-S.17 = DHW (Pump Running)
                    pump_running = state_code in [2, 3, 4, 5, 6, 7, 8, 10, 14, 17]
                    state_str = "ON" if pump_running else "OFF"

                    self._set_sensor("boiler.pump_status", state_str, "", ts, f"Pump State (S.{state_code:02d})")

                # SANITY CHECK: Water Pressure (0.0 to 3.5 bar)
                if resp[2] != 0xFF:
                    self._set_sensor("boiler.water_pressure", resp[2] / 10.0, "bar", ts,
                                   "Water Pressure", min_v=0.0, max_v=3.5)

                if resp[7] != 0xFF:
                    ext_status = resp[7]
                    self._set_sensor("boiler.flame_on", bool(ext_status & 0x01), "", ts, "Burner Flame")
                    self._set_sensor("boiler.dhw_active", bool(ext_status & 0x04), "", ts, "DHW Mode")
                    self._set_sensor("boiler.heating_active", bool(ext_status & 0x80), "", ts, "Heating Mode")

            elif query_type == 2 and len(resp) >= 6:
                # Type 2: Setpoints
                if resp[0] != 0xFF:
                    self._set_sensor("boiler.burner_modulation", resp[0], "%", ts, "Modulation", min_v=0, max_v=100)

                if resp[1] != 0xFF:
                    self._set_sensor("boiler.outdoor_cutoff_internal", resp[1], "°C", ts,
                                   "Boiler Internal Cutoff (Ignored by MiPro)")

                if resp[2] != 0xFF:
                    self._set_sensor("mipro.max_flow_temp", resp[2] / 2.0, "°C", ts, "Max Flow Limit")

                if resp[3] != 0xFF:
                    self._set_sensor("boiler.dhw_setpoint_local", resp[3] / 2.0, "°C", ts, "Boiler Dial Setpoint")

                if resp[5] != 0xFF:
                    val = resp[5] / 2.0
                    if 30 <= val <= 75:
                        self._set_sensor("mipro.dhw_setpoint", val, "°C", ts, "DHW Setpoint (Active)")

        # === B512: INSTANT WRITE COMMAND ===
        elif msg.name == "param_write" and len(data) >= 2:
            param_id = data[0]
            val_raw = data[1]
            if param_id == 0x00:
                dhw_new = val_raw / 2.0
                if 30 <= dhw_new <= 75:
                    self._set_sensor("mipro.dhw_setpoint", dhw_new, "°C", ts, "DHW Setpoint (Instant Write)")

        # === B504: Outdoor ===
        elif msg.name == "modulation_outdoor":
            # Confirmed via debug dump: Bytes 8-9 contain outdoor temp
            if len(resp) >= 10:
                val = int.from_bytes(resp[8:10], 'little', signed=True) / 256.0
                self._set_sensor("boiler.outdoor_temperature", round(val, 1), "°C", ts,
                               "Outdoor Temp", min_v=-40.0, max_v=50.0)

        # === B509: Direct Room Temp (Primary Source) ===
        elif msg.name == "room_temp" and len(data) >= 2:
            if data[0] != 0xFF:
                # --- FIX: ALWAYS update if valid (Priority over Boiler) ---
                self._set_sensor("mipro.room_temperature", data[0] / 2.0, "°C", ts,
                               "Room Temperature (Direct)", min_v=1.0, max_v=40.0)

    def _set_sensor(self, name: str, value: Any, unit: str,
                   timestamp: datetime, description: str = "",
                   min_v: float = None, max_v: float = None) -> None:

        # Apply Sanity Checks
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if min_v is not None and value < min_v:
                return
            if max_v is not None and value > max_v:
                return

        self._sensors[name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp,
            "description": description,
        }

    def get_sensor(self, name: str) -> Optional[Any]:
        if name not in self._sensors:
            return None
        data = self._sensors[name]
        age = (datetime.now() - data["timestamp"]).total_seconds()
        if age > self.max_age:
            return None
        return data["value"]

    def get_all_sensors(self) -> Dict[str, Dict]:
        result = {}
        now = datetime.now()
        for name, data in self._sensors.items():
            age = (now - data["timestamp"]).total_seconds()
            if age <= self.max_age:
                result[name] = {
                    "value": data["value"],
                    "unit": data["unit"],
                    "age_seconds": round(age, 1),
                    "description": data.get("description", ""),
                }
        return result

    def print_status(self) -> None:
        sensors = self.get_all_sensors()
        print("\n" + "=" * 70)
        print("📊 HEATING SYSTEM STATUS (Fixed)")
        print("=" * 70)

        boiler = {k.replace("boiler.", ""): v for k, v in sensors.items() if k.startswith("boiler.")}
        mipro = {k.replace("mipro.", ""): v for k, v in sensors.items() if k.startswith("mipro.")}

        if boiler:
            print("\n🔥 BOILER:")
            for k, v in boiler.items():
                self._print_sensor(k, v)

        if mipro:
            print("\n📱 MIPRO:")
            for k, v in mipro.items():
                self._print_sensor(k, v)
        print("\n" + "=" * 70)

    def _print_sensor(self, name: str, data: Dict) -> None:
        val = data["value"]
        unit = data["unit"]
        desc = data.get("description", "")
        if isinstance(val, bool):
            val_str = "✅ YES" if val else "❌ NO"
        else:
            val_str = f"{val}{unit}"
        print(f"   {name:25s}: {val_str:10s} | {desc}")