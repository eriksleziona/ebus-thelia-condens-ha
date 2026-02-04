#!/usr/bin/env python3
"""Live test with improved output."""

import sys
import os
import time

# Add parent directory to path
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

    try:
        count = 0
        last_summary = time.time()

        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            count += 1

            ts = msg.timestamp.strftime("%H:%M:%S")

            if msg.name == "unknown":
                cmd = f"{msg.command[0]:02X}{msg.command[1]:02X}"
                print(f"[{count:3d}] {ts} ❓ Unknown CMD:{cmd} data={msg.query_data.get('raw', '')}")
            else:
                print(f"[{count:3d}] {ts} ✅ {msg}")

            # Print sensor summary every 30 seconds
            if time.time() - last_summary > 30:
                aggregator.print_status()
                last_summary = time.time()

            if count >= 60:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    finally:
        connection.disconnect()

    # Final summary
    aggregator.print_status()

    print(f"\n📈 Stats: {parser.get_stats()}")


if __name__ == "__main__":
    main()