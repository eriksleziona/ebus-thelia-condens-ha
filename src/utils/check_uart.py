import serial
import time

# We try serial0 (the default alias)
port = '/dev/serial0'

print(f"--- Starting Loopback Test on {port} ---")

try:
    # Initialize serial
    ser = serial.Serial(port, 115200, timeout=2)

    # Clear buffers
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    test_string = "PI_UART_ALIVE"
    print(f"Sending: {test_string}")

    ser.write(test_string.encode())
    time.sleep(0.1)  # Give hardware a moment

    response = ser.read(len(test_string)).decode()

    if response == test_string:
        print("✅ SUCCESS: Data looped back perfectly!")
    elif len(response) > 0:
        print(f"⚠️ PARTIAL: Received '{response}' (Check baud rate/wiring)")
    else:
        print("❌ FAIL: Received nothing. The signal is lost.")

except Exception as e:
    print(f"‼️ ERROR: {e}")

finally:
    if 'ser' in locals():
        ser.close()