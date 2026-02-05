

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


Will be Continued on the End of the Project