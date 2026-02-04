#!/usr/bin/env python3
"""Live test with improved output and filtering."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, DataAggregator


def main():
    PORT = "/dev/ttyAMA0"

    print(f"🔌 Connecting to {PORT}...")

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)
    parser = TheliaParser()
    aggregator = DataAggregator()

    parser.register_callback(aggregator.update)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected! Reading eBus data...\n")
    print("=" * 70)
    print("(device_id messages are filtered for readability)")
    print("=" * 70)

    try:
        count = 0
        displayed = 0
        last_summary = time.time()

        # Track message types to avoid spam
        msg_counts = {}

        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            count += 1

            ts = msg.timestamp.strftime("%H:%M:%S")

            # Skip device_id spam (show only first few)
            if msg.name == "device_id":
                msg_counts["device_id"] = msg_counts.get("device_id", 0) + 1
                if msg_counts["device_id"] <= 3:
                    print(f"[{count:3d}] {ts} 🔍 device_id (query)")
                elif msg_counts["device_id"] == 4:
                    print(f"[{count:3d}] {ts} 🔍 device_id ... (suppressing further)")
                continue

            displayed += 1

            if msg.name == "unknown":
                cmd = f"{msg.command[0]:02X}{msg.command[1]:02X}"
                print(f"[{count:3d}] {ts} ❓ Unknown CMD:{cmd} data={msg.query_data.get('raw', '')}")
            else:
                # Add context for status_temps based on query_type
                if msg.name == "status_temps":
                    qt = msg.query_data.get("query_type", -1)
                    context = {0: "(extended status)", 1: "(flow temp)", 2: "(setpoints)"}.get(qt, "")
                    print(f"[{count:3d}] {ts} ✅ {msg} {context}")
                else:
                    print(f"[{count:3d}] {ts} ✅ {msg}")

            # Print sensor summary every 30 seconds
            if time.time() - last_summary > 30:
                aggregator.print_status()
                last_summary = time.time()

            if displayed >= 40:  # Count only displayed messages
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    finally:
        connection.disconnect()

    # Final summary
    aggregator.print_status()

    print(f"\n📈 Stats: {parser.get_stats()}")
    print(f"   (device_id messages filtered: {msg_counts.get('device_id', 0)})")


if __name__ == "__main__":
    main()