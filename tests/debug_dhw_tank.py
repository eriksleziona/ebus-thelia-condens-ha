#!/usr/bin/env python3
"""
Find DHW Tank/Cylinder temperature for system boiler.
The tank has its own NTC sensor - we need to find which byte it is.
"""

import sys
import os
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🛢️  DHW Cylinder/Tank Temperature Finder")
    print("=" * 70)
    print("""
System boiler with storage cylinder detected.

The tank has an NTC temperature sensor. We need to find which
eBus byte contains this value.

Expected behavior:
- Tank temp should be stable around 45°C (your setpoint)
- Will drop when you use hot water
- Will rise when boiler reheats the tank
- Will reach 70°C during weekly Legionella cycle

Monitor all temperature-like values to find the tank sensor.
""")
    print("=" * 70)

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")

    # Track all potential temperature values
    temp_tracking = defaultdict(lambda: {"values": [], "min": 999, "max": -999})

    count = 0
    start_time = time.time()
    last_print = time.time()

    # Store last response for each message type to detect changes
    last_responses = {}

    try:
        for telegram in connection.telegram_generator():
            cmd = f"{telegram.primary_command:02X}{telegram.secondary_command:02X}"
            data = telegram.data or b''
            resp = telegram.response_data or b''

            if not resp or len(resp) < 2:
                continue

            count += 1
            ts = datetime.now().strftime("%H:%M:%S")

            # Create message key
            query_byte = data[0] if data else 0xFF
            msg_key = f"{cmd}_Q{query_byte:02X}"

            # Check for changes
            prev_resp = last_responses.get(msg_key, b'')
            has_changes = prev_resp and prev_resp != resp
            last_responses[msg_key] = resp

            # Track all temperature-like values (10-80°C when ÷2)
            for i, byte_val in enumerate(resp):
                if byte_val == 0xFF or byte_val == 0x00:
                    continue

                temp = byte_val / 2.0

                # Only track reasonable temperatures
                if 10 <= temp <= 85:
                    key = f"{msg_key}_B{i}"
                    temp_tracking[key]["values"].append(temp)
                    temp_tracking[key]["min"] = min(temp_tracking[key]["min"], temp)
                    temp_tracking[key]["max"] = max(temp_tracking[key]["max"], temp)
                    temp_tracking[key]["last"] = temp
                    temp_tracking[key]["last_raw"] = byte_val

            # Print status every 10 seconds
            if time.time() - last_print > 10:
                print(f"\n[{ts}] Monitored {count} messages...")
                print("Current temperature-like values:")

                # Group by likely purpose
                for key, data in sorted(temp_tracking.items()):
                    if len(data["values"]) > 0:
                        last = data["last"]
                        min_v = data["min"]
                        max_v = data["max"]
                        variance = max_v - min_v

                        # Highlight values around 45°C (DHW setpoint)
                        marker = ""
                        if 40 <= last <= 50:
                            marker = " ← Possible DHW tank temp!"
                        elif 55 <= last <= 75:
                            marker = " ← Could be Legionella cycle"
                        elif variance > 2:
                            marker = " ← VALUE CHANGING!"

                        print(f"  {key}: {last:.1f}°C (range: {min_v:.1f}-{max_v:.1f}){marker}")

                last_print = time.time()

            # Run for 2 minutes
            if time.time() - start_time > 120:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted")
    finally:
        connection.disconnect()

    # Final Analysis
    print("\n" + "=" * 70)
    print("📊 FINAL ANALYSIS")
    print("=" * 70)

    print("\n🔍 All temperature values found:\n")

    # Sort by likelihood of being DHW tank temp
    candidates = []

    for key, data in temp_tracking.items():
        if len(data["values"]) >= 3:  # Need some samples
            last = data["last"]
            min_v = data["min"]
            max_v = data["max"]
            variance = max_v - min_v
            avg = sum(data["values"]) / len(data["values"])

            # Score likelihood of being DHW tank temp
            score = 0
            reason = []

            # Near 45°C (user's setpoint)
            if 40 <= avg <= 50:
                score += 10
                reason.append("near 45°C setpoint")

            # Stable (tank temp shouldn't vary much)
            if variance < 3:
                score += 5
                reason.append("stable")

            # Not from B511_Q01 (those are flow/return temps)
            if "B511_Q01" not in key:
                score += 2

            # From B511_Q00 (extended status often has DHW)
            if "B511_Q00" in key:
                score += 3
                reason.append("from extended status")

            # From B512 (DHW specific commands)
            if "B512" in key:
                score += 5
                reason.append("from B512 (DHW command)")

            candidates.append({
                "key": key,
                "last": last,
                "min": min_v,
                "max": max_v,
                "avg": avg,
                "variance": variance,
                "samples": len(data["values"]),
                "score": score,
                "reasons": reason
            })

    # Sort by score
    candidates.sort(key=lambda x: -x["score"])

    print("Ranked by likelihood of being DHW tank temperature:\n")

    for i, c in enumerate(candidates[:10]):
        print(f"  {i + 1}. {c['key']}")
        print(f"     Current: {c['last']:.1f}°C, Range: {c['min']:.1f}-{c['max']:.1f}°C")
        print(f"     Score: {c['score']}, Reasons: {', '.join(c['reasons']) if c['reasons'] else 'none'}")
        print()

    print("=" * 70)
    print("💡 INTERPRETATION GUIDE:")
    print("=" * 70)
    print("""
DHW Tank Temperature candidates should:
1. Be stable around your setpoint (45°C)
2. NOT be the flow/return temps (B511_Q01_B0/B1)
3. Often in B511_Q00 (extended status) or B512 (DHW specific)

To verify:
- Run hot water → tank temp should DROP
- Wait for reheat → tank temp should RISE to 45°C
- During Legionella cycle → should reach 70°C
""")
    print("=" * 70)


if __name__ == "__main__":
    main()