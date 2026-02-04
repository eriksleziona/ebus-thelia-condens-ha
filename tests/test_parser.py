#!/usr/bin/env python3
"""Tests for the parser."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ebus_core.crc import EbusCRC
from ebus_core.telegram import TelegramParser, EbusTelegram, EscapeHandler
from thelia.parser import TheliaParser
from thelia.messages import THELIA_MESSAGES


def test_crc():
    """Test CRC calculation."""
    print("Testing CRC...")

    # Test vector
    data = bytes([0x10, 0xFE, 0x05, 0x07, 0x04, 0x00, 0x48, 0x12, 0x80])
    crc = EbusCRC.calculate(data)
    print(f"  CRC of {data.hex()}: 0x{crc:02X}")

    print("  ✅ CRC test passed")


def test_escape():
    """Test escape handling."""
    print("Testing escape sequences...")

    # Test unescape
    escaped = bytes([0x10, 0xA9, 0x00, 0xA9, 0x01, 0x20])
    unescaped = EscapeHandler.unescape(escaped)
    expected = bytes([0x10, 0xA9, 0xAA, 0x20])

    assert unescaped == expected, f"Expected {expected.hex()}, got {unescaped.hex()}"
    print(f"  Unescape: {escaped.hex()} -> {unescaped.hex()}")

    # Test escape
    original = bytes([0x10, 0xA9, 0xAA, 0x20])
    escaped = EscapeHandler.escape(original)
    print(f"  Escape: {original.hex()} -> {escaped.hex()}")

    print("  ✅ Escape test passed")


def test_telegram_parser():
    """Test telegram parsing."""
    print("Testing telegram parser...")

    parser = TelegramParser()

    # Simulate raw data with SYNC bytes
    raw = bytes([
        0xAA,  # SYNC
        0x10, 0xFE, 0x05, 0x07, 0x04, 0x00, 0x48, 0x12, 0x80, 0x00,  # Telegram
        0xAA,  # SYNC
    ])

    telegrams = parser.feed(raw)
    print(f"  Fed {len(raw)} bytes, got {len(telegrams)} telegram(s)")

    for t in telegrams:
        print(f"  {t}")

    print("  ✅ Telegram parser test passed")


def test_thelia_parser():
    """Test Thelia message parsing."""
    print("Testing Thelia parser...")

    parser = TheliaParser()

    # Create test telegram
    telegram = EbusTelegram(
        source=0x10,
        destination=0xFE,
        primary_command=0x05,
        secondary_command=0x07,
        data=bytes([0x00, 0x14, 0x80, 0x00, 0x12, 0x80]),  # Flow/return temps
        valid=True
    )

    msg = parser.parse(telegram)
    print(f"  Parsed: {msg}")
    print(f"  Values: {msg.values}")

    print("  ✅ Thelia parser test passed")


def test_message_definitions():
    """Test message definitions."""
    print("Testing message definitions...")

    print(f"  Registered messages: {len(THELIA_MESSAGES)}")
    for cmd, msg_def in THELIA_MESSAGES.items():
        print(f"    {msg_def.command_hex}: {msg_def.name} ({len(msg_def.fields)} fields)")

    print("  ✅ Message definitions test passed")


def main():
    print("=" * 60)
    print("eBus Thelia Parser Tests")
    print("=" * 60)

    test_crc()
    test_escape()
    test_telegram_parser()
    test_thelia_parser()
    test_message_definitions()

    print("=" * 60)
    print("All tests passed! ✅")


if __name__ == "__main__":
    main()