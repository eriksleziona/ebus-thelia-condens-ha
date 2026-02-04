#!/usr/bin/env python3
"""
Debug capture - shows raw bytes and parsing attempts.
"""

import serial
import time
from datetime import datetime


def calculate_crc(data: bytes) -> int:
    """Calculate eBus CRC-8."""
    crc = 0
    for byte in data:
        for _ in range(8):
            if (crc ^ byte) & 0x80:
                crc = ((crc << 1) ^ 0x9B) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            byte = (byte << 1) & 0xFF
    return crc


def main():
    PORT = "/dev/ttyAMA0"
    SYNC = 0xAA

    print(f"Connecting to {PORT}...")
    ser = serial.Serial(PORT, 2400, timeout=0.1)
    print("Connected! Capturing telegrams...\n")
    print("=" * 80)

    buffer = bytearray()
    count = 0

    try:
        while count < 50:
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                buffer.extend(data)

                # Extract telegrams between SYNC bytes
                while SYNC in buffer:
                    sync_pos = buffer.index(SYNC)

                    if sync_pos > 0:
                        telegram = bytes(buffer[:sync_pos])
                        buffer = buffer[sync_pos:]

                        if len(telegram) >= 5:
                            count += 1
                            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                            # Raw hex
                            raw_hex = ' '.join(f'{b:02X}' for b in telegram)

                            # Parse header
                            src = telegram[0]
                            dst = telegram[1]
                            pb = telegram[2]
                            sb = telegram[3]
                            nn = telegram[4]

                            print(f"\n[{count:3d}] {ts}")
                            print(f"      RAW: {raw_hex}")
                            print(f"      SRC=0x{src:02X} DST=0x{dst:02X} CMD={pb:02X}{sb:02X} NN={nn}")

                            if len(telegram) >= 5 + nn:
                                data_bytes = telegram[5:5 + nn]
                                print(f"      DATA[{nn}]: {data_bytes.hex()}")

                                # CRC check
                                if len(telegram) >= 6 + nn:
                                    received_crc = telegram[5 + nn]
                                    crc_data = telegram[:5 + nn]
                                    expected_crc = calculate_crc(crc_data)

                                    if received_crc == expected_crc:
                                        print(f"      CRC: 0x{received_crc:02X} ✅")
                                    else:
                                        print(f"      CRC: got 0x{received_crc:02X}, expected 0x{expected_crc:02X} ❌")

                                    # Check for slave response
                                    remaining = telegram[6 + nn:]
                                    if remaining:
                                        print(f"      EXTRA: {remaining.hex()}")
                            else:
                                print(f"      ⚠️ Incomplete: have {len(telegram)} bytes, need {5 + nn}")

                    # Skip SYNC bytes
                    while len(buffer) > 0 and buffer[0] == SYNC:
                        buffer.pop(0)

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\nInterrupted")
    finally:
        ser.close()

    print("\n" + "=" * 80)
    print(f"Captured {count} telegrams")


if __name__ == "__main__":
    main()