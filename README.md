

# Complete Guide: Integrating Thelia Condens Saunier Duval 30+ with Home Assistant

This guide will help you integrate your gas heater with Home Assistant using a Raspberry Pi Zero W2, eBUS adapter, and MQTT communication.
Table of Contents

## Hardware Setup
- Software Installation
- Python Module Structure
- MQTT Integration
- Home Assistant Configuration
- Git Repository Structure

## 1. Hardware Setup
### Required Components

- Raspberry Pi Zero W2
- eBUS adapter C6 shield
- UART adapter cable
- Power supply for Raspberry Pi
- MicroSD card (16GB minimum)
Wiring Diagram
```bash 
Thelia Condens eBUS Port
    │
    ├── eBUS+ ──────> C6 Shield (eBUS+)
    ├── eBUS- ──────> C6 Shield (eBUS-)
    └── GND ─────────> C6 Shield (GND)

C6 Shield UART
    │
    ├── TX ──────────> Raspberry Pi GPIO 15 (RXD)
    ├── RX ──────────> Raspberry Pi GPIO 14 (TXD)
    ├── GND ─────────> Raspberry Pi GND (Pin 6)

```

### Step-by-Step Wiring

Power off everything before making connections
Connect eBUS cables from heater service port to C6 shield (eBUS+, eBUS-, GND)
Connect C6 shield to Raspberry Pi GPIO:

- C6 TX → GPIO 15 (RXD, Pin 10)
- C6 RX → GPIO 14 (TXD, Pin 8)
- C6 GND → GND (Pin 6)



Warning: Double-check polarity. Incorrect wiring can damage your equipment.

# 2. Software Installation

## Prepare Raspberry Pi

```bash

# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3-pip git mosquitto mosquitto-clients autoconf build-essential

# Enable UART
sudo raspi-config
# Navigate to: Interface Options → Serial Port
# "Would you like a login shell accessible over serial?" → No
# "Would you like the serial port hardware enabled?" → Yes

# Reboot
sudo reboot

```

## Install ebusd

```bash

# Install dependencies
sudo apt install -y autoconf build-essential cmake

# Clone ebusd repository
cd /tmp
git clone https://github.com/john30/ebusd.git
cd ebusd

# Build and install
./autogen.sh
make -j2
sudo make install

# Create configuration directory
sudo mkdir -p /etc/ebusd
sudo mkdir -p /var/log/ebusd
```
##  Configure ebusd
### Create configuration file:

```bash
sudo nano /etc/default/ebusd
```

### Add the following content:

```bash
# /etc/default/ebusd
EBUSD_OPTS="--device=/dev/serial0 --scanconfig --mqtthost=localhost --mqttport=1883 --mqttjson --log=all:error --log=network:info --log=bus:info"
```

### Create systemd service:

```bash
sudo nano /etc/systemd/system/ebusd.service
```

```bash
[Unit]
Description=ebusd - eBUS daemon
After=network.target mosquitto.service

[Service]
Type=simple
EnvironmentFile=/etc/default/ebusd
ExecStart=/usr/local/bin/ebusd $EBUSD_OPTS
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Enable and start ebusd:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ebusd
sudo systemctl start ebusd

```

### Configure Mosquitto (MQTT Broker)

```bash
sudo nano /etc/mosquitto/mosquitto.conf
```

```editorconfig
# Mosquitto configuration
listener 1883
allow_anonymous true
persistence true
persistence_location /var/lib/mosquitto/
log_dest file /var/log/mosquitto/mosquitto.log

```

### Restart Mosquitto:

```bash

sudo systemctl restart mosquitto
sudo systemctl enable mosquitto
```
