#!/usr/bin/env python3
"""
Discover historical data commands (gas consumption, burner hours, etc.)
"""

import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig


def analyze_response(cmd: str, query_data: bytes, resp: bytes) -> dict:
    """Try to decode response as various data types."""
    result = {
        "cmd": cmd,
        "query": query_data.hex() if query_data else "",
        "response": resp.hex() if resp else "",
        "decoded": {}
    }

    if not resp:
        return result

    # Try different decodings
    decoded = result["decoded"]

    # Individual bytes
    for i, b in enumerate(resp):
        if b != 0xFF:
            decoded[f"byte[{i}]"] = b

    # 16-bit values (counters are often 16 or 32 bit)
    for i in range(0, len(resp) - 1, 2):
        val = int.from_bytes(resp[i:i + 2], 'little')
        if val != 0xFFFF and val != 0:
            decoded[f"uint16[{i}]"] = val

    # 32-bit values (for large counters like gas consumption)
    for i in range(0, len(resp) - 3, 4):
        val = int.from_bytes(resp[i:i + 4], 'little')
        if val != 0xFFFFFFFF and val != 0:
            decoded[f"uint32[{i}]"] = val

    return result


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🔍 Historical Data Discovery")
    print("=" * 70)
    print("Looking for gas consumption, burner hours, starts, etc.")
    print("=" * 70)

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")

    # Commands we're interested in
    target_cmds = {
        "B512": "Possibly counters/statistics",
        "B513": "Possibly history/logs",
        "B514": "Possibly schedules/programs",
        "B515": "Possibly error history",
        "B517": "Possibly more stats",
        "B518": "Possibly more stats",
        "B519": "Possibly more stats",
        "B51A": "Possibly more stats",
    }

    # Also look for these patterns (common for statistics)
    interesting_patterns = {}

    count = 0
    start_time = time.time()

    try:
        for telegram in connection.telegram_generator():
            count += 1
            cmd = f"{telegram.primary_command:02X}{telegram.secondary_command:02X}"
            data = telegram.data or b''
            resp = telegram.response_data or b''

            ts = datetime.now().strftime("%H:%M:%S")

            # Check if this is an interesting command
            if cmd in target_cmds or cmd.startswith("B5"):
                analysis = analyze_response(cmd, data, resp)

                # Skip if we've seen this exact pattern
                pattern_key = f"{cmd}_{data.hex()}"
                if pattern_key in interesting_patterns:
                    interesting_patterns[pattern_key]["count"] += 1
                    continue

                interesting_patterns[pattern_key] = {
                    "count": 1,
                    "analysis": analysis
                }

                print(f"\n[{count:4d}] {ts} Command: {cmd}")
                print(f"       Query: {data.hex() if data else '(none)'}")
                print(f"       Response ({len(resp)} bytes): {resp.hex() if resp else '(none)'}")

                if analysis["decoded"]:
                    print(f"       Decoded values:")
                    for k, v in analysis["decoded"].items():
                        # Format large numbers nicely
                        if isinstance(v, int) and v > 1000:
                            print(
                                f"         {k}: {v:,} ({v} hours = {v / 24:.0f} days)" if "32" in k else f"         {k}: {v:,}")
                        else:
                            print(f"         {k}: {v}")

            # Run for 3 minutes max
            if time.time() - start_time > 180:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        connection.disconnect()

    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY - Unique Command Patterns Found")
    print("=" * 70)

    for pattern_key, data in sorted(interesting_patterns.items()):
        cmd = data["analysis"]["cmd"]
        query = data["analysis"]["query"]
        response = data["analysis"]["response"]
        decoded = data["analysis"]["decoded"]

        print(f"\n{cmd} query={query} (seen {data['count']}x)")
        print(f"  Response: {response}")
        if decoded:
            print(f"  Decoded:")
            for k, v in decoded.items():
                if isinstance(v, int) and v > 1000:
                    print(f"    {k}: {v:,}")
                else:
                    print(f"    {k}: {v}")

    print("\n" + "=" * 70)
    print("💡 Look for:")
    print("   - Large numbers (10000+) = burner hours or gas consumption")
    print("   - Medium numbers (100-10000) = burner starts")
    print("   - Sequential numbers = counters")
    print("=" * 70)


if __name__ == "__main__":
    main()