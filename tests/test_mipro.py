#!/usr/bin/env python3
"""
Capture and analyze MiPro controller messages.
"""

import sys
import os
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from ebus_core.telegram import EbusTelegram

# Known eBus addresses
ADDRESSES = {
    0x00: "Broadcast/Unknown",
    0x08: "Boiler (Thelia)",
    0x10: "MiPro Controller",
    0x18: "Secondary Controller?",
    0xFE: "Broadcast",
}


def get_device_name(addr: int) -> str:
    return ADDRESSES.get(addr, f"Unknown-0x{addr:02X}")


def analyze_telegram(telegram: EbusTelegram) -> dict:
    """Analyze a telegram and extract useful info."""
    info = {
        "source": get_device_name(telegram.source),
        "source_addr": telegram.source,
        "dest": get_device_name(telegram.destination),
        "dest_addr": telegram.destination,
        "cmd": f"{telegram.primary_command:02X}{telegram.secondary_command:02X}",
        "data": telegram.data.hex() if telegram.data else "",
        "data_len": len(telegram.data) if telegram.data else 0,
        "response": telegram.response_data.hex() if telegram.response_data else "",
        "response_len": len(telegram.response_data) if telegram.response_data else 0,
    }
    return info


def decode_known_values(cmd: str, data: bytes, response: bytes) -> dict:
    """Try to decode known values from the data."""
    values = {}

    if cmd == "B509" and len(data) >= 2:
        # Room temperature from MiPro
        room_temp = data[0] / 2.0
        values["room_temp"] = f"{room_temp:.1f}°C"
        values["byte1"] = data[1]

    elif cmd == "B511" and len(data) >= 1:
        query_type = data[0]
        values["query_type"] = query_type

        if response and len(response) >= 2:
            temp = int.from_bytes(response[0:2], 'little', signed=True) / 256.0
            values["temp1"] = f"{temp:.1f}°C"

    elif cmd == "B510" and len(data) >= 3:
        setpoint = data[2] / 2.0
        values["flow_setpoint"] = f"{setpoint:.1f}°C"

    elif cmd == "B516" and len(data) >= 8:
        # DateTime - try BCD
        def bcd(b):
            return ((b >> 4) * 10) + (b & 0x0F)

        try:
            values["time"] = f"{bcd(data[3]):02d}:{bcd(data[2]):02d}:{bcd(data[1]):02d}"
            values["date"] = f"20{bcd(data[7]):02d}-{bcd(data[5]):02d}-{bcd(data[4]):02d}"
        except:
            pass

    elif cmd == "B504" and response and len(response) >= 1:
        values["modulation"] = f"{response[0]}%"

    return values


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 80)
    print("🔍 MiPro Controller Message Analyzer")
    print("=" * 80)
    print(f"\nConnecting to {PORT}...")

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")
    print("Capturing messages... (Ctrl+C to stop and show summary)\n")
    print("-" * 80)

    # Statistics
    messages_by_cmd = defaultdict(list)
    messages_by_source = defaultdict(int)
    messages_by_direction = defaultdict(int)

    count = 0
    start_time = time.time()

    try:
        for telegram in connection.telegram_generator():
            count += 1
            info = analyze_telegram(telegram)

            # Track statistics
            messages_by_cmd[info["cmd"]].append(info)
            messages_by_source[info["source"]] += 1
            direction = f"{info['source']} → {info['dest']}"
            messages_by_direction[direction] += 1

            # Decode values
            data = telegram.data if telegram.data else b''
            resp = telegram.response_data if telegram.response_data else b''
            decoded = decode_known_values(info["cmd"], data, resp)

            # Print message
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # Color/emoji based on source
            if telegram.source == 0x10:
                src_icon = "📱"  # MiPro
            elif telegram.source == 0x08:
                src_icon = "🔥"  # Boiler
            else:
                src_icon = "❓"

            print(f"[{count:4d}] {ts} {src_icon} {info['source']:20s} → {info['dest']:15s} "
                  f"CMD:{info['cmd']} ", end="")

            if info['data']:
                print(f"DATA:{info['data'][:20]}", end="")
            if info['response']:
                print(f" → RESP:{info['response'][:20]}", end="")

            # Print decoded values
            if decoded:
                decoded_str = " | " + ", ".join(f"{k}={v}" for k, v in decoded.items())
                print(decoded_str, end="")

            print()

            # Run for 2 minutes or 200 messages
            if count >= 200 or (time.time() - start_time) > 120:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    finally:
        connection.disconnect()

    # Print summary
    elapsed = time.time() - start_time

    print("\n" + "=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)

    print(f"\n⏱️  Duration: {elapsed:.1f} seconds")
    print(f"📨 Total messages: {count}")

    print("\n📍 Messages by Source:")
    for source, cnt in sorted(messages_by_source.items(), key=lambda x: -x[1]):
        pct = cnt / count * 100
        print(f"   {source:25s}: {cnt:4d} ({pct:5.1f}%)")

    print("\n🔄 Message Flow:")
    for direction, cnt in sorted(messages_by_direction.items(), key=lambda x: -x[1]):
        pct = cnt / count * 100
        print(f"   {direction:45s}: {cnt:4d} ({pct:5.1f}%)")

    print("\n📝 Commands Seen:")
    for cmd, msgs in sorted(messages_by_cmd.items()):
        cnt = len(msgs)
        # Show sample data
        sample = msgs[0]
        print(f"   {cmd}: {cnt:4d} messages")
        print(f"        Sample: DATA={sample['data'][:30] if sample['data'] else 'none':30s} "
              f"RESP={sample['response'][:30] if sample['response'] else 'none'}")

    print("\n" + "=" * 80)
    print("🔍 DETAILED COMMAND ANALYSIS")
    print("=" * 80)

    for cmd, msgs in sorted(messages_by_cmd.items()):
        print(f"\n--- Command {cmd} ({len(msgs)} messages) ---")

        # Show unique data patterns
        data_patterns = defaultdict(int)
        resp_patterns = defaultdict(int)

        for m in msgs:
            if m['data']:
                data_patterns[m['data']] += 1
            if m['response']:
                resp_patterns[m['response']] += 1

        print("  Data patterns:")
        for pattern, cnt in sorted(data_patterns.items(), key=lambda x: -x[1])[:5]:
            print(f"    {pattern}: {cnt}x")

        if resp_patterns:
            print("  Response patterns:")
            for pattern, cnt in sorted(resp_patterns.items(), key=lambda x: -x[1])[:5]:
                print(f"    {pattern}: {cnt}x")


if __name__ == "__main__":
    main()