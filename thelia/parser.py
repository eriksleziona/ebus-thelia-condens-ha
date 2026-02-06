"""
Thelia + MiPro parser with all sensors including DHW tank.
For system boiler with storage cylinder.
FIXED VERSION: Corrected Byte 5 Mapping for DHW Setpoint.
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
    FIXED MAPPING: DHW Setpoint is at Byte 5 for MiPro.
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
                    flow = resp[0] / 2.0
                    if 0 < flow < 100:
                        self._set_sensor("boiler.flow_temperature", round(flow, 1), "°C", ts,
                                         "Flow temperature (Vorlauf)")

                if resp[1] != 0xFF:
                    ret = resp[1] / 2.0
                    if 0 < ret < 100:
                        self._set_sensor("boiler.return_temperature", round(ret, 1), "°C", ts,
                                         "Return temperature (Rücklauf)")

                # DHW Tank (Storage) Temperature
                # Byte 2 is often a secondary temp (Outdoor/Tank)
                if resp[2] != 0xFF:
                    temp_aux = resp[2] / 2.0
                    if 10 <= temp_aux <= 85:
                        self._set_sensor("boiler.storage_temperature_aux", round(temp_aux, 1), "°C", ts,
                                         "DHW Storage (Aux Sensor)")

                # Byte 5 is the primary Tank temp (from your logs: 0x51 -> 40.5C)
                if resp[5] != 0xFF:
                    dhw_tank = resp[5] / 2.0
                    if 10 <= dhw_tank <= 85:
                        self._set_sensor("boiler.dhw_tank_temperature", round(dhw_tank, 1), "°C", ts,
                                         "DHW Cylinder Temperature")

                # Calculate Delta T
                flow_val = self.get_sensor("boiler.flow_temperature")
                ret_val = self.get_sensor("boiler.return_temperature")
                if flow_val and ret_val:
                    delta = flow_val - ret_val
                    self._set_sensor("boiler.delta_t", round(delta, 1), "°C", ts,
                                     "Flow - Return Delta")
                    self._set_sensor("boiler.condensing_possible", ret_val < 55.0, "", ts,
                                     "Condensing Mode Active")

            elif query_type == 0 and len(resp) >= 8:
                # Type 0: Extended status with pressure
                if resp[2] != 0xFF:
                    pressure = resp[2] / 10.0
                    if 0.1 <= pressure <= 4.0:
                        self._set_sensor("boiler.water_pressure", round(pressure, 1), "bar", ts,
                                         "Water Pressure")

                if resp[7] != 0xFF:
                    ext_status = resp[7]
                    self._set_sensor("boiler.flame_on", bool(ext_status & 0x01), "", ts, "Burner ON")
                    self._set_sensor("boiler.pump_running", bool(ext_status & 0x02), "", ts, "Pump ON")
                    self._set_sensor("boiler.dhw_active", bool(ext_status & 0x04), "", ts, "DHW Mode")
                    self._set_sensor("boiler.heating_active", bool(ext_status & 0x80), "", ts, "Heating Mode")

            elif query_type == 2 and len(resp) >= 6:  # REQUIRED: Check length >= 6 for byte 5
                # Type 2: Setpoints
                if resp[0] != 0xFF and resp[0] <= 100:
                    self._set_sensor("boiler.burner_modulation", resp[0], "%", ts,
                                     "Modulation Level")

                # Outdoor cutoff
                if resp[1] != 0xFF and 5 <= resp[1] <= 30:
                    self._set_sensor("mipro.outdoor_cutoff", resp[1], "°C", ts,
                                     "Summer/Winter Threshold")

                # Max flow temperature
                if resp[2] != 0xFF:
                    max_flow = resp[2] / 2.0
                    if 40 <= max_flow <= 90:
                        self._set_sensor("mipro.max_flow_temp", round(max_flow, 1), "°C", ts,
                                         "Max Flow Limit")

                # === FIXED: DHW Setpoints ===

                # Byte 3: Boiler Internal/Local Setpoint (0x5A = 45C in your log)
                if resp[3] != 0xFF:
                    dhw_local = resp[3] / 2.0
                    self._set_sensor("boiler.dhw_setpoint_local", round(dhw_local, 1), "°C", ts,
                                     "DHW Setpoint (Boiler Dial)")

                # Byte 5: MiPro Active Setpoint (0x64 = 50C in your log)
                if resp[5] != 0xFF:
                    dhw_sp = resp[5] / 2.0
                    # Sanity check: 30-75C
                    if 30 <= dhw_sp <= 75:
                        self._set_sensor("mipro.dhw_setpoint", round(dhw_sp, 1), "°C", ts,
                                         "DHW Setpoint (MiPro Active)")

        # === B504: Modulation and Outdoor Temperature ===
        elif msg.name == "modulation_outdoor" and len(resp) >= 4:
            if resp[0] != 0xFF and resp[0] <= 100:
                self._set_sensor("boiler.burner_modulation", resp[0], "%", ts, "Modulation Level")

            # Outdoor temperature
            # Try 16-bit first
            if len(resp) >= 10:
                outdoor_raw = int.from_bytes(resp[8:10], 'little', signed=True)
                if outdoor_raw not in (-1, 32767, -32768):
                    outdoor = outdoor_raw / 256.0
                    if -40 <= outdoor <= 50:
                        self._set_sensor("boiler.outdoor_temperature", round(outdoor, 1), "°C", ts, "Outdoor Temp")
                        return

            # Fallback to Byte 1 (Data2c) if 16-bit failed or packet short
            # Your messages.py defines this as 'outdoor_temp_backup'
            if resp[1] != 0xFF:
                # Some firmwares send signed byte/2 here
                val = int.from_bytes([resp[1]], 'little', signed=True) / 2.0
                if -40 <= val <= 50:
                    self._set_sensor("boiler.outdoor_temperature", round(val, 1), "°C", ts, "Outdoor Temp (Backup)")

        # === B509: Room Temperature ===
        elif msg.name == "room_temp" and len(data) >= 2:
            if data[0] != 0xFF:
                room = data[0] / 2.0
                if 5 <= room <= 40:
                    self._set_sensor("mipro.room_temperature", round(room, 1), "°C", ts, "Room Temp")

            if data[1] != 0xFF and data[1] != 0x7F:
                adj = int.from_bytes([data[1]], 'little', signed=True)
                if -10 <= adj <= 10:
                    self._set_sensor("mipro.room_setpoint_adjust", adj, "", ts, "Room Adjust")

        # === B516: DateTime ===
        elif msg.name == "datetime" and len(data) >= 8:
            flags = data[0]
            if flags == 0:
                def bcd(b):
                    return ((b >> 4) & 0xF) * 10 + (b & 0xF)

                try:
                    h, m, s = bcd(data[3]), bcd(data[2]), bcd(data[1])
                    if h < 24 and m < 60:
                        self._set_sensor("mipro.time", f"{h:02d}:{m:02d}:{s:02d}", "", ts)
                except:
                    pass

                try:
                    D, M, Y = bcd(data[4]), bcd(data[5]), bcd(data[7])
                    if 1 <= M <= 12 and 1 <= D <= 31:
                        self._set_sensor("mipro.date", f"20{Y:02d}-{M:02d}-{D:02d}", "", ts)
                except:
                    pass

    def _set_sensor(self, name: str, value: Any, unit: str,
                    timestamp: datetime, description: str = "") -> None:
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
        print("📊 HEATING SYSTEM STATUS (MiPro Corrected)")
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