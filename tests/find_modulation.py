import sys
import time


# Helper to decode eBUS bytes
def parse_msg(hex_str):
    # Looking for: AA [Src] [Dst] B5 09 [Len] [Data...] [CRC]
    # We want Src=08 (Boiler) and Cmd=B509

    parts = hex_str.strip().split()
    if len(parts) < 10: return

    # Check for Sync (AA) and Src (08) and Command (B5 09)
    try:
        if parts[1] == "08" and parts[3] == "b5" and parts[4] == "09":
            data_len = int(parts[5], 16)
            data = parts[6:6 + data_len]

            print(f"\n🎯 FOUND B5 09 (Service Info):")
            print(f"Raw Data: {' '.join(data)}")

            # Modulation Candidates
            # Usually it is Byte 0, Byte 1, or Byte 2
            for i, b in enumerate(data):
                val = int(b, 16)
                print(f"  Byte {i}: {b} (Hex) -> {val} (Decimal)")
                if 10 <= val <= 100:
                    print(f"    ^-- CANDIDATE for Modulation? ({val}%)")
                if val > 100:
                    print(f"    ^-- CANDIDATE for Fan RPM? ({val * 10}?)")

    except Exception as e:
        pass


print("🔍 Searching for Modulation in B5 09 messages...")
print("Please trigger a specific boiler state (e.g. Hot Water) to see changes.")
print("Press Ctrl+C to stop.")

# Read from the pipe (run this via: cat /dev/ttyAMA0 | xxd | python find_modulation.py)
# OR we can just read the raw stream directly in python for you:

with open('/dev/ttyAMA0', 'rb', buffering=0) as f:
    buffer = []
    while True:
        byte = f.read(1)
        if not byte: continue

        hex_byte = byte.hex()

        if hex_byte == "aa":
            # Process the previous packet if it looks complete
            if len(buffer) > 6:
                parse_msg("aa " + " ".join(buffer))
            buffer = []
        else:
            buffer.append(hex_byte)