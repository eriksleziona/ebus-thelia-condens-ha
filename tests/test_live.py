#!/usr/bin/env python3
"""Live test of the parser."""

import sys
import os
import time
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, DataAggregator


def main():
    PORT = "/dev/ttyAMA0"

    print(f"Connecting to {PORT}...")

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)
    parser = TheliaParser()
    aggregator = DataAggregator()

    parser.register_callback(aggregator.update)

    if not connection.connect():
        print("Failed to connect!")
        return

    print("Connected! Parsing messages...\n")
    print("=" * 70)

    try:
        count = 0
        last_summary = time.time()

        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            count += 1

            ts = msg.timestamp.strftime("%H:%M:%S")

            if msg.name == "unknown":
                print(f"[{count:3d}] {ts} ❓ CMD:{msg.command[0]:02X}{msg.command[1]:02X}")
            else:
                print(f"[{count:3d}] {ts} ✅ {msg}")

            if time.time() - last_summary > 30:
                print("\n" + "-" * 50)
                print("📊 Current Sensors:")
                for key, data in aggregator.get_all_sensors().items():
                    if "value" in data:
                        print(f"   {key}: {data['value']} {data.get('unit', '')}")
                print("-" * 50 + "\n")
                last_summary = time.time()

            if count >= 50:
                break

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        connection.disconnect()

    print("\n" + "=" * 70)
    print(f"Parsed {count} messages")
    print(f"Stats: {parser.get_stats()}")


if __name__ == "__main__":
    main()