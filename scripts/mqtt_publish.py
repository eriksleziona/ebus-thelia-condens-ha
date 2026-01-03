import paho.mqtt.publish as publish


def send(base, data):
    for k, v in data.items():
        publish.single(f"{base}/{k}", v, hostname="localhost")
