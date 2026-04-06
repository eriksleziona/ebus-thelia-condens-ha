#!/usr/bin/env python3
"""Tests for the long-running main service loop."""

import logging
from pathlib import Path

from ebus_core.connection import ConnectionConfig
from main_service import (
    BridgeLoopState,
    MQTT_HEALTHCHECK_INTERVAL_SECONDS,
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
    def __init__(self, publish_ok: bool = True, healthcheck_ok: bool = True):
        self.published = []
        self.publish_ok = publish_ok
        self.healthcheck_ok = healthcheck_ok
        self.healthcheck_calls = 0
        self.restart_reasons = []

    def publish_sensors(self, sensors):
        self.published.append(dict(sensors))
        return self.publish_ok

    def publish_healthcheck(self):
        self.healthcheck_calls += 1
        return self.healthcheck_ok

    def restart(self, reason):
        self.restart_reasons.append(reason)


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


def test_cycle_does_not_mark_publish_success_when_mqtt_publish_fails(tmp_path):
    connection = _FakeConnection()
    parser = TheliaParser()
    aggregator = _aggregator(tmp_path)
    mqtt_client = _FakeMqtt(publish_ok=False)
    state = BridgeLoopState()
    logger = logging.getLogger("test.main_service.publish_failure")

    _run_maintenance_cycle(connection, parser, aggregator, mqtt_client, state, logger, loop_now=200.0)

    assert state.last_publish_monotonic == 0.0
    assert mqtt_client.published


def test_cycle_runs_periodic_mqtt_healthcheck(tmp_path):
    connection = _FakeConnection()
    parser = TheliaParser()
    aggregator = _aggregator(tmp_path)
    mqtt_client = _FakeMqtt()
    state = BridgeLoopState(last_mqtt_healthcheck_monotonic=10.0)
    logger = logging.getLogger("test.main_service.healthcheck")

    _run_maintenance_cycle(
        connection,
        parser,
        aggregator,
        mqtt_client,
        state,
        logger,
        loop_now=10.0 + MQTT_HEALTHCHECK_INTERVAL_SECONDS + 1.0,
    )

    assert mqtt_client.healthcheck_calls == 1
    assert state.last_mqtt_healthcheck_monotonic == 10.0 + MQTT_HEALTHCHECK_INTERVAL_SECONDS + 1.0
