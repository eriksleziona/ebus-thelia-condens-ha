#!/usr/bin/env python3
"""Tests for the low-level eBUS and Thelia parsers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ebus_core.crc import EbusCRC
from ebus_core.telegram import EbusTelegram, TelegramParser
from thelia.messages import THELIA_MESSAGES, get_message_definition
from thelia.parser import TheliaParser


def test_crc_returns_byte_value():
    data = bytes([0x10, 0xFE, 0x05, 0x07, 0x04, 0x00, 0x48, 0x12, 0x80])
    crc = EbusCRC.calculate(data)

    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFF


def test_telegram_parser_extracts_sync_delimited_frame():
    parser = TelegramParser()
    raw = bytes([
        0xAA,
        0x10, 0xFE, 0xB5, 0x09, 0x01, 0x2B, 0x00,
        0xAA,
    ])

    telegrams = parser.feed(raw)

    assert len(telegrams) == 1
    telegram = telegrams[0]
    assert telegram.source == 0x10
    assert telegram.destination == 0xFE
    assert telegram.primary_command == 0xB5
    assert telegram.secondary_command == 0x09
    assert telegram.data == bytes([0x2B])
    assert telegram.crc == 0x00


def test_thelia_parser_decodes_room_temperature():
    parser = TheliaParser()
    telegram = EbusTelegram(
        source=0x10,
        destination=0x08,
        primary_command=0xB5,
        secondary_command=0x09,
        data=bytes([0x2B, 0x00]),
        valid=True,
    )

    message = parser.parse(telegram)

    assert message.name == "room_temp"
    assert message.query_data["room_temp"] == 21.5
    assert message.source_name == "mipro"
    assert message.dest_name == "boiler"


def test_message_definitions_are_registered():
    assert len(THELIA_MESSAGES) >= 5
    assert get_message_definition(0xB5, 0x11) is not None
    assert get_message_definition(0xB5, 0x11).name == "status_temps"
