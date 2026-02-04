#!/usr/bin/env python3
"""
Raw byte capture with no processing - to debug CRC issues.
"""

import serial
import time
from datetime import datetime


def crc8(data: bytes) -> int:
    """Calculate eBus CRC-8 with polynomial 0x9B."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x9B) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def unescape(data: bytes) -> bytes:
    """Remove eBus escape sequences."""
    result = bytearray()
    i = 0
    while i < len(data):
        if i < len(data) - 1 and data[i] == 0xA9:
            if data[i + 1] == 0x00:
                result.append(0xA9)
                i += 2
            elif data[i + 1] == 0x01:
                result.append(0xAA)
                i += 2
            else:
                result.append(data[i])
                i += 1
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


def main():
    PORT = "/dev/ttyAMA0"
    SYNC = 0xAA

    print(f"Opening {PORT}...")
    ser = serial.Serial(PORT, 2400, timeout=0.1)
    print("Capturing raw bytes (Ctrl+C to stop)...\n")
    print("=" * 90)

    buffer = bytearray()
    count = 0

    try:
        while count < 30:
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                buffer.extend(data)

                while SYNC in buffer:
                    sync_pos = buffer.index(SYNC)

                    if sync_pos > 0:
                        raw = bytes(buffer[:sync_pos])
                        buffer = buffer[sync_pos:]

                        if len(raw) >= 5:
                            count += 1
                            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                            # Show raw hex
                            raw_hex = ' '.join(f'{b:02X}' for b in raw)

                            # Check for escape sequences
                            has_escapes = 0xA9 in raw

                            # Unescape
                            unesc = unescape(raw)

                            print(f"\n[{count:2d}] {ts}")
                            print(f"    RAW ({len(raw):2d} bytes): {raw_hex}")

                            if has_escapes:
                                unesc_hex = ' '.join(f'{b:02X}' for b in unesc)
                                print(f"    ESC ({len(unesc):2d} bytes): {unesc_hex}")

                            # Parse header
                            src = unesc[0]
                            dst = unesc[1]
                            pb = unesc[2]
                            sb = unesc[3]
                            nn = unesc[4]

                            print(f"    QQ={src:02X} ZZ={dst:02X} PB={pb:02X} SB={sb:02X} NN={nn}")

                            # Expected length for master telegram: 5 + NN + 1 (CRC)
                            master_len = 5 + nn + 1

                            if len(unesc) >= master_len:
                                master_data = unesc[5:5 + nn]
                                master_crc = unesc[5 + nn]

                                # Calculate expected CRC
                                crc_input = unesc[:5 + nn]
                                calc_crc = crc8(crc_input)

                                crc_ok = "✅" if calc_crc == master_crc else "❌"

                                print(f"    DATA ({nn} bytes): {master_data.hex()}")
                                print(f"    CRC: recv=0x{master_crc:02X} calc=0x{calc_crc:02X} {crc_ok}")

                                # Show CRC calculation input
                                crc_hex = ' '.join(f'{b:02X}' for b in crc_input)
                                print(f"    CRC over: {crc_hex}")

                                # Slave response
                                if len(unesc) > master_len:
                                    slave_part = unesc[master_len:]
                                    print(f"    SLAVE ({len(slave_part)} bytes): {slave_part.hex()}")

                                    # Try to parse slave response
                                    if len(slave_part) >= 1:
                                        ack = slave_part[0]
                                        if ack == 0x00:
                                            print(f"    SLAVE ACK: 0x00 ✅")
                                            if len(slave_part) >= 2:
                                                snn = slave_part[1]
                                                print(f"    SLAVE NN: {snn}")
                                                if len(slave_part) >= 2 + snn + 1:
                                                    sdata = slave_part[2:2 + snn]
                                                    scrc = slave_part[2 + snn]
                                                    scalc = crc8(slave_part[1:2 + snn])
                                                    scrc_ok = "✅" if scalc == scrc else "❌"
                                                    print(f"    SLAVE DATA: {sdata.hex()}")
                                                    print(
                                                        f"    SLAVE CRC: recv=0x{scrc:02X} calc=0x{scalc:02X} {scrc_ok}")
                                        else:
                                            print(f"    SLAVE ACK: 0x{ack:02X} (not ACK)")
                            else:
                                print(f"    ⚠️  Too short: have {len(unesc)}, need {master_len}")

                    while len(buffer) > 0 and buffer[0] == SYNC:
                        buffer.pop(0)

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\nStopped")
    finally:
        ser.close()

    print("=" * 90)


if __name__ == "__main__":
    main()