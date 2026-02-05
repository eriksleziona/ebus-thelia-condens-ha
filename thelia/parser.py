"""
Thelia + MiPro parser with corrected value interpretation.
Based on user-provided decoding table.
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
                continue  # Skip None values
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
    Aggregates sensor values using corrected decoding:

    Based on user-provided table:
    - Flow Temp: B511 query_type=1, response byte 0 ÷ 2
    - Return Temp: B511 query_type=1, response byte 1 ÷ 2
    - Water Pressure: B511 query_type=1, response byte 6 ÷ 10
    - Outdoor Temp: B504 response bytes 1&2 ÷ 256 (signed)
    - DHW Setpoint: B510 byte 3 ÷ 2 (if not 0xFF)
    - Target Flow: B510 byte 2 ÷ 2
    - Burner Modulation: B511 query_type=2, response byte 0
    - Room Temp: B509 byte 0 ÷ 2
    - Flame Status: B511 query_type=1, response byte 8 bit 0
    - Pump Status: B511 query_type=1, response byte 8 bit 1
    """

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        if message.name in ("unknown", "device_id"):
            return
        self._extract_sensors(message)

    def _extract_sensors(self, msg: ParsedMessage) -> None:
        ts = msg.timestamp
        resp = msg.response_data
        query = msg.query_data

        # === status_temps (B511) ===
        if msg.name == "status_temps":
            query_type = query.get("query_type", -1)

            if query_type == 1:
                # Type 1: Flow/Return temps, pressure, status flags
                # Response: byte0=flow/2, byte1=return/2, ..., byte6=pressure/10, ..., byte8=flags

                byte0 = resp.get("byte0")
                if byte0 is not None and byte0 != 255:
                    flow_temp = byte0 / 2.0
                    if 0 < flow_temp < 100:
                        self._set_sensor("boiler.flow_temperature", round(flow_temp, 1), "°C", ts,
                                        "Flow temperature (Vorlauf)")

                byte1 = resp.get("byte1")
                if byte1 is not None and byte1 != 255:
                    return_temp = byte1 / 2.0
                    if 0 < return_temp < 100:
                        self._set_sensor("boiler.return_temperature", round(return_temp, 1), "°C", ts,
                                        "Return temperature (Rücklauf)")

                # Pressure: byte 6 ÷ 10
                byte6 = resp.get("byte6")
                if byte6 is not None and byte6 != 255:
                    pressure = byte6 / 10.0
                    if 0.1 <= pressure <= 4.0:
                        self._set_sensor("boiler.water_pressure", round(pressure, 1), "bar", ts,
                                        "System water pressure")

                # Status flags in byte 8 (or last byte)
                byte8 = resp.get("byte8")
                if byte8 is not None:
                    self._set_sensor("boiler.flame_on", bool(byte8 & 0x01), "", ts, "Burner flame")
                    self._set_sensor("boiler.pump_running", bool(byte8 & 0x02), "", ts, "Pump running")

                # Status byte (byte 5)
                status = resp.get("status_byte")
                if status is not None:
                    self._set_sensor("boiler.status_code", status, "", ts)
                    # Try additional status decoding
                    self._set_sensor("boiler.heating_demand", bool(status & 0x10), "", ts, "Heating demand")
                    self._set_sensor("boiler.dhw_demand", bool(status & 0x80), "", ts, "DHW demand")

            elif query_type == 2:
                # Type 2: Modulation data
                byte0 = resp.get("byte0")
                if byte0 is not None and byte0 != 255:
                    if 0 <= byte0 <= 100:
                        self._set_sensor("boiler.burner_modulation", byte0, "%", ts,
                                        "Burner modulation level")

            elif query_type == 0:
                # Type 0: Extended status - possibly contains other temps
                pass

        # === modulation_outdoor (B504) ===
        elif msg.name == "modulation_outdoor":
            # Modulation: byte 0
            mod = resp.get("modulation")
            if mod is not None and mod != 255:
                if 0 <= mod <= 100:
                    self._set_sensor("boiler.burner_modulation", mod, "%", ts,
                                    "Burner modulation level")

            # Outdoor temp: bytes 1-2 as signed int16 ÷ 256
            outdoor_raw = resp.get("outdoor_temp_raw")
            if outdoor_raw is not None and outdoor_raw != -1 and outdoor_raw != 32767:
                outdoor = outdoor_raw / 256.0
                if -40 <= outdoor <= 50:
                    self._set_sensor("boiler.outdoor_temperature", round(outdoor, 1), "°C", ts,
                                    "Outdoor temperature")

        # === temp_setpoint (B510) ===
        elif msg.name == "temp_setpoint":
            mode1 = query.get("mode1", 255)

            # Main setpoint (mode1=0)
            if mode1 == 0:
                # Target flow temp: byte 2 ÷ 2
                target = query.get("target_flow_temp")
                if target is not None and 20 <= target <= 80:
                    self._set_sensor("mipro.target_flow_temp", target, "°C", ts,
                                    "Target flow temperature")

                # DHW setpoint: byte 3 ÷ 2 (if valid)
                dhw = query.get("dhw_setpoint")
                if dhw is not None and 30 <= dhw <= 65:
                    self._set_sensor("mipro.dhw_setpoint", dhw, "°C", ts,
                                    "DHW temperature setpoint")

        # === room_temp (B509) ===
        elif msg.name == "room_temp":
            room = query.get("room_temp")
            if room is not None and 5 <= room <= 35:
                self._set_sensor("mipro.room_temperature", room, "°C", ts,
                                "Room temperature")

            adj = query.get("room_setpoint_adjust")
            if adj is not None and adj != 127 and adj != -128:
                self._set_sensor("mipro.room_setpoint_adjust", adj, "", ts,
                                "Room setpoint adjustment")

        # === datetime (B516) ===
        elif msg.name == "datetime":
            flags = query.get("flags", 255)

            if flags == 0:  # Full datetime message
                h = query.get("hours")
                m = query.get("minutes")
                s = query.get("seconds")

                if h is not None and m is not None and s is not None:
                    if h < 24 and m < 60 and s < 60:
                        self._set_sensor("mipro.time", f"{h:02d}:{m:02d}:{s:02d}", "", ts)

                day = query.get("day")
                month = query.get("month")
                year = query.get("year")

                if day and month and year is not None:
                    if 1 <= day <= 31 and 1 <= month <= 12:
                        year_full = 2000 + year if year < 100 else year
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
            print("   ── Temperatures ──")
            for k in ["flow_temperature", "return_temperature", "outdoor_temperature"]:
                if k in boiler:
                    self._print_sensor(k, boiler[k])

            # Pressure
            if "water_pressure" in boiler:
                print("   ── Pressure ──")
                self._print_sensor("water_pressure", boiler["water_pressure"])

            # Modulation
            if "burner_modulation" in boiler:
                print("   ── Burner ──")
                self._print_sensor("burner_modulation", boiler["burner_modulation"])

            # Status
            print("   ── Status ──")
            for k in ["flame_on", "pump_running", "heating_demand", "dhw_demand"]:
                if k in boiler:
                    self._print_sensor(k, boiler[k])

            if "status_code" in boiler:
                print(f"   status_code              : {boiler['status_code']['value']} (raw hex: 0x{boiler['status_code']['value']:02X})")

        if mipro:
            print("\n📱 MIPRO Controller:")
            for k in ["room_temperature", "target_flow_temp", "dhw_setpoint", "room_setpoint_adjust", "time", "date"]:
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