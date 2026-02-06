#!/usr/bin/env python3
"""Debug script to find missing sensor updates."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from ebus_core.telegram import EbusTelegram


def analyze_telegram(telegram: EbusTelegram):
    """Print detailed telegram analysis."""
    src = telegram.source
    dst = telegram.destination
    pc = telegram.primary_command
    sc = telegram.secondary_command
    data = telegram.data or b''
    resp = telegram.response_data or b''

    cmd_name = "UNKNOWN"

    # Known commands
    if (pc, sc) == (0xB5, 0x11):
        cmd_name = "B511_STATUS_TEMPS"
        if len(data) >= 1:
            query_type = data[0]
            cmd_name += f" (Type {query_type})"
    elif (pc, sc) == (0xB5, 0x04):
        cmd_name = "B504_MODULATION"
    elif (pc, sc) == (0xB5, 0x10):
        cmd_name = "B510_TARGET_FLOW"
    elif (pc, sc) == (0xB5, 0x09):
        cmd_name = "B509_ROOM_TEMP"
    elif (pc, sc) == (0xB5, 0x16):
        cmd_name = "B516_DATETIME"

    print(f"\n{'=' * 70}")
    print(f"Command: {cmd_name}")
    print(f"Source: 0x{src:02X} → Dest: 0x{dst:02X}")
    print(f"Primary: 0x{pc:02X}, Secondary: 0x{sc:02X}")
    print(f"Data ({len(data)} bytes): {data.hex() if data else '(none)'}")
    print(f"Response ({len(resp)} bytes): {resp.hex() if resp else '(none)'}")

    # Decode specific messages
    if (pc, sc) == (0xB5, 0x11) and len(data) >= 1:
        query_type = data[0]

        if query_type == 2 and len(resp) >= 5:
            print(f"\n🔍 B511 Type 2 Decoded:")
            print(f"   byte[0] Modulation:     {resp[0]}%")
            print(f"   byte[1] Outdoor cutoff: {resp[1]}°C (RAW, not /2)")
            print(f"   byte[2] Max flow:       {resp[2] / 2.0:.1f}°C")
            print(f"   byte[3] DHW setpoint:   {resp[3] / 2.0:.1f}°C  ← WATCH THIS")
            if len(resp) >= 5:
                print(f"   byte[4] Legionella:     {resp[4] / 2.0:.1f}°C")

    elif (pc, sc) == (0xB5, 0x09) and len(data) >= 2:
        print(f"\n🔍 B509 Room Temp Decoded:")
        print(f"   byte[0] Room temp:      {data[0] / 2.0:.1f}°C  ← WATCH THIS")
        if data[1] != 0xFF:
            adj = int.from_bytes([data[1]], 'little', signed=True)
            print(f"   byte[1] Adjustment:     {adj}")

    print(f"{'=' * 70}")


def main():
    PORT = "/dev/ttyAMA0"

    print("\n" + "🔍 " * 20)
    print("eBUS RAW TELEGRAM DEBUGGER")
    print("🔍 " * 20)
    print("\n📋 Instructions:")
    print("   1. Let this run for 30 seconds")
    print("   2. Change DHW setpoint 45°C → 50°C on MiPro")
    print("   3. Watch for B511 Type 2 messages")
    print("   4. Check if byte[3] changes from 90 (45*2) to 100 (50*2)")
    print("\n" + "=" * 70)

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected! Monitoring all telegrams...\n")

    # Track unique commands
    seen_commands = set()
    telegram_count = 0

    try:
        for telegram in connection.telegram_generator():
            telegram_count += 1

            cmd_key = (telegram.primary_command, telegram.secondary_command)

            # Show ALL B5xx commands
            if telegram.primary_command == 0xB5:
                analyze_telegram(telegram)

            # Track unique commands
            if cmd_key not in seen_commands:
                seen_commands.add(cmd_key)
                print(f"\n🆕 NEW COMMAND: {cmd_key[0]:02X} {cmd_key[1]:02X}")
                analyze_telegram(telegram)

            # Show counter every 20 telegrams
            if telegram_count % 20 == 0:
                print(f"\n📊 Received {telegram_count} telegrams, {len(seen_commands)} unique commands")

    except KeyboardInterrupt:
        print("\n\n⚠️ Stopped")
    finally:
        connection.disconnect()

    print(f"\n📈 Summary:")
    print(f"   Total telegrams: {telegram_count}")
    print(f"   Unique commands: {len(seen_commands)}")
    print(f"\n   Commands seen:")
    for pc, sc in sorted(seen_commands):
        print(f"      {pc:02X} {sc:02X}")


if __name__ == "__main__":
    main()