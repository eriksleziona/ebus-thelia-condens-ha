#!/usr/bin/env python3
"""Regression tests for parser/aggregator edge cases."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ebus_core.telegram import EbusTelegram
from thelia.parser import DataAggregator, ParsedMessage


def _make_message(name: str, data: bytes, resp: bytes, ts: datetime, primary: int, secondary: int, query_data=None):
    telegram = EbusTelegram(
        source=0x08,
        destination=0x10,
        primary_command=primary,
        secondary_command=secondary,
        data=data,
        response_data=resp,
        timestamp=ts.timestamp(),
    )
    return ParsedMessage(
        name=name,
        timestamp=ts,
        source=telegram.source,
        destination=telegram.destination,
        source_name="boiler",
        dest_name="mipro",
        command=(primary, secondary),
        query_data=query_data or {},
        response_data={},
        units={},
        raw_telegram=telegram,
    )


def _msg_status_q2(ts: datetime, resp: bytes):
    return _make_message(
        name="status_temps",
        data=bytes([0x02]),
        resp=resp,
        ts=ts,
        primary=0xB5,
        secondary=0x11,
        query_data={"query_type": 2},
    )


def _msg_status_q0(ts: datetime, ext_status: int):
    # Need at least 8 response bytes for the parser's type-0 branch.
    resp = bytes([0x00, 0x00, 0x10, 0x28, 0x02, 0x00, 0x00, ext_status & 0xFF])
    return _make_message(
        name="status_temps",
        data=bytes([0x00]),
        resp=resp,
        ts=ts,
        primary=0xB5,
        secondary=0x11,
        query_data={"query_type": 0},
    )


def _msg_b504(ts: datetime, resp: bytes):
    return _make_message(
        name="modulation_outdoor",
        data=bytes([0x00]),
        resp=resp,
        ts=ts,
        primary=0xB5,
        secondary=0x04,
    )


def test_b511_q2_modulation_for_variable_response_length(tmp_path):
    aggregator = DataAggregator(state_file=str(tmp_path / "runtime_state.json"), flame_debounce_seconds=0)
    now = datetime.now()

    for length in range(1, 7):
        resp = bytes([37] + [0x00] * (length - 1))
        aggregator.update(_msg_status_q2(now + timedelta(seconds=length), resp))
        assert aggregator.get_sensor("boiler.burner_modulation") == 37


def test_modulation_source_and_raw_hex_updates(tmp_path):
    aggregator = DataAggregator(state_file=str(tmp_path / "runtime_state.json"), flame_debounce_seconds=0)
    now = datetime.now()

    aggregator.update(_msg_status_q2(now, bytes([25])))
    assert aggregator.get_sensor("boiler.modulation_source") == "B511_Q2_B0"
    assert aggregator.get_sensor("boiler.modulation_raw_hex") == "0x19"

    aggregator.update(_msg_b504(now + timedelta(seconds=1), bytes([40])))
    assert aggregator.get_sensor("boiler.modulation_source") == "B504_B0"
    assert aggregator.get_sensor("boiler.modulation_raw_hex") == "0x28"


def test_burner_start_window_counters(tmp_path):
    aggregator = DataAggregator(state_file=str(tmp_path / "runtime_state.json"), flame_debounce_seconds=0)
    now = datetime.now()

    event_times = [
        now - timedelta(days=8),   # outside 7d
        now - timedelta(days=3),   # inside 7d
        now - timedelta(hours=3),  # inside 24h
        now,                       # inside 24h
    ]

    aggregator.update(_msg_status_q0(event_times[0] - timedelta(seconds=1), ext_status=0x00))
    for ev in event_times:
        aggregator.update(_msg_status_q0(ev, ext_status=0x81))  # flame+heating
        aggregator.update(_msg_status_q0(ev + timedelta(seconds=1), ext_status=0x00))

    sensors = aggregator.get_all_sensors()
    assert sensors["boiler.burner_starts_24h"]["value"] == 2
    assert sensors["boiler.burner_starts_7d"]["value"] == 3
    assert sensors["boiler.burner_starts_today"]["value"] >= 1


def test_status_stale_and_ages(tmp_path):
    aggregator = DataAggregator(
        state_file=str(tmp_path / "runtime_state.json"),
        flame_debounce_seconds=0,
        status_stale_threshold_seconds=10,
    )
    now = datetime.now()

    old = now - timedelta(seconds=30)
    aggregator.update(_msg_status_q0(old, ext_status=0x00))
    aggregator.update(_msg_status_q2(old + timedelta(seconds=1), bytes([22])))

    stale_view = aggregator.get_all_sensors()
    assert stale_view["boiler.status_stale"]["value"] is True
    assert stale_view["boiler.modulation_last_update_s"]["value"] >= 20
    assert stale_view["boiler.ebus_last_seen_s"]["value"] >= 20

    aggregator.update(_msg_status_q0(datetime.now(), ext_status=0x00))
    fresh_view = aggregator.get_all_sensors()
    assert fresh_view["boiler.status_stale"]["value"] is False


def test_b504_live_modulation_has_priority_over_b511_q2(tmp_path):
    aggregator = DataAggregator(state_file=str(tmp_path / "runtime_state.json"), flame_debounce_seconds=0)
    now = datetime.now()

    # Live B504 update first.
    aggregator.update(_msg_b504(now, bytes([8])))
    assert aggregator.get_sensor("boiler.burner_modulation") == 8
    assert aggregator.get_sensor("boiler.modulation_source") == "B504_B0"

    # B511 Q2 arrives shortly after with different value, should not override live source.
    aggregator.update(_msg_status_q2(now + timedelta(seconds=2), bytes([35, 0, 0, 0, 0, 0])))
    assert aggregator.get_sensor("boiler.burner_modulation") == 8
    assert aggregator.get_sensor("boiler.modulation_source") == "B504_B0"
    assert aggregator.get_sensor("boiler.burner_modulation_q2") == 35
