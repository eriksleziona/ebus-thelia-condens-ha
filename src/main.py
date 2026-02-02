import serial
import time
import json
import logging
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# --- CONFIGURATION ---
SERIAL_PORT = '/dev/ttyAMA0'
BAUD_RATE = 2400
MQTT_BROKER = "192.168.1.XXX"  # Change to your HA/Mosquitto IP
MQTT_USER = "mqtt_user"  # Optional
MQTT_PASS = "mqtt_password"  # Optional
DEVICE_ID = "saunier_duval_boiler"

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- MQTT SETUP ---
# Using API Version 2 for paho-mqtt 2.0+ compatibility
client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"{DEVICE_ID}_bridge")
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)


def publish_discovery():
    """Configures entities in Home Assistant via MQTT Discovery"""
    base_topic = f"homeassistant/sensor/{DEVICE_ID}"

    sensors = {
        "flow_temp": {"name": "Boiler Flow Temperature", "unit": "°C", "class": "temperature"},
        "return_temp": {"name": "Boiler Return Temperature", "unit": "°C", "class": "temperature"},
        "water_pressure": {"name": "Boiler Water Pressure", "unit": "bar", "class": "pressure"}
    }

    for key, config in sensors.items():
        discovery_topic = f"{base_topic}_{key}/config"
        payload = {
            "name": config["name"],
            "state_topic": f"ebus/{DEVICE_ID}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "unit_of_measurement": config["unit"],
            "device_class": config["class"],
            "unique_id": f"{DEVICE_ID}_{key}",
            "device": {
                "identifiers": [DEVICE_ID],
                "name": "Saunier Duval Thelia Condens",
                "manufacturer": "Saunier Duval"
            }
        }
        client.publish(discovery_topic, json.dumps(payload), retain=True)
    logger.info("Sent discovery payloads to Home Assistant")


# --- EBUSD DECODING LOGIC ---
def calculate_crc(data):
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


def process_status01(data_payload):
    """Parses Saunier Duval B5 11 Status frame"""
    if len(data_payload) < 9: return

    state = {
        "flow_temp": data_payload[1] / 2.0,
        "return_temp": data_payload[2] / 2.0,
        "water_pressure": data_payload[6] / 10.0
    }

    logger.info(f"Update: {state['flow_temp']}°C | {state['water_pressure']} bar")
    client.publish(f"ebus/{DEVICE_ID}/state", json.dumps(state))


# --- MAIN LOOP ---
def main():
    try:
        client.connect(MQTT_BROKER, 1883, 60)
        client.loop_start()
        publish_discovery()

        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        buffer = []

        logger.info("Service started. Listening for eBUS frames...")

        while True:
            byte = ser.read(1)
            if not byte: continue
            val = ord(byte)

            if val == 0xAA:  # Sync byte
                if len(buffer) > 5:
                    # Check for Status01 pattern (10 08 b5 11 ...)
                    if buffer[0:4] == [0x10, 0x08, 0xb5, 0x11]:
                        # Extract the reply (starts after ACK and Length byte)
                        try:
                            # In your log, the reply starts with 0x09 (length)
                            start_idx = buffer.index(0x09)
                            process_status01(buffer[start_idx:])
                        except ValueError:
                            pass
                buffer = []
                continue

            # Byte Destuffing
            if val == 0xA9:
                next_byte = ser.read(1)
                if next_byte:
                    nv = ord(next_byte)
                    if nv == 0x00:
                        buffer.append(0xA9)
                    elif nv == 0x01:
                        buffer.append(0xAA)
                continue

            buffer.append(val)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        ser.close()
        client.loop_stop()


if __name__ == "__main__":
    main()