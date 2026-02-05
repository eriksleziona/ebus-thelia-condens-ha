"""
Thelia + MiPro parser with all verified sensors including Delta T.
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

        for field_def in msg_def.fields:
            value = field_def.decode(telegram.data)
            if value is not None:
                query_values[field_def.name] = value
                if field_def.unit:
                    units[field_def.name] = field_def.unit

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
    Aggregates sensor values from verified byte positions:

    B511 type 1 response:
      byte[0] ÷ 2 = Flow temperature
      byte[1] ÷ 2 = Return temperature
      byte[5] = Status byte

    B511 type 0 response:
      byte[2] ÷ 10 = Water pressure
      byte[7] = Extended status (flame, pump, etc.)

    B511 type 2 response:
      byte[0] = Modulation %
      byte[1] = Outdoor cutoff setpoint

    B504 response:
      byte[0] = Modulation %
      bytes[8:9] int16_le ÷ 256 = Outdoor temperature

    B510 query:
      byte[2] ÷ 2 = Target flow temperature
      byte[3] ÷ 2 = DHW setpoint (if not 0xFF)

    B509 query:
      byte[0] ÷ 2 = Room temperature
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
                # Type 1: Flow temp, Return temp, Status
                flow = None
                ret = None

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

                # Calculate Delta T
                if flow is not None and ret is not None and flow > 0 and ret > 0:
                    delta_t = flow - ret
                    self._set_sensor("boiler.delta_t", round(delta_t, 1), "°C", ts,
                                     "Temperature difference (Flow - Return)")

                    # Condensing indicator: Return < 55°C means condensing is possible
                    is_condensing = ret < 55.0
                    self._set_sensor("boiler.condensing_possible", is_condensing, "", ts,
                                     "Return temp allows condensation (<55°C)")

                # Status byte at position 5
                if resp[5] != 0xFF:
                    status = resp[5]
                    self._set_sensor("boiler.status_code", status, "", ts)

            elif query_type == 0 and len(resp) >= 8:
                # Type 0: Extended status with PRESSURE
                # byte[2] = pressure ÷ 10
                if resp[2] != 0xFF:
                    pressure = resp[2] / 10.0
                    if 0.1 <= pressure <= 4.0:
                        self._set_sensor("boiler.water_pressure", round(pressure, 1), "bar", ts,
                                         "System water pressure")

                # Extended status from byte 7
                if resp[7] != 0xFF:
                    ext_status = resp[7]
                    # 0x83 = 1000 0011: bit 0 = flame, bit 1 = pump, bit 7 = heating
                    self._set_sensor("boiler.flame_on", bool(ext_status & 0x01), "", ts,
                                     "Burner flame active")
                    self._set_sensor("boiler.pump_running", bool(ext_status & 0x02), "", ts,
                                     "Circulation pump running")
                    self._set_sensor("boiler.heating_active", bool(ext_status & 0x80), "", ts,
                                     "Heating mode active")

            elif query_type == 2 and len(resp) >= 2:
                # Type 2: Modulation and setpoints
                if resp[0] != 0xFF and resp[0] <= 100:
                    self._set_sensor("boiler.burner_modulation", resp[0], "%", ts,
                                     "Burner modulation level")

                if resp[1] != 0xFF:
                    cutoff = resp[1]
                    if 5 <= cutoff <= 30:
                        self._set_sensor("mipro.outdoor_cutoff", cutoff, "°C", ts,
                                         "Outdoor cutoff setpoint (heating off when outdoor > this)")

        # === B504: Modulation and OUTDOOR TEMP ===
        elif msg.name == "modulation_outdoor" and len(resp) >= 10:
            # byte[0] = modulation
            if resp[0] != 0xFF and resp[0] <= 100:
                self._set_sensor("boiler.burner_modulation", resp[0], "%", ts,
                                 "Burner modulation level")

            # Outdoor temperature from bytes 8-9 (int16_le ÷ 256)
            outdoor_raw = int.from_bytes(resp[8:10], 'little', signed=True)
            if outdoor_raw != -1 and outdoor_raw != 32767 and outdoor_raw != -32768:
                outdoor = outdoor_raw / 256.0
                if -40 <= outdoor <= 50:
                    self._set_sensor("boiler.outdoor_temperature", round(outdoor, 1), "°C", ts,
                                     "Outdoor temperature")

        # === B510: Setpoints ===
        elif msg.name == "temp_setpoint" and len(data) >= 4:
            mode1 = data[0]

            if mode1 == 0:
                # byte[2] = target flow temp ÷ 2
                if data[2] != 0xFF:
                    target = data[2] / 2.0
                    if 20 <= target <= 80:
                        self._set_sensor("mipro.target_flow_temp", round(target, 1), "°C", ts,
                                         "Target flow temperature")

                # byte[3] = DHW setpoint ÷ 2 (if valid)
                if data[3] != 0xFF:
                    dhw = data[3] / 2.0
                    if 30 <= dhw <= 65:
                        self._set_sensor("mipro.dhw_setpoint", round(dhw, 1), "°C", ts,
                                         "DHW temperature setpoint")

        # === B509: Room Temp ===
        elif msg.name == "room_temp" and len(data) >= 2:
            if data[0] != 0xFF:
                room = data[0] / 2.0
                if 5 <= room <= 35:
                    self._set_sensor("mipro.room_temperature", round(room, 1), "°C", ts,
                                     "Room temperature")

            if data[1] != 0xFF and data[1] != 0x7F:
                adj = int.from_bytes([data[1]], 'little', signed=True)
                if -10 <= adj <= 10:
                    self._set_sensor("mipro.room_setpoint_adjust", adj, "", ts,
                                     "Room setpoint adjustment")

        # === B516: DateTime ===
        elif msg.name == "datetime" and len(data) >= 8:
            flags = data[0]

            if flags == 0:
                def bcd(b):
                    high = (b >> 4) & 0x0F
                    low = b & 0x0F
                    if high > 9 or low > 9:
                        return None
                    return high * 10 + low

                s = bcd(data[1])
                m = bcd(data[2])
                h = bcd(data[3])

                if s is not None and m is not None and h is not None:
                    if h < 24 and m < 60 and s < 60:
                        self._set_sensor("mipro.time", f"{h:02d}:{m:02d}:{s:02d}", "", ts)

                day = bcd(data[4])
                month = bcd(data[5])
                year = bcd(data[7])

                if day and month and year is not None:
                    if 1 <= day <= 31 and 1 <= month <= 12:
                        year_full = 2000 + year
                        self._set_sensor("mipro.date", f"{year_full}-{month:02d}-{day:02d}", "", ts)

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
        print("📊 HEATING SYSTEM STATUS")
        print("=" * 70)

        boiler = {k.replace("boiler.", ""): v for k, v in sensors.items() if k.startswith("boiler.")}
        mipro = {k.replace("mipro.", ""): v for k, v in sensors.items() if k.startswith("mipro.")}

        if boiler:
            print("\n🔥 BOILER (Thelia Condens):")

            # Temperatures
            temp_order = ["flow_temperature", "return_temperature", "delta_t", "outdoor_temperature"]
            temps_found = [k for k in temp_order if k in boiler]
            if temps_found:
                print("   ── Temperatures ──")
                for k in temps_found:
                    self._print_sensor(k, boiler[k])

            # Condensing
            if "condensing_possible" in boiler:
                print("   ── Efficiency ──")
                self._print_sensor("condensing_possible", boiler["condensing_possible"])

            # Pressure
            if "water_pressure" in boiler:
                print("   ── Pressure ──")
                self._print_sensor("water_pressure", boiler["water_pressure"])

            # Modulation
            if "burner_modulation" in boiler:
                print("   ── Burner ──")
                self._print_sensor("burner_modulation", boiler["burner_modulation"])

            # Status flags
            status_keys = ["flame_on", "pump_running", "heating_active"]
            status_found = [k for k in status_keys if k in boiler]
            if status_found:
                print("   ── Status ──")
                for k in status_found:
                    self._print_sensor(k, boiler[k])

            if "status_code" in boiler:
                val = boiler['status_code']['value']
                print(f"   status_code              : {val} (0x{val:02X})")

        if mipro:
            print("\n📱 MIPRO Controller:")
            order = ["room_temperature", "target_flow_temp", "dhw_setpoint",
                     "outdoor_cutoff", "room_setpoint_adjust", "time", "date"]
            for k in order:
                if k in mipro:
                    self._print_sensor(k, mipro[k])

        print("\n" + "=" * 70)

    def _print_sensor(self, name: str, data: Dict) -> None:
        val = data["value"]
        unit = data["unit"]
        desc = data.get("description", "")

        if isinstance(val, bool):
            val_str = "✅ YES" if val else "❌ NO"
        elif isinstance(val, float):
            val_str = f"{val:.1f}{unit}"
        else:
            val_str = f"{val}{unit}"

        print(f"   {name:25s}: {val_str}")
        if desc:
            print(f"   {' ':25s}  └─ {desc}")