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


def _msg_history(ts: datetime, name: str, secondary: int, query_data: bytes, resp: bytes):
    return _make_message(
        name=name,
        data=query_data,
        resp=resp,
        ts=ts,
        primary=0xB5,
        secondary=secondary,
        query_data={"query_type": query_data[0] if query_data else None},
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


def test_history_stats_are_exposed_as_candidate_sensors(tmp_path):
    aggregator = DataAggregator(state_file=str(tmp_path / "runtime_state.json"), flame_debounce_seconds=0)
    now = datetime.now()

    # q0 response with two non-empty words and one non-empty dword.
    resp_q0 = bytes([0x34, 0x12, 0x78, 0x56, 0x00, 0x00, 0xFF, 0xFF])
    aggregator.update(_msg_history(now, "history_stats", 0x13, bytes([0x00]), resp_q0))

    sensors = aggregator.get_all_sensors()
    assert sensors["history.b513.q00_response_len"]["value"] == 8
    assert sensors["history.b513.q00_query_hex"]["value"] == "00"
    assert sensors["history.b513.q00_u16_0"]["value"] == 0x1234
    assert sensors["history.b513.q00_u16_2"]["value"] == 0x5678
    assert sensors["history.b513.q00_u32_0"]["value"] == 0x56781234
    assert sensors["history.b513.q00_kwh_guess_div10_0"]["value"] == round(0x56781234 / 10.0, 1)
    assert sensors["history.b513.q00_kwh_guess_div100_0"]["value"] == round(0x56781234 / 100.0, 2)

    # q1 should write into a separate namespace.
    resp_q1 = bytes([0x05, 0x00, 0x00, 0x00])
    aggregator.update(_msg_history(now + timedelta(seconds=1), "error_history", 0x15, bytes([0x01]), resp_q1))
    sensors = aggregator.get_all_sensors()
    assert sensors["history.b515.q01_response_len"]["value"] == 4
    assert sensors["history.b515.q01_u16_0"]["value"] == 5

    # Two-byte indexed query should produce a unique namespace per index.
    resp_q0_i2 = bytes([0x10, 0x27, 0x00, 0x00])
    aggregator.update(_msg_history(now + timedelta(seconds=2), "history_stats", 0x13, bytes([0x00, 0x02]), resp_q0_i2))
    sensors = aggregator.get_all_sensors()
    assert sensors["history.b513.q0002_response_len"]["value"] == 4
    assert sensors["history.b513.q0002_u16_0"]["value"] == 10000
    assert sensors["history.b513.q0002_kwh_guess_u16_div10_0"]["value"] == 1000.0


def test_history_sensors_persist_beyond_max_age(tmp_path):
    aggregator = DataAggregator(
        max_age=1.0,
        state_file=str(tmp_path / "runtime_state.json"),
        flame_debounce_seconds=0,
    )
    old_ts = datetime.now() - timedelta(seconds=30)
    aggregator.update(_msg_history(old_ts, "history_stats", 0x13, bytes([0x00]), bytes([0x34, 0x12])))

    assert aggregator.get_sensor("history.b513.q00_u16_0") == 0x1234
    sensors = aggregator.get_all_sensors()
    assert "history.b513.q00_u16_0" in sensors


def test_unknown_b5_with_response_is_published_as_history_candidates(tmp_path):
    aggregator = DataAggregator(
        state_file=str(tmp_path / "runtime_state.json"),
        flame_debounce_seconds=0,
    )
    now = datetime.now()

    msg = _make_message(
        name="unknown",
        data=bytes([0x09, 0x02]),
        resp=bytes([0x34, 0x12, 0x00, 0x00]),
        ts=now,
        primary=0xB5,
        secondary=0x77,
    )
    aggregator.update(msg)

    sensors = aggregator.get_all_sensors()
    assert sensors["history.unknown.b577.q0902_response_len"]["value"] == 4
    assert sensors["history.unknown.b577.q0902_u16_0"]["value"] == 0x1234
