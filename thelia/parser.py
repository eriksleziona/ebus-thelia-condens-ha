"""
Thelia + MiPro parser with all sensors.
FIXED VERSION: Handles Instant Writes, Pump Status, Ghost Data Filtering, and Room Temp Priority.
"""

import logging
import json
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

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

    def __init__(
        self,
        max_age: float = 300.0,
        state_file: str = "config/runtime_state.json",
        flame_debounce_seconds: float = 8.0,
        status_stale_threshold_seconds: float = 120.0,
    ):
        self.max_age = max_age
        self._sensors: Dict[str, Dict] = {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self._state_file = Path(state_file) if state_file else None
        self._flame_debounce_seconds = max(0.0, flame_debounce_seconds)
        self._status_stale_threshold_seconds = max(1.0, status_stale_threshold_seconds)
        self._last_flame_state: Optional[bool] = None
        self._pending_flame_state: Optional[bool] = None
        self._pending_flame_since: Optional[datetime] = None
        self._burner_start_count = 0
        self._burner_runtime_total_s = 0.0
        self._burner_last_cycle_s = 0.0
        self._burner_start_events: List[datetime] = []
        self._last_flame_on: Optional[datetime] = None
        self._last_flame_off: Optional[datetime] = None
        self._active_cycle_started_at: Optional[datetime] = None
        self._last_telegram_at: Optional[datetime] = None
        self._last_status_at: Optional[datetime] = None
        self._last_modulation_update_at: Optional[datetime] = None
        self._last_live_modulation_at: Optional[datetime] = None
        self._modulation_source = "unknown"
        self._modulation_raw_hex = "0x00"

        self._load_runtime_state()

    def update(self, message: ParsedMessage) -> None:
        self._last_telegram_at = message.timestamp

        if message.name in ("unknown", "device_id"):
            self._publish_runtime_metrics(message.timestamp)
            return

        telegram = message.raw_telegram
        if telegram is None:
            self._publish_runtime_metrics(message.timestamp)
            return

        self._extract_sensors(message, telegram)
        self._publish_runtime_metrics(message.timestamp)

    def _to_iso8601(self, ts: datetime) -> str:
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        return ts.isoformat(timespec="seconds")

    def _parse_iso8601(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _load_runtime_state(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._burner_start_count = int(data.get("burner_start_count", 0))
            self._burner_runtime_total_s = float(data.get("burner_runtime_total_s", 0.0))
            self._burner_last_cycle_s = float(data.get("burner_last_cycle_s", 0.0))
            self._last_flame_on = self._parse_iso8601(data.get("last_flame_on"))
            self._last_flame_off = self._parse_iso8601(data.get("last_flame_off"))
            self._burner_start_events = []
            for item in data.get("burner_start_events", []):
                parsed = self._parse_iso8601(item)
                if parsed is not None:
                    self._burner_start_events.append(parsed)
            self._prune_start_events(datetime.now())

            last_flame_state = data.get("last_flame_state")
            if isinstance(last_flame_state, bool):
                self._last_flame_state = last_flame_state
            elif isinstance(last_flame_state, str):
                normalized = last_flame_state.strip().lower()
                if normalized in ("on", "true", "1"):
                    self._last_flame_state = True
                elif normalized in ("off", "false", "0"):
                    self._last_flame_state = False
        except Exception as e:
            self.logger.warning(f"Could not load runtime state from {self._state_file}: {e}")

    def _save_runtime_state(self) -> None:
        if self._state_file is None:
            return

        self._prune_start_events(datetime.now())

        payload = {
            "burner_start_count": self._burner_start_count,
            "burner_runtime_total_s": round(self._burner_runtime_total_s, 1),
            "burner_last_cycle_s": round(self._burner_last_cycle_s, 1),
            "last_flame_on": self._to_iso8601(self._last_flame_on) if self._last_flame_on else None,
            "last_flame_off": self._to_iso8601(self._last_flame_off) if self._last_flame_off else None,
            "last_flame_state": self._last_flame_state,
            "burner_start_events": [self._to_iso8601(ev) for ev in self._burner_start_events],
        }

        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            tmp_path.replace(self._state_file)
        except Exception as e:
            self.logger.warning(f"Could not persist runtime state to {self._state_file}: {e}")

    def _prune_start_events(self, now: datetime) -> None:
        cutoff = now - timedelta(days=8)
        self._burner_start_events = [ev for ev in self._burner_start_events if ev >= cutoff]

    def _count_starts_since(self, since: datetime, now: datetime) -> int:
        return sum(1 for ev in self._burner_start_events if since <= ev <= now)

    def _set_modulation(self, modulation: int, timestamp: datetime, source: str, raw_byte: Optional[int] = None) -> None:
        raw = int(raw_byte if raw_byte is not None else modulation) & 0xFF
        normalized = int(modulation)
        normalized_source = source

        # Some buses expose modulation in half-percent scale (0..200).
        if normalized > 100 and raw <= 200:
            normalized = int(round(raw / 2.0))
            normalized_source = f"{source}_DIV2"

        if normalized < 0:
            normalized = 0
        if normalized > 100:
            normalized = 100

        self._last_modulation_update_at = timestamp
        if not source.startswith("B511_Q2"):
            self._last_live_modulation_at = timestamp
        self._modulation_source = normalized_source
        self._modulation_raw_hex = f"0x{raw:02X}"
        self._set_sensor("boiler.burner_modulation", normalized, "%", timestamp, "Modulation", min_v=0, max_v=100)

        # If status telegrams are stale/missing, infer flame from modulation.
        status_age_s: Optional[float] = None
        if self._last_status_at is not None:
            status_age_s = max(0.0, (timestamp - self._last_status_at).total_seconds())
        status_missing_or_stale = status_age_s is None or status_age_s > self._status_stale_threshold_seconds
        if status_missing_or_stale:
            self._set_flame_state(normalized > 0, timestamp)

    def _publish_runtime_metrics(self, timestamp: datetime) -> None:
        self._prune_start_events(timestamp)
        self._publish_flame_metrics(timestamp)

        if self._last_telegram_at is not None:
            ebus_age_s = max(0.0, (timestamp - self._last_telegram_at).total_seconds())
            self._set_sensor("boiler.ebus_last_seen_s", int(round(ebus_age_s)), "s", timestamp, "Age of last eBUS telegram")

        if self._last_modulation_update_at is not None:
            modulation_age_s = max(0.0, (timestamp - self._last_modulation_update_at).total_seconds())
            self._set_sensor("boiler.modulation_last_update_s", int(round(modulation_age_s)), "s", timestamp, "Age of last modulation update")

        status_age_s: Optional[float] = None
        if self._last_status_at is not None:
            status_age_s = max(0.0, (timestamp - self._last_status_at).total_seconds())
        status_stale = status_age_s is None or status_age_s > self._status_stale_threshold_seconds
        if status_age_s is not None:
            self._set_sensor("boiler.status_last_update_s", int(round(status_age_s)), "s", timestamp, "Age of last status type 0 update")
        self._set_sensor("boiler.status_stale", status_stale, "", timestamp, "Status telegram is stale")

        day_start = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        starts_today = self._count_starts_since(day_start, timestamp)
        starts_24h = self._count_starts_since(timestamp - timedelta(hours=24), timestamp)
        starts_7d = self._count_starts_since(timestamp - timedelta(days=7), timestamp)
        self._set_sensor("boiler.burner_starts_today", starts_today, "", timestamp, "Burner starts today")
        self._set_sensor("boiler.burner_starts_24h", starts_24h, "", timestamp, "Burner starts last 24h")
        self._set_sensor("boiler.burner_starts_7d", starts_7d, "", timestamp, "Burner starts last 7d")

        self._set_sensor("boiler.modulation_source", self._modulation_source, "", timestamp, "Last modulation source")
        self._set_sensor("boiler.modulation_raw_hex", self._modulation_raw_hex, "", timestamp, "Last modulation raw byte")

    def _publish_flame_metrics(self, timestamp: datetime) -> None:
        current_cycle_s = 0.0
        if self._last_flame_state and self._active_cycle_started_at is not None:
            current_cycle_s = max(0.0, (timestamp - self._active_cycle_started_at).total_seconds())

        total_runtime_s = self._burner_runtime_total_s + current_cycle_s

        self._set_sensor("boiler.flame_on", bool(self._last_flame_state), "", timestamp, "Burner Flame")
        self._set_sensor("boiler.burner_start_count", int(self._burner_start_count), "", timestamp, "Burner start count")
        self._set_sensor("boiler.burner_runtime_total_s", int(round(total_runtime_s)), "s", timestamp, "Burner runtime total")
        self._set_sensor("boiler.burner_runtime_current_cycle_s", int(round(current_cycle_s)), "s", timestamp, "Burner runtime current cycle")
        self._set_sensor("boiler.burner_last_cycle_s", int(round(self._burner_last_cycle_s)), "s", timestamp, "Burner runtime last cycle")

        if self._last_flame_on is not None:
            self._set_sensor("boiler.last_flame_on", self._to_iso8601(self._last_flame_on), "", timestamp, "Last burner ON")
        if self._last_flame_off is not None:
            self._set_sensor("boiler.last_flame_off", self._to_iso8601(self._last_flame_off), "", timestamp, "Last burner OFF")

    def _commit_flame_state(self, flame_on: bool, timestamp: datetime) -> None:
        previous_state = self._last_flame_state
        self._pending_flame_state = None
        self._pending_flame_since = None

        if previous_state is None:
            if flame_on:
                self._last_flame_on = timestamp
                self._active_cycle_started_at = timestamp
            else:
                self._last_flame_off = timestamp
        elif previous_state != flame_on:
            if flame_on:
                self._burner_start_count += 1
                self._burner_start_events.append(timestamp)
                self._prune_start_events(timestamp)
                self._last_flame_on = timestamp
                self._active_cycle_started_at = timestamp
                self.logger.info(f"Burner start detected. Count={self._burner_start_count}")
            else:
                self._last_flame_off = timestamp
                if self._active_cycle_started_at is not None:
                    cycle_s = max(0.0, (timestamp - self._active_cycle_started_at).total_seconds())
                    self._burner_last_cycle_s = cycle_s
                    self._burner_runtime_total_s += cycle_s
                self._active_cycle_started_at = None

        self._last_flame_state = flame_on
        self._save_runtime_state()

    def _set_flame_state(self, flame_on: bool, timestamp: datetime) -> None:
        if self._last_flame_state is None:
            self._commit_flame_state(flame_on, timestamp)
            self._publish_flame_metrics(timestamp)
            return

        if flame_on == self._last_flame_state:
            self._pending_flame_state = None
            self._pending_flame_since = None
            if flame_on and self._active_cycle_started_at is None:
                # After restart we may know flame is ON from persisted state but not cycle start.
                self._active_cycle_started_at = timestamp
            self._publish_flame_metrics(timestamp)
            return

        if self._flame_debounce_seconds <= 0:
            self._commit_flame_state(flame_on, timestamp)
            self._publish_flame_metrics(timestamp)
            return

        if self._pending_flame_state != flame_on:
            self._pending_flame_state = flame_on
            self._pending_flame_since = timestamp
            self._publish_flame_metrics(timestamp)
            return

        if self._pending_flame_since is None:
            self._pending_flame_since = timestamp
            self._publish_flame_metrics(timestamp)
            return

        pending_for = (timestamp - self._pending_flame_since).total_seconds()
        if pending_for >= self._flame_debounce_seconds:
            self._commit_flame_state(flame_on, timestamp)

        self._publish_flame_metrics(timestamp)

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
                self._last_status_at = ts

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
                    self._set_sensor("boiler.pump_status", pump_running, "", ts, f"Pump State (S.{state_code:02d})")

                # SANITY CHECK: Water Pressure (0.0 to 3.5 bar)
                if resp[2] != 0xFF:
                    self._set_sensor("boiler.water_pressure", resp[2] / 10.0, "bar", ts,
                                   "Water Pressure", min_v=0.0, max_v=3.5)

                if resp[7] != 0xFF:
                    ext_status = resp[7]
                    heating_active = bool(ext_status & 0x80)
                    dhw_active = bool(ext_status & 0x04)
                    flame_from_status = bool(ext_status & 0x01)
                    # ExaControl behavior: when heating/DHW mode toggles, reflect that in flame state.
                    flame_proxy_from_mode = heating_active or dhw_active
                    self._set_flame_state(flame_from_status or flame_proxy_from_mode, ts)
                    self._set_sensor("boiler.dhw_active", dhw_active, "", ts, "DHW Mode")
                    self._set_sensor("boiler.heating_active", heating_active, "", ts, "Heating Mode")

            elif query_type == 2 and len(resp) >= 1:
                # Type 2: Setpoints
                if len(resp) >= 1 and resp[0] != 0xFF:
                    modulation_q2 = resp[0]
                    self._set_sensor("boiler.burner_modulation_q2", modulation_q2, "%", ts, "Modulation (B511 type 2)")
                    live_age_s: Optional[float] = None
                    if self._last_live_modulation_at is not None:
                        live_age_s = max(0.0, (ts - self._last_live_modulation_at).total_seconds())
                    if live_age_s is None or live_age_s > 120.0:
                        self._set_modulation(modulation_q2, ts, "B511_Q2_B0", raw_byte=resp[0])

                if len(resp) >= 2 and resp[1] != 0xFF:
                    self._set_sensor("boiler.outdoor_cutoff_internal", resp[1], "°C", ts,
                                   "Boiler Internal Cutoff (Ignored by MiPro)")

                if len(resp) >= 3 and resp[2] != 0xFF:
                    self._set_sensor("mipro.max_flow_temp", resp[2] / 2.0, "°C", ts, "Max Flow Limit")

                if len(resp) >= 4 and resp[3] != 0xFF:
                    self._set_sensor("boiler.dhw_setpoint_local", resp[3] / 2.0, "°C", ts, "Boiler Dial Setpoint")

                if len(resp) >= 6 and resp[5] != 0xFF:
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
            if len(resp) >= 1 and resp[0] != 0xFF:
                modulation = resp[0]
                self._set_modulation(modulation, ts, "B504_B0", raw_byte=resp[0])

            # Confirmed via debug dump: Bytes 8-9 contain outdoor temp
            if len(resp) >= 10:
                val = int.from_bytes(resp[8:10], 'little', signed=True) / 256.0
                self._set_sensor("boiler.outdoor_temperature", round(val, 1), "°C", ts,
                               "Outdoor Temp", min_v=-40.0, max_v=50.0)

        # === B509: Direct Room Temp (Primary Source) ===
        elif msg.name == "room_temp" and len(data) >= 2:
            if msg.source == 0x10 and data[0] != 0xFF:
                # --- FIX: ALWAYS update if valid (Priority over Boiler) ---
                self._set_sensor("mipro.room_temperature", data[0] / 2.0, "°C", ts,
                               "Room Temperature (Direct)", min_v=1.0, max_v=40.0)
            elif msg.source == 0x08:
                if data[0] != 0xFF:
                    self._set_modulation(data[0], ts, "B509_B0", raw_byte=data[0])
                if len(resp) >= 1 and resp[0] != 0xFF:
                    self._set_modulation(resp[0], ts, "B509_R0", raw_byte=resp[0])

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
        self._publish_runtime_metrics(datetime.now())
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
