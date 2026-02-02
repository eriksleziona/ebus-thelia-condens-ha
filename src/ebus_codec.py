# src/ebus_codec.py

def calculate_crc(data):
    """Standard eBUS CRC-8 (Polynomial 0x19)"""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x19
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


def destuff(raw_buffer):
    """Removes 0xA9 escaping from the eBUS stream"""
    destuffed = []
    skip_next = False
    for i in range(len(raw_buffer)):
        if skip_next:
            skip_next = False
            continue
        if raw_buffer[i] == 0xA9:
            if i + 1 < len(raw_buffer):
                if raw_buffer[i + 1] == 0x00:
                    destuffed.append(0xA9)
                elif raw_buffer[i + 1] == 0x01:
                    destuffed.append(0xAA)
                skip_next = True
        else:
            destuffed.append(raw_buffer[i])
    return destuffed
