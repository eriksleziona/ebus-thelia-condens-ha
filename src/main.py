#!/usr/bin/env python3
import serial
import time
from datetime import datetime
import paho.mqtt.publish as publish

# ============= EDIT ONLY THESE =============
MQTT_BROKER = "192.168.1.100"  # ← YOUR HA IP
MQTT_USER = "mqtt"  # ← change if needed
MQTT_PASS = "mqtt"  # ← change if needed
# ===========================================

SERIAL_PORT = "/dev/serial0"
BAUDRATE = 2400

ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.1)
print(f"{datetime.now().strftime('%H:%M:%S')} → Direct eBUS reader started – no ebusd needed")

auth = {'username': MQTT_USER, 'password': MQTT_PASS} if MQTT_USER else None
buffer = bytearray()

while True:
    try:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting)
            for b in data:
                if b == 0xAA:
                    if len(buffer) >= 11:
                        msg = buffer

                        # Flow temperature
                        if msg.startswith(b'\xB5\x08\x07\x00\x04') and len(msg) >= 15:
                            temp = msg[9] + msg[10] / 10.0
                            publish.single("ebus/thelia/FlowTemp", f"{temp:.1f}", hostname=MQTT_BROKER, auth=auth,
                                           retain=True)
                            print(f"{datetime.now().strftime('%H:%M:%S')} → FlowTemp = {temp:.1f}°C")

                        # Return temperature
                        if msg.startswith(b'\xB5\x08\x07\x00\x05') and len(msg) >= 15:
                            temp = msg[9] + msg[10] / 10.0
                            publish.single("ebus/thelia/ReturnTemp", f"{temp:.1f}", hostname=MQTT_BROKER, auth=auth,
                                           retain=True)

                        # Status + Modulation
                        if msg.startswith(b'\xB5\x08\x07\x00\x18') and len(msg) >= 17:
                            status = "ON" if msg[9] & 0x01 else "OFF"
                            mod = msg[11]
                            publish.single("ebus/thelia/Status", status, hostname=MQTT_BROKER, auth=auth, retain=True)
                            publish.single("ebus/thelia/Modulation", mod, hostname=MQTT_BROKER, auth=auth, retain=True)
                            print(f"{datetime.now().strftime('%H:%M:%S')} → Boiler {status}, Modulation {mod}%")

                        # Flame
                        if msg.startswith(b'\xB5\x08\x07\x00\x1A') and len(msg) >= 13:
                            flame = "ON" if msg[9] > 0 else "OFF"
                            publish.single("ebus/thelia/Flame", flame, hostname=MQTT_BROKER, auth=auth, retain=True)

                    buffer = bytearray()
                buffer.append(b)
    except:
        time.sleep(5)
        try:
            ser.close()
            ser.open()
        except:
            pass
    time.sleep(0.001)
