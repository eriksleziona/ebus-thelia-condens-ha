#!/usr/bin/env python3
"""
Actively query for historical/statistics data.
Saunier Duval/Vaillant boilers typically have:
- Burner hours
- Burner starts
- DHW hours
- Error history
"""

import sys
import os
import time
import serial
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.crc import EbusCRC


class StatsQuerier:
    """Query boiler for statistics data."""

    SYNC = 0xAA
    BOILER_ADDR = 0x08
    MIPRO_ADDR = 0x10

    def __init__(self, port: str = "/dev/ttyAMA0"):
        self.port = port
        self.serial = None

    def connect(self) -> bool:
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=2400,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5
            )
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self):
        if self.serial:
            self.serial.close()

    def listen_for_response(self, timeout: float = 2.0) -> dict:
        """Listen for eBus messages and extract relevant ones."""
        buffer = bytearray()
        start = time.time()
        messages = []

        while time.time() - start < timeout:
            if self.serial.in_waiting:
                data = self.serial.read(self.serial.in_waiting)
                buffer.extend(data)

                # Extract messages between SYNC bytes
                while self.SYNC in buffer:
                    sync_pos = buffer.index(self.SYNC)
                    if sync_pos > 5:
                        msg_data = bytes(buffer[:sync_pos])
                        messages.append(msg_data)
                    buffer = buffer[sync_pos:]

                    # Remove leading SYNCs
                    while len(buffer) > 0 and buffer[0] == self.SYNC:
                        buffer.pop(0)

            time.sleep(0.01)

        return messages

    def analyze_messages(self, messages: list) -> None:
        """Analyze captured messages for statistics data."""
        for msg in messages:
            if len(msg) < 6:
                continue

            src = msg[0]
            dst = msg[1]
            pb = msg[2]
            sb = msg[3]
            nn = msg[4]

            cmd = f"{pb:02X}{sb:02X}"

            # Only interested in responses from boiler
            if src != self.BOILER_ADDR:
                continue

            print(f"\nFrom boiler: CMD={cmd} Data={msg[5:5 + nn].hex() if nn > 0 else 'none'}")

            # Try to decode as statistics
            if nn >= 4:
                data = msg[5:5 + nn]

                # Try 16-bit values
                for i in range(0, len(data) - 1, 2):
                    val = int.from_bytes(data[i:i + 2], 'little')
                    if 0 < val < 100000 and val != 0xFFFF:
                        print(f"  uint16[{i}]: {val}")

                # Try 32-bit values
                for i in range(0, len(data) - 3, 4):
                    val = int.from_bytes(data[i:i + 4], 'little')
                    if 0 < val < 10000000 and val != 0xFFFFFFFF:
                        print(f"  uint32[{i}]: {val:,}")


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🔍 Statistics Data Query")
    print("=" * 70)
    print("Listening for statistics in regular traffic...")
    print("(The MiPro might not poll for stats - may need service tool)")
    print("=" * 70)

    querier = StatsQuerier(PORT)

    if not querier.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")

    # Known query patterns for statistics on Vaillant/SD boilers
    stats_patterns = {
        "B511_00": "Extended status (pressure, etc.)",
        "B511_01": "Temperatures",
        "B511_02": "Modulation/setpoints",
        "B512_00": "Possibly statistics type 0",
        "B512_01": "Possibly statistics type 1",
        "B512_02": "Possibly statistics type 2",
        "B512_03": "Possibly statistics type 3",
        "B512_04": "Possibly statistics type 4",
        "B513_00": "Possibly error history 0",
        "B513_01": "Possibly error history 1",
    }

    print("Looking for the following patterns:")
    for pattern, desc in stats_patterns.items():
        print(f"  {pattern}: {desc}")
    print()

    # Collect data for 60 seconds
    print("Collecting data for 60 seconds...")

    all_messages = []
    collected_patterns = {}

    try:
        start = time.time()
        while time.time() - start < 60:
            messages = querier.listen_for_response(timeout=1.0)

            for msg in messages:
                if len(msg) < 6:
                    continue

                src = msg[0]
                dst = msg[1]
                pb = msg[2]
                sb = msg[3]
                nn = msg[4]

                cmd = f"{pb:02X}{sb:02X}"

                # Track queries (from MiPro to boiler)
                if src == 0x10 and dst == 0x08:
                    query_data = msg[5:5 + nn].hex() if nn > 0 else ""
                    pattern_key = f"{cmd}_{query_data}"

                    if pattern_key not in collected_patterns:
                        collected_patterns[pattern_key] = {
                            "cmd": cmd,
                            "query": query_data,
                            "responses": [],
                            "count": 0
                        }
                    collected_patterns[pattern_key]["count"] += 1

                # Track responses (from boiler)
                if src == 0x08:
                    all_messages.append(msg)

            # Show progress
            elapsed = int(time.time() - start)
            print(f"\r  Collected {len(all_messages)} messages in {elapsed}s...", end="", flush=True)

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        querier.disconnect()

    # Analysis
    print("\n\n" + "=" * 70)
    print("📊 ANALYSIS")
    print("=" * 70)

    print("\nQuery patterns seen (MiPro → Boiler):")
    for key, data in sorted(collected_patterns.items()):
        print(f"  {data['cmd']} query={data['query'] or 'none'}: {data['count']}x")

    print("\n" + "-" * 70)
    print("Looking for statistics values in responses...")
    print("-" * 70)

    # Analyze responses for potential counter values
    potential_stats = {}

    for msg in all_messages:
        if len(msg) < 10:
            continue

        nn = msg[4]
        data = msg[5:5 + nn] if nn > 0 else b''

        if len(data) < 4:
            continue

        # Look for 16-bit values that could be counters (100-50000 range)
        for i in range(0, len(data) - 1, 2):
            val = int.from_bytes(data[i:i + 2], 'little')
            if 100 < val < 50000 and val != 0xFFFF:
                key = f"uint16_pos{i}"
                if key not in potential_stats:
                    potential_stats[key] = set()
                potential_stats[key].add(val)

        # Look for 32-bit values that could be counters
        for i in range(0, len(data) - 3, 4):
            val = int.from_bytes(data[i:i + 4], 'little')
            if 100 < val < 10000000 and val != 0xFFFFFFFF:
                key = f"uint32_pos{i}"
                if key not in potential_stats:
                    potential_stats[key] = set()
                potential_stats[key].add(val)

    if potential_stats:
        print("\nPotential counter values found:")
        for key, values in sorted(potential_stats.items()):
            vals_list = sorted(values)
            if len(vals_list) <= 5:
                print(f"  {key}: {vals_list}")
            else:
                print(f"  {key}: {vals_list[:3]} ... ({len(vals_list)} unique values)")
    else:
        print("\nNo obvious counter values found in regular traffic.")
        print("Statistics may require active querying with service commands.")

    print("\n" + "=" * 70)
    print("💡 NOTES:")
    print("=" * 70)
    print("""
Historical data (burner hours, gas consumption) is typically NOT 
in regular polling traffic. Options:

1. Check MiPro controller menu:
   - Often shows statistics in service/info menu

2. Use ebusd with proper CSV definitions:
   - Has dedicated commands for Vaillant/SD statistics

3. The data might be in B512/B513 commands with specific query bytes
   that aren't regularly polled

Your current visible sensors are working correctly. For historical
data, we may need to reverse-engineer the specific query commands
or use ebusd CSV files as reference.
""")


if __name__ == "__main__":
    main()