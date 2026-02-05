#!/usr/bin/env python3
"""
Debug script to find outdoor temperature and pressure bytes.
"""

import sys
import os
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from ebus_core.telegram import EbusTelegram


def format_bytes(data: bytes, highlight_positions: list = None) -> str:
    """Format bytes with position markers."""
    if not data:
        return "(empty)"

    parts = []
    for i, b in enumerate(data):
        if highlight_positions and i in highlight_positions:
            parts.append(f"[{b:02X}]")  # Highlighted
        else:
            parts.append(f"{b:02X}")

    # Add position markers
    positions = " ".join(f"{i:2d}" for i in range(len(data)))
    hex_str = " ".join(parts)

    return f"Pos: {positions}\nHex: {hex_str}"


def analyze_b511(telegram: EbusTelegram) -> None:
    """Analyze B511 message in detail."""
    query_type = telegram.data[0] if telegram.data else -1
    resp = telegram.response_data

    print(f"\n{'=' * 60}")
    print(f"B511 Query Type {query_type}")
    print(f"{'=' * 60}")
    print(f"Query data ({len(telegram.data)} bytes):")
    print(f"  {format_bytes(telegram.data)}")

    if resp:
        print(f"\nResponse ({len(resp)} bytes):")
        print(f"  {format_bytes(resp)}")

        print(f"\nDecoded values (trying different methods):")

        for i in range(min(len(resp), 9)):
            val = resp[i]
            print(f"  byte[{i}] = {val:3d} (0x{val:02X})")
            print(f"         ÷2 = {val / 2:.1f}°C")
            print(f"         ÷10 = {val / 10:.1f} bar")
            if val != 255:
                print(f"         (valid)")
            else:
                print(f"         (0xFF = N/A)")

        # Try 16-bit values
        print(f"\n  16-bit combinations:")
        for i in range(0, len(resp) - 1, 2):
            val_le = int.from_bytes(resp[i:i + 2], 'little', signed=True)
            val_ue = int.from_bytes(resp[i:i + 2], 'little', signed=False)
            print(f"  bytes[{i}:{i + 2}] = {val_le} signed, {val_ue} unsigned")
            print(f"              ÷256 = {val_le / 256:.2f}°C (signed)")


def analyze_b504(telegram: EbusTelegram) -> None:
    """Analyze B504 message in detail."""
    resp = telegram.response_data

    print(f"\n{'=' * 60}")
    print(f"B504 - Modulation/Outdoor")
    print(f"{'=' * 60}")
    print(f"Query data ({len(telegram.data)} bytes):")
    print(f"  {format_bytes(telegram.data)}")

    if resp:
        print(f"\nResponse ({len(resp)} bytes):")
        print(f"  {format_bytes(resp)}")

        print(f"\nDecoded values:")

        for i in range(min(len(resp), 10)):
            val = resp[i]
            print(f"  byte[{i}] = {val:3d} (0x{val:02X})", end="")
            if i == 0:
                print(f"  ← Modulation? {val}%")
            elif val == 255:
                print(f"  ← 0xFF (N/A)")
            else:
                print(f"  ÷2={val / 2:.1f}°C  ÷10={val / 10:.1f}bar")

        # Try outdoor temp in different positions
        print(f"\n  Outdoor temp candidates (signed int16 ÷ 256):")
        for i in range(0, len(resp) - 1):
            val = int.from_bytes(resp[i:i + 2], 'little', signed=True)
            temp = val / 256.0
            if -40 <= temp <= 50:
                print(f"    bytes[{i}:{i + 2}] = {val} → {temp:.1f}°C ✓")
            else:
                print(f"    bytes[{i}:{i + 2}] = {val} → {temp:.1f}°C (out of range)")


def analyze_b510(telegram: EbusTelegram) -> None:
    """Analyze B510 message."""
    data = telegram.data
    resp = telegram.response_data

    print(f"\n{'=' * 60}")
    print(f"B510 - Setpoints")
    print(f"{'=' * 60}")
    print(f"Query data ({len(data)} bytes):")
    print(f"  {format_bytes(data)}")

    if data and len(data) >= 3:
        print(f"\n  Decoded:")
        print(f"    mode1 = {data[0]}")
        print(f"    mode2 = {data[1]}")
        for i in range(2, min(len(data), 9)):
            val = data[i]
            if val != 255:
                print(f"    byte[{i}] = {val} → ÷2 = {val / 2:.1f}°C")
            else:
                print(f"    byte[{i}] = 255 (0xFF = N/A)")


def analyze_b512(telegram: EbusTelegram) -> None:
    """Analyze B512 message - might contain pressure."""
    resp = telegram.response_data
    data = telegram.data

    print(f"\n{'=' * 60}")
    print(f"B512 - Possibly Pressure/DHW")
    print(f"{'=' * 60}")
    print(f"Query data ({len(data)} bytes):")
    print(f"  {format_bytes(data)}")

    if resp:
        print(f"\nResponse ({len(resp)} bytes):")
        print(f"  {format_bytes(resp)}")

        print(f"\n  Looking for pressure (should be 0.5-3.0 bar, so raw 5-30):")
        for i, val in enumerate(resp):
            if 5 <= val <= 35 and val != 255:
                print(f"    byte[{i}] = {val} → ÷10 = {val / 10:.1f} bar ✓ possible")


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 60)
    print("🔍 eBus Byte-Level Debug")
    print("=" * 60)
    print("Looking for outdoor temperature and pressure...")
    print(f"\nConnecting to {PORT}...")

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")

    # Track which messages we've seen
    seen = defaultdict(int)
    samples = {
        "B511_0": None,  # B511 query type 0
        "B511_1": None,  # B511 query type 1
        "B511_2": None,  # B511 query type 2
        "B504": None,
        "B510": None,
        "B512": None,
    }

    count = 0

    try:
        for telegram in connection.telegram_generator():
            count += 1
            cmd = f"{telegram.primary_command:02X}{telegram.secondary_command:02X}"

            # Capture samples of each message type
            if cmd == "B511" and telegram.data:
                qtype = telegram.data[0]
                key = f"B511_{qtype}"
                if key in samples and samples[key] is None:
                    samples[key] = telegram
                    analyze_b511(telegram)

            elif cmd == "B504":
                if samples["B504"] is None:
                    samples["B504"] = telegram
                    analyze_b504(telegram)

            elif cmd == "B510":
                if samples["B510"] is None:
                    samples["B510"] = telegram
                    analyze_b510(telegram)

            elif cmd == "B512":
                if samples["B512"] is None:
                    samples["B512"] = telegram
                    analyze_b512(telegram)

            seen[cmd] += 1

            # Stop after we have samples or 100 messages
            if all(v is not None for v in samples.values()) or count >= 150:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        connection.disconnect()

    # Summary
    print("\n" + "=" * 60)
    print("📊 MESSAGES SEEN:")
    print("=" * 60)
    for cmd, cnt in sorted(seen.items()):
        sample = "✓" if any(cmd in k for k in samples if samples.get(k)) else "✗"
        print(f"  {cmd}: {cnt} messages {sample}")

    # Check what we're missing
    print("\n📋 SAMPLES CAPTURED:")
    for key, telegram in samples.items():
        if telegram:
            print(f"  {key}: ✓")
        else:
            print(f"  {key}: ✗ NOT SEEN")

    print("\n" + "=" * 60)
    print("💡 Look above for the outdoor temperature and pressure bytes!")
    print("=" * 60)


if __name__ == "__main__":
    main()