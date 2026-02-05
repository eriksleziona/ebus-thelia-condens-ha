#!/usr/bin/env python3
"""Live test with alerts."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, DataAggregator
from thelia.alerts import AlertManager, Alert


def on_alert(alert: Alert):
    """Callback for new alerts."""
    print(f"\n{'!' * 50}")
    print(f"{alert}")
    print(f"{'!' * 50}\n")


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🔥 Thelia Condens Monitor with Alerts")
    print("=" * 70)

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)
    parser = TheliaParser()
    aggregator = DataAggregator()
    alert_manager = AlertManager()

    # Register callbacks
    parser.register_callback(aggregator.update)
    alert_manager.register_callback(on_alert)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")
    print("Monitoring for alerts...")
    print("  - Low pressure < 0.8 bar")
    print("  - High pressure > 2.5 bar")
    print("  - High return temp > 55°C (no condensing)")
    print("  - Fault codes")
    print("=" * 70)

    try:
        count = 0
        last_summary = time.time()
        last_alert_check = time.time()
        device_id_count = 0

        for telegram in connection.telegram_generator():
            msg = parser.parse(telegram)
            count += 1

            # Skip device_id spam
            if msg.name == "device_id":
                device_id_count += 1
                continue

            ts = msg.timestamp.strftime("%H:%M:%S")

            # Print message (optional - comment out for quiet mode)
            if msg.name != "unknown":
                print(f"[{count:3d}] {ts} {msg.name}")

            # Check alerts every 10 seconds
            if time.time() - last_alert_check > 10:
                sensors = aggregator.get_all_sensors()
                alert_manager.check_sensors(sensors)
                alert_manager.check_sensor_staleness(sensors)

                # Check fault code if available
                if "boiler.status_code" in sensors:
                    status = sensors["boiler.status_code"]["value"]
                    alert_manager.check_fault_code(status)

                last_alert_check = time.time()

            # Print status every 60 seconds
            if time.time() - last_summary > 60:
                aggregator.print_status()
                alert_manager.print_status()
                last_summary = time.time()

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        connection.disconnect()

    # Final summary
    aggregator.print_status()
    alert_manager.print_status()

    print(f"\n📈 Stats: {parser.get_stats()}")


if __name__ == "__main__":
    main()