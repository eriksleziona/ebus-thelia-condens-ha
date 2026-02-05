#!/usr/bin/env python3
"""
Monitor B511 type 1 to see flow/return temps when actively heating.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebus_core.connection import SerialConnection, ConnectionConfig


def main():
    PORT = "/dev/ttyAMA0"

    print("=" * 70)
    print("🌡️  Flow/Return Temperature Monitor")
    print("=" * 70)
    print("Wait for burner to fire (higher modulation) to see temperature difference")
    print("=" * 70)

    config = ConnectionConfig(port=PORT, baudrate=2400)
    connection = SerialConnection(config)

    if not connection.connect():
        print("❌ Failed to connect!")
        return

    print("✅ Connected!\n")

    count = 0

    try:
        for telegram in connection.telegram_generator():
            cmd = f"{telegram.primary_command:02X}{telegram.secondary_command:02X}"
            data = telegram.data or b''
            resp = telegram.response_data or b''

            # B511 type 1 - temps
            if cmd == "B511" and len(data) >= 1 and data[0] == 1 and len(resp) >= 6:
                count += 1

                flow_raw = resp[0]
                return_raw = resp[1]
                byte2 = resp[2]
                byte3 = resp[3]
                byte5 = resp[5]

                flow = flow_raw / 2.0 if flow_raw != 0xFF else None
                ret = return_raw / 2.0 if return_raw != 0xFF else None

                ts = time.strftime("%H:%M:%S")

                print(f"[{count:3d}] {ts} B511 type 1:")
                print(f"      Raw bytes: {resp[:6].hex()}")
                print(
                    f"      byte[0]={flow_raw:3d} (0x{flow_raw:02X}) → Flow:   {flow}°C" if flow else f"      byte[0]=N/A")
                print(
                    f"      byte[1]={return_raw:3d} (0x{return_raw:02X}) → Return: {ret}°C" if ret else f"      byte[1]=N/A")
                print(f"      byte[2]={byte2:3d} (0x{byte2:02X}) → ÷2={byte2 / 2:.1f}°C  ÷10={byte2 / 10:.1f}bar")
                print(f"      byte[5]={byte5:3d} (0x{byte5:02X}) → Status")

                if flow and ret:
                    diff = flow - ret
                    print(f"      ΔT = {diff:.1f}°C", end="")
                    if abs(diff) < 1:
                        print(" (temps equal - burner likely off/low)")
                    else:
                        print(" ✓")
                print()

            # B504 - modulation
            elif cmd == "B504" and len(resp) >= 1:
                mod = resp[0]
                if mod != 0xFF:
                    print(f"      📊 Modulation: {mod}%\n")

            # B511 type 2 - also check for modulation here
            elif cmd == "B511" and len(data) >= 1 and data[0] == 2 and len(resp) >= 2:
                mod = resp[0]
                if mod != 0xFF and mod <= 100:
                    print(f"      📊 Modulation (from B511 type 2): {mod}%\n")

            if count >= 20:
                break

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted")
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()