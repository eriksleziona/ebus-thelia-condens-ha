"""Thelia + MiPro parser with comprehensive sensor extraction."""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ebus_core.telegram import EbusTelegram
from .messages import MessageDefinition, get_message_definition


# eBus Address mapping
EBUS_ADDRESSES = {
    0x00: "broadcast_0",
    0x08: "boiler",
    0x10: "mipro",
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
            else:
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
    """Aggregates sensor values from MiPro and Boiler."""

    def __init__(self, max_age: float = 300.0):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, message: ParsedMessage) -> None:
        if message.name == "unknown":
            return
        self._extract_sensors(message)

    def _extract_sensors(self, msg: ParsedMessage) -> None:
        ts = msg.timestamp
        source = msg.source_name

        # === status_temps (B511) ===
        if msg.name == "status_temps":
            query_type = msg.query_data.get("query_type", -1)

            if query_type == 1:
                # Flow temperature (from boiler response)
                if "temp1" in msg.response_data:
                    self._set_sensor("boiler.flow_temperature",
                                    msg.response_data["temp1"], "°C", ts,
                                    "Actual flow water temperature")
                if "status_byte" in msg.response_data:
                    status = msg.response_data["status_byte"]
                    self._set_sensor("boiler.status_code", status, "", ts)
                    # Decode status bits
                    self._set_sensor("boiler.heating_active", bool(status & 0x01), "", ts)
                    self._set_sensor("boiler.dhw_active", bool(status & 0x02), "", ts)
                    self._set_sensor("boiler.flame_on", bool(status & 0x04), "", ts)
                    self._set_sensor("boiler.pump_running", bool(status & 0x10), "", ts)

            elif query_type == 2:
                # Setpoints (outdoor cutoff threshold)
                if "temp1" in msg.response_data:
                    self._set_sensor("mipro.outdoor_cutoff",
                                    msg.response_data["temp1"], "°C", ts,
                                    "Outdoor cutoff temperature setting")

            elif query_type == 0:
                # Extended status (may contain outdoor temp)
                if "temp1" in msg.response_data:
                    temp = msg.response_data["temp1"]
                    if -40 <= temp <= 50:
                        self._set_sensor("boiler.outdoor_temperature", temp, "°C", ts,
                                        "Actual outdoor temperature")

        # === temp_setpoint (B510) ===
        elif msg.name == "temp_setpoint":
            mode1 = msg.query_data.get("mode1", 0)
            if mode1 == 0 and "flow_setpoint" in msg.query_data:
                setpoint = msg.query_data["flow_setpoint"]
                if 20 <= setpoint <= 80:  # Valid range
                    self._set_sensor("mipro.flow_setpoint", setpoint, "°C", ts,
                                    "Requested flow temperature setpoint")

        # === modulation (B504) ===
        elif msg.name == "modulation":
            if "modulation" in msg.response_data:
                self._set_sensor("boiler.burner_modulation",
                                msg.response_data["modulation"], "%", ts,
                                "Burner modulation level")

        # === room_temp (B509) from MiPro ===
        elif msg.name == "room_temp":
            if "room_temp" in msg.query_data:
                self._set_sensor("mipro.room_temperature",
                                msg.query_data["room_temp"], "°C", ts,
                                "Room temperature from MiPro")
            if "room_setpoint_adjust" in msg.query_data:
                adj = msg.query_data["room_setpoint_adjust"]
                if adj != 0:
                    self._set_sensor("mipro.room_setpoint_adjust", adj, "", ts,
                                    "Room setpoint adjustment")

        # === datetime (B516) from MiPro ===
        elif msg.name == "datetime":
            qd = msg.query_data
            flags = qd.get("flags", 0)

            if flags == 0 and len(qd) >= 7:
                hours = qd.get("hours", 0)
                minutes = qd.get("minutes", 0)
                seconds = qd.get("seconds", 0)

                if minutes < 60 and seconds < 60 and hours < 24:
                    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    self._set_sensor("mipro.time", time_str, "", ts)

                day = qd.get("day", 0)
                month = qd.get("month", 0)
                year = qd.get("year", 0)

                if 1 <= day <= 31 and 1 <= month <= 12:
                    year_full = 2000 + year if year < 100 else year
                    date_str = f"{year_full}-{month:02d}-{day:02d}"
                    self._set_sensor("mipro.date", date_str, "", ts)

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
        print("📊 SYSTEM STATUS")
        print("=" * 70)

        # Group by device
        boiler_sensors = {k: v for k, v in sensors.items() if k.startswith("boiler.")}
        mipro_sensors = {k: v for k, v in sensors.items() if k.startswith("mipro.")}
        other_sensors = {k: v for k, v in sensors.items()
                        if not k.startswith("boiler.") and not k.startswith("mipro.")}

        if boiler_sensors:
            print("\n🔥 BOILER (Thelia Condens):")
            self._print_sensor_group(boiler_sensors)

        if mipro_sensors:
            print("\n📱 MIPRO Controller:")
            self._print_sensor_group(mipro_sensors)

        if other_sensors:
            print("\n📈 Other:")
            self._print_sensor_group(other_sensors)

        print("\n" + "=" * 70)

    def _print_sensor_group(self, sensors: Dict[str, Dict]) -> None:
        for name, data in sorted(sensors.items()):
            # Remove prefix for display
            display_name = name.split(".", 1)[-1] if "." in name else name
            val = data["value"]
            unit = data["unit"]

            if isinstance(val, bool):
                val_str = "✅ ON" if val else "❌ OFF"
            elif isinstance(val, float):
                val_str = f"{val:.1f}{unit}"
            else:
                val_str = f"{val}{unit}"

            print(f"   {display_name:25s}: {val_str}")
            if data.get("description"):
                print(f"   {'':25s}  └─ {data['description']}")