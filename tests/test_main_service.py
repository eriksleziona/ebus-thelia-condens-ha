#!/usr/bin/env python3
"""Tests for the long-running main service loop."""

import logging
from pathlib import Path

from ebus_core.connection import ConnectionConfig
from main_service import (
    BridgeLoopState,
    SERIAL_IDLE_RECONNECT_SECONDS,
    STATUS_QUERY_TYPE_0,
    STATUS_QUERY_TYPE_1,
    STATUS_QUERY_TYPE_2,
    _run_maintenance_cycle,
)
from thelia.parser import DataAggregator, TheliaParser


class _FakeConnection:
    def __init__(self, idle_seconds: float = 0.0):
        self.config = ConnectionConfig(port="/dev/null", reconnect_delay=5.0)
        self.connected = True
        self.idle_seconds = idle_seconds
        self.sent_queries = []
        self.disconnect_calls = 0

    def seconds_since_last_activity(self, now=None):
        return self.idle_seconds

    def read_telegrams(self):
        return []

    def send_query(self, **kwargs):
        self.sent_queries.append(kwargs)
        return True

    def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1


class _FakeMqtt:
    def __init__(self):
        self.published = []

    def publish_sensors(self, sensors):
        self.published.append(dict(sensors))


def _aggregator(tmp_path: Path) -> DataAggregator:
    return DataAggregator(
        state_file=str(tmp_path / "runtime_state.json"),
        flame_debounce_seconds=0,
        status_stale_threshold_seconds=120,
    )


def test_cycle_polls_even_when_no_telegram_arrives(tmp_path):
    connection = _FakeConnection()
    parser = TheliaParser()
    aggregator = _aggregator(tmp_path)
    mqtt_client = _FakeMqtt()
    state = BridgeLoopState()
    logger = logging.getLogger("test.main_service.poll")

    _run_maintenance_cycle(connection, parser, aggregator, mqtt_client, state, logger, loop_now=200.0)

    assert [item["data"] for item in connection.sent_queries] == [
        STATUS_QUERY_TYPE_0,
        STATUS_QUERY_TYPE_1,
        STATUS_QUERY_TYPE_2,
    ]


def test_cycle_reconnects_after_bus_silence(tmp_path):
    connection = _FakeConnection(idle_seconds=SERIAL_IDLE_RECONNECT_SECONDS + 1.0)
    parser = TheliaParser()
    aggregator = _aggregator(tmp_path)
    mqtt_client = _FakeMqtt()
    state = BridgeLoopState()
    logger = logging.getLogger("test.main_service.idle")

    _run_maintenance_cycle(connection, parser, aggregator, mqtt_client, state, logger, loop_now=200.0)

    assert connection.disconnect_calls == 1
    assert connection.sent_queries == []
