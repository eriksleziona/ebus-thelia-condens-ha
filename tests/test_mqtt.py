#!/usr/bin/env python3
"""Tests for MQTT client resilience."""

from unittest.mock import patch

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from thelia.mqtt import HAMqttClient


class _FakePublishInfo:
    def __init__(self, rc=mqtt.MQTT_ERR_SUCCESS, published=True):
        self.rc = rc
        self._published = published
        self.wait_calls = []

    def wait_for_publish(self, timeout=None):
        self.wait_calls.append(timeout)

    def is_published(self):
        return self._published


class _FakeClient:
    def __init__(self, publish_results=None):
        self.publish_results = list(publish_results or [])
        self.publish_calls = []
        self.connected = True
        self.loop_started = False
        self.loop_stopped = False
        self.disconnect_calls = 0
        self.connect_async_calls = 0
        self.reconnect_calls = 0
        self.on_connect = None
        self.on_disconnect = None

    def username_pw_set(self, username, password):
        self.username = username
        self.password = password

    def reconnect_delay_set(self, min_delay=1, max_delay=30):
        self.reconnect_delay = (min_delay, max_delay)

    def will_set(self, topic, payload, retain=False):
        self.will = (topic, payload, retain)

    def enable_logger(self, logger):
        self.logger = logger

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_stopped = True

    def connect_async(self, broker, port, keepalive):
        self.connect_async_calls += 1
        self.last_connect_async = (broker, port, keepalive)

    def reconnect(self):
        self.reconnect_calls += 1

    def is_connected(self):
        return self.connected

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.publish_calls.append((topic, payload, qos, retain))
        if self.publish_results:
            return self.publish_results.pop(0)
        return _FakePublishInfo()


def test_publish_sensors_restarts_client_after_publish_error():
    broken_client = _FakeClient(publish_results=[_FakePublishInfo(rc=1)])
    replacement_client = _FakeClient()

    with patch("thelia.mqtt.mqtt.Client", side_effect=[broken_client, replacement_client]):
        mqtt_client = HAMqttClient("broker", 1883, "user", "pass")
        mqtt_client.connected = True
        mqtt_client.discovery_sent = True
        mqtt_client._ever_connected = True  # pylint: disable=protected-access
        mqtt_client._loop_started = True  # pylint: disable=protected-access

        ok = mqtt_client.publish_sensors({"boiler.flow_temperature": {"value": 41.5, "unit": "C"}})

    assert ok is False
    assert broken_client.disconnect_calls == 1
    assert broken_client.loop_stopped is True
    assert replacement_client.loop_started is True
    assert replacement_client.connect_async_calls == 1


def test_publish_healthcheck_restarts_client_when_ack_never_arrives():
    broken_client = _FakeClient(publish_results=[_FakePublishInfo(rc=mqtt.MQTT_ERR_SUCCESS, published=False)])
    replacement_client = _FakeClient()

    with patch("thelia.mqtt.mqtt.Client", side_effect=[broken_client, replacement_client]):
        mqtt_client = HAMqttClient("broker", 1883)
        mqtt_client.connected = True
        mqtt_client.discovery_sent = True
        mqtt_client._ever_connected = True  # pylint: disable=protected-access
        mqtt_client._loop_started = True  # pylint: disable=protected-access

        ok = mqtt_client.publish_healthcheck()

    assert ok is False
    assert broken_client.disconnect_calls == 1
    assert broken_client.loop_stopped is True
    assert replacement_client.loop_started is True
    assert replacement_client.connect_async_calls == 1


def test_publish_sensors_marks_success_timestamp():
    healthy_client = _FakeClient()

    with patch("thelia.mqtt.mqtt.Client", return_value=healthy_client):
        mqtt_client = HAMqttClient("broker", 1883)
        mqtt_client.connected = True
        mqtt_client.discovery_sent = True

        ok = mqtt_client.publish_sensors({"boiler.flow_temperature": {"value": 41.5, "unit": "C"}})

    assert ok is True
    assert mqtt_client.seconds_since_last_successful_publish() is not None


def test_on_connect_accepts_reasoncode_object():
    healthy_client = _FakeClient()

    with patch("thelia.mqtt.mqtt.Client", return_value=healthy_client):
        mqtt_client = HAMqttClient("broker", 1883)
        mqtt_client._loop_started = True  # pylint: disable=protected-access
        healthy_client.connected = True

        reason_code = ReasonCode(PacketTypes.CONNACK, "Success")
        mqtt_client._on_connect(healthy_client, None, None, reason_code, None)

    assert mqtt_client.connected is True


def test_on_disconnect_accepts_reasoncode_object():
    healthy_client = _FakeClient()

    with patch("thelia.mqtt.mqtt.Client", return_value=healthy_client):
        mqtt_client = HAMqttClient("broker", 1883)
        mqtt_client.connected = True

        reason_code = ReasonCode(PacketTypes.DISCONNECT, "Normal disconnection")
        mqtt_client._on_disconnect(healthy_client, None, None, reason_code, None)

    assert mqtt_client.connected is False
