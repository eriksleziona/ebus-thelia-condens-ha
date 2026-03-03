#!/usr/bin/env python3
"""Tests for active eBUS query sending."""

from ebus_core.connection import ConnectionConfig, SerialConnection
from ebus_core.crc import EbusCRC
from ebus_core.telegram import EbusTelegram


class _DummySerial:
    def __init__(self):
        self.is_open = True
        self.written = []
        self.flushed = False
        self.reset_calls = 0

    def write(self, payload):
        self.written.append(bytes(payload))
        return len(payload)

    def flush(self):
        self.flushed = True

    def reset_input_buffer(self):
        self.reset_calls += 1


def _connection_with_dummy_serial():
    conn = SerialConnection(ConnectionConfig())
    conn._serial = _DummySerial()  # pylint: disable=protected-access
    conn._connected = True  # pylint: disable=protected-access
    return conn


def test_build_query_frame_without_payload():
    frame = SerialConnection.build_query_frame(0x30, 0x08, 0xB5, 0x11, b"")
    expected_head = bytes([0x30, 0x08, 0xB5, 0x11, 0x00])
    expected_crc = EbusCRC.calculate(expected_head)
    assert frame == expected_head + bytes([expected_crc])


def test_build_query_frame_with_payload():
    payload = bytes([0x02])
    frame = SerialConnection.build_query_frame(0x30, 0x08, 0xB5, 0x11, payload)
    expected_head = bytes([0x30, 0x08, 0xB5, 0x11, 0x01, 0x02])
    expected_crc = EbusCRC.calculate(expected_head)
    assert frame == expected_head + bytes([expected_crc])


def test_send_query_writes_frame_with_sync():
    conn = _connection_with_dummy_serial()
    ok = conn.send_query(0x30, 0x08, 0xB5, 0x11, data=bytes([0x00]), prepend_sync=True, append_sync=True)
    assert ok is True

    frame = SerialConnection.build_query_frame(0x30, 0x08, 0xB5, 0x11, bytes([0x00]))
    assert conn._serial.written == [bytes([0xAA]) + frame + bytes([0xAA])]  # pylint: disable=protected-access
    assert conn._serial.flushed is True  # pylint: disable=protected-access


def test_send_query_can_flush_input_buffer():
    conn = _connection_with_dummy_serial()
    ok = conn.send_query(0x30, 0x08, 0xB5, 0x11, data=bytes([0x00]), flush_input=True)
    assert ok is True
    assert conn._serial.reset_calls == 1  # pylint: disable=protected-access


def test_query_once_returns_matching_telegram():
    conn = _connection_with_dummy_serial()

    wanted = EbusTelegram(
        source=0x30,
        destination=0x08,
        primary_command=0xB5,
        secondary_command=0x11,
        data=bytes([0x00]),
        response_data=bytes([0x01, 0x02, 0x03]),
    )
    other = EbusTelegram(
        source=0x10,
        destination=0x08,
        primary_command=0xB5,
        secondary_command=0x11,
        data=bytes([0x00]),
    )

    batches = [[other], [wanted]]

    def fake_read_telegrams():
        if batches:
            return batches.pop(0)
        return []

    conn.read_telegrams = fake_read_telegrams  # type: ignore[method-assign]

    reply = conn.query_once(
        source=0x30,
        destination=0x08,
        primary_command=0xB5,
        secondary_command=0x11,
        data=bytes([0x00]),
        timeout_s=0.2,
    )
    assert reply is wanted


def test_query_once_returns_none_on_timeout():
    conn = _connection_with_dummy_serial()
    conn.read_telegrams = lambda: []  # type: ignore[method-assign]

    reply = conn.query_once(
        source=0x30,
        destination=0x08,
        primary_command=0xB5,
        secondary_command=0x11,
        data=bytes([0x00]),
        timeout_s=0.05,
    )
    assert reply is None
