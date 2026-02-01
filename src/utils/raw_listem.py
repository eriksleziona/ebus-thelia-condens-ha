import serial

PORT = '/dev/ttyAMA0'
BAUD = 115200  # Change to 2400 for eBUS later

try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    print(f"--- Monitoring raw data on {PORT} ---")

    while True:
        # Read 1 byte at a time
        byte = ser.read(1)
        if byte:
            # Print the Hex and ASCII side-by-side
            hex_val = byte.hex()
            char_val = byte.decode('ascii', errors='replace')
            print(f"Hex: 0x{hex_val} | Char: {char_val}")

except KeyboardInterrupt:
    print("\nStopping monitor...")
finally:
    ser.close()