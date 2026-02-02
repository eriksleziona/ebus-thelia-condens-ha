# src/parser.py

def decode_status01(data):
    # Data usually starts after the length byte (0x09)
    # Example: 09 [64] [4d] 00 f7 ff [5a] 01 00
    if len(data) < 9: return None

    return {
        "flow_temp": data[1] / 2.0,
        "return_temp": data[2] / 2.0,
        "water_pressure": data[6] / 10.0,
        "boiler_status": data[8]  # Bitmask for pump/burner status
    }