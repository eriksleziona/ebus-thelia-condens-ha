"""
Thelia + MiPro parser with corrected sensor extraction.
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
    0x18: "controller_2",
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
            unit = self.units.get(k, "")
            if isinstance(v, float):
                parts.append(f"{k}={v:.1f}{unit}")
            elif isinstance(v, bool):
                parts.append(f"{k}={'ON' if v else 'OFF'}")
            elif v is not None:
                parts.append(f"{k}={v}{unit}")
        direction = f"{self.source_name}→{self.dest_name}"
        return f"{self.name} [{direction}]: {', '.join(parts)}"

    def get(self, key: str, default=None) -> Any:
        if key in self.response_data:
            return self.response_data[key]
        return self.query_data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source_name,
            "destination": self.dest_name,
            "command": f"{self.command[0]:02X}{self.command[1]:02X}",
            "query": self.query_data,
            "response": self.response_data,
            "units": self.units,
        }


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
    """Aggregates sensor values with corrected interpretation."""

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

        # === status_temps (B511) ===
        if msg.name == "status_temps":
            query_type = msg.query_data.get("query_type", -1)

            if query_type == 1:
                # Query type 1: Flow and Return temperatures
                if "flow_temp" in msg.response_data:
                    flow = msg.response_data["flow_temp"]
                    if 0 < flow < 100:  # Valid range
                        self._set_sensor("boiler.flow_temperature", flow, "°C", ts,
                                        "Flow temperature (Vorlauf)")

                if "return_temp" in msg.response_data:
                    ret = msg.response_data["return_temp"]
                    if 0 < ret < 100:  # Valid range
                        self._set_sensor("boiler.return_temperature", ret, "°C", ts,
                                        "Return temperature (Rücklauf)")

                # Status byte - decode status flags
                if "status_byte" in msg.response_data:
                    status = msg.response_data["status_byte"]
                    self._set_sensor("boiler.status_code", status, "", ts)

                    # Decode individual bits (verify against your system!)
                    # Bit 0: Flame
                    # Bit 1: Pump
                    # Bit 4: Heating demand
                    # Bit 7: DHW demand
                    self._set_sensor("boiler.flame_on", bool(status & 0x01), "", ts,
                                    "Burner flame active")
                    self._set_sensor("boiler.pump_running", bool(status & 0x02), "", ts,
                                    "Circulation pump running")
                    self._set_sensor("boiler.heating_active", bool(status & 0x10), "", ts,
                                    "Heating mode active")
                    self._set_sensor("boiler.dhw_active", bool(status & 0x80), "", ts,
                                    "DHW mode active")

                # Pressure from byte 6 (if valid)
                if "pressure_raw" in msg.response_data:
                    praw = msg.response_data["pressure_raw"]
                    # Pressure should be 0.5 - 3.0 bar typically
                    pressure = praw / 10.0
                    if 0.1 <= pressure <= 5.0:
                        self._set_sensor("boiler.water_pressure", round(pressure, 1), "bar", ts,
                                        "System water pressure")

            elif query_type == 2:
                # Query type 2: Modulation
                if "flow_temp" in msg.response_data:
                    # In type 2, first byte might be modulation
                    mod = msg.response_data.get("flow_temp", 0)
                    # Actually this might be different - need to check raw data
                    pass

        # === modulation_outdoor (B504) ===
        elif msg.name == "modulation_outdoor":
            if "modulation" in msg.response_data:
                mod = msg.response_data["modulation"]
                if 0 <= mod <= 100:
                    self._set_sensor("boiler.burner_modulation", mod, "%", ts,
                                    "Burner modulation level")

            if "outdoor_temp" in msg.response_data:
                outdoor = msg.response_data["outdoor_temp"]
                # Valid outdoor range: -40 to +50
                if outdoor is not None and -40 <= outdoor <= 50:
                    self._set_sensor("boiler.outdoor_temperature", outdoor, "°C", ts,
                                    "Outdoor temperature sensor")

        # === temp_setpoint (B510) ===
        elif msg.name == "temp_setpoint":
            mode1 = msg.query_data.get("mode1", 255)

            # Main setpoint messages have mode1=0
            if mode1 == 0:
                if "target_flow_temp" in msg.query_data:
                    target = msg.query_data["target_flow_temp"]
                    if 20 <= target <= 80:
                        self._set_sensor("mipro.target_flow_temp", target, "°C", ts,
                                        "Target flow temperature setpoint")

                if "dhw_setpoint" in msg.query_data:
                    dhw = msg.query_data["dhw_setpoint"]
                    if 30 <= dhw <= 65:  # DHW range
                        self._set_sensor("mipro.dhw_setpoint", dhw, "°C", ts,
                                        "DHW temperature setpoint")

        # === room_temp (B509) from MiPro ===
        elif msg.name == "room_temp":
            if "room_temp" in msg.query_data:
                room = msg.query_data["room_temp"]
                if 5 <= room <= 35:  # Valid room temp range
                    self._set_sensor("mipro.room_temperature", room, "°C", ts,
                                    "Room temperature")

            if "room_setpoint_adjust" in msg.query_data:
                adj = msg.query_data["room_setpoint_adjust"]
                if adj != 0:
                    self._set_sensor("mipro.room_setpoint_adjust", adj, "", ts,
                                    "Room setpoint adjustment")

        # === datetime (B516) ===
        elif msg.name == "datetime":
            qd = msg.query_data
            flags = qd.get("flags", 255)

            if flags == 0:  # Full datetime
                h = qd.get("hours", 0)
                m = qd.get("minutes", 0)
                s = qd.get("seconds", 0)

                if h < 24 and m < 60 and s < 60:
                    self._set_sensor("mipro.time", f"{h:02d}:{m:02d}:{s:02d}", "", ts)

                day = qd.get("day", 0)
                month = qd.get("month", 0)
                year = qd.get("year", 0)

                if 1 <= day <= 31 and 1 <= month <= 12 and year < 100:
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

            # Temperatures first
            temp_keys = ["flow_temperature", "return_temperature", "outdoor_temperature"]
            for k in temp_keys:
                if k in boiler:
                    self._print_sensor(k, boiler[k])

            # Pressure
            if "water_pressure" in boiler:
                self._print_sensor("water_pressure", boiler["water_pressure"])

            # Modulation
            if "burner_modulation" in boiler:
                self._print_sensor("burner_modulation", boiler["burner_modulation"])

            # Status flags
            print("   --- Status ---")
            flag_keys = ["flame_on", "pump_running", "heating_active", "dhw_active"]
            for k in flag_keys:
                if k in boiler:
                    self._print_sensor(k, boiler[k])

            if "status_code" in boiler:
                print(f"   status_code              : {boiler['status_code']['value']} (raw)")

        if mipro:
            print("\n📱 MIPRO Controller:")
            for k, v in sorted(mipro.items()):
                self._print_sensor(k, v)

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