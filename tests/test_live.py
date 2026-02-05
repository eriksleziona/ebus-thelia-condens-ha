#!/usr/bin/env python3
"""Live test with corrected sensor parsing."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, DataAggregator


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🔥 Thelia Condens + MiPro eBus Reader")
    print("=" * 70)
    print(f"\n🔌 Connecting to {PORT}...")

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)
    parser = TheliaParser()
    aggregator = DataAggregator()

    parser.register_callback(aggregator.update)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")
    print("=" * 70)

    try:
        count = 0
        displayed = 0
        last_summary = time.time()
        device_id_count = 0

        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            count += 1

            ts = msg.timestamp.strftime("%H:%M:%S")

            # Skip device_id spam
            if msg.name == "device_id":
                device_id_count += 1
                if device_id_count <= 2:
                    print(f"[{count:3d}] {ts} 🔍 device_id query")
                elif device_id_count == 3:
                    print(f"[{count:3d}] {ts} 🔍 device_id ... (suppressing)")
                continue

            displayed += 1

            if msg.name == "unknown":
                cmd = f"{msg.command[0]:02X}{msg.command[1]:02X}"
                print(f"[{count:3d}] {ts} ❓ {cmd} data={msg.query_data.get('raw', '')}")
            else:
                # Add context for specific messages
                context = ""
                if msg.name == "status_temps":
                    qt = msg.query_data.get("query_type", -1)
                    context = {0: "(extended)", 1: "(temps)", 2: "(other)"}.get(qt, "")

                print(f"[{count:3d}] {ts} ✅ {msg} {context}")

            # Print summary every 30 seconds
            if time.time() - last_summary > 30:
                aggregator.print_status()
                last_summary = time.time()

            if displayed >= 40:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        connection.disconnect()

    aggregator.print_status()

    print(f"\n📈 Stats: {parser.get_stats()}")
    if device_id_count > 0:
        print(f"   (device_id filtered: {device_id_count})")


if __name__ == "__main__":
    main()