import serial
import time
import threading
from collections import defaultdict
from src.utils.logger import setup_logger


class EbusDirectReader:
    """Direct eBUS protocol reader without ebusd."""

    # eBUS protocol constants
    SYN = 0xAA  # Synchronization byte
    ESCAPE = 0xA9  # Escape byte
    BROADCAST_ADDR = 0xFE

    def __init__(self, config):
        self.config = config
        self.logger = setup_logger(__name__)
        self.device = config.get('ebus.device', '/dev/ttyS0')
        self.baudrate = 2400  # eBUS standard baud rate
        self.serial = None
        self.running = False
        self.messages = defaultdict(dict)
        self.lock = threading.Lock()

    def connect(self):
        """Open serial connection to eBUS."""
        try:
            self.serial = serial.Serial(
                port=self.device,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            self.logger.info(f"Connected to {self.device} at {self.baudrate} baud")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to {self.device}: {e}")
            return False

    def disconnect(self):
        """Close serial connection."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.logger.info("Disconnected from eBUS")

    def read_byte(self, timeout=1):
        """Read a single byte from eBUS."""
        try:
            if self.serial and self.serial.is_open:
                byte = self.serial.read(1)
                if byte:
                    return byte[0]
            return None
        except Exception as e:
            self.logger.error(f"Error reading byte: {e}")
            return None

    def unescape_byte(self, byte1, byte2):
        """Handle eBUS escape sequences."""
        if byte1 == self.ESCAPE:
            if byte2 == 0x00:
                return 0xA9
            elif byte2 == 0x01:
                return 0xAA
        return None

    def calculate_crc(self, data):
        """Calculate eBUS CRC."""
        crc = 0
        for byte in data:
            crc = (crc + byte) & 0xFF
        return crc

    def parse_temperature(self, high_byte, low_byte):
        """Parse temperature from two bytes."""
        try:
            # Combine bytes (big-endian for eBUS)
            raw_value = (high_byte << 8) | low_byte

            # Convert to signed
            if raw_value > 32767:
                raw_value -= 65536

            # eBUS temperature is typically value/16
            temperature = raw_value / 16.0

            return round(temperature, 1)
        except Exception as e:
            self.logger.error(f"Error parsing temperature: {e}")
            return None

    def parse_pressure(self, byte_value):
        """Parse pressure from byte."""
        try:
            # Pressure is typically value/10
            return round(byte_value / 10.0, 1)
        except Exception as e:
            self.logger.error(f"Error parsing pressure: {e}")
            return None

    def read_message(self):
        """Read and parse one eBUS message."""
        try:
            # Wait for SYN byte
            while True:
                byte = self.read_byte()
                if byte is None:
                    return None
                if byte == self.SYN:
                    break

            # Read QQ (source address)
            qq = self.read_byte()
            if qq is None or qq == self.SYN:
                return None

            # Read ZZ (destination address)
            zz = self.read_byte()
            if zz is None or zz == self.SYN:
                return None

            # Read PB (primary command)
            pb = self.read_byte()
            if pb is None or pb == self.SYN:
                return None

            # Read SB (secondary command)
            sb = self.read_byte()
            if sb is None or sb == self.SYN:
                return None

            # Read NN (number of data bytes)
            nn = self.read_byte()
            if nn is None or nn == self.SYN:
                return None

            # Read data bytes
            data = []
            for i in range(nn):
                byte = self.read_byte()
                if byte is None:
                    return None
                data.append(byte)

            # Read CRC
            crc = self.read_byte()
            if crc is None:
                return None

            # Verify CRC
            message_bytes = [qq, zz, pb, sb, nn] + data
            calculated_crc = self.calculate_crc(message_bytes)

            if calculated_crc != crc:
                self.logger.debug(f"CRC mismatch: got {crc:02X}, expected {calculated_crc:02X}")
                return None

            # Parse message
            message = {
                'source': qq,
                'dest': zz,
                'pb': pb,
                'sb': sb,
                'data': data
            }

            self.logger.debug(
                f"Message: QQ={qq:02X} ZZ={zz:02X} PB={pb:02X} SB={sb:02X} Data={[f'{b:02X}' for b in data]}")

            return message

        except Exception as e:
            self.logger.error(f"Error reading message: {e}")
            return None

    def process_message(self, message):
        """Process and store message data."""
        if not message:
            return

        try:
            pb = message['pb']
            sb = message['sb']
            data = message['data']

            # Vaillant/Saunier Duval messages
            # These are common command codes - adjust based on your heater

            with self.lock:
                # Broadcast messages (destination FE or 00)
                if message['dest'] in [0xFE, 0x00]:
                    # Temperature broadcasts (PB=B5, SB=09)
                    if pb == 0xB5 and sb == 0x09 and len(data) >= 7:
                        if len(data) >= 2:
                            self.messages['flow_temp'] = self.parse_temperature(data[0], data[1])
                        if len(data) >= 4:
                            self.messages['return_temp'] = self.parse_temperature(data[2], data[3])
                        if len(data) >= 6:
                            self.messages['dhw_temp'] = self.parse_temperature(data[4], data[5])
                        if len(data) >= 7:
                            self.messages['water_pressure'] = self.parse_pressure(data[6])

                    # Status broadcasts (PB=B5, SB=05)
                    elif pb == 0xB5 and sb == 0x05 and len(data) >= 1:
                        status = data[0]
                        self.messages['flame_status'] = 'on' if (status & 0x08) else 'off'
                        self.messages['heating_active'] = 'on' if (status & 0x04) else 'off'
                        self.messages['dhw_active'] = 'on' if (status & 0x02) else 'off'

                    # Outside temperature (PB=B5, SB=16)
                    elif pb == 0xB5 and sb == 0x16 and len(data) >= 2:
                        self.messages['outside_temp'] = self.parse_temperature(data[0], data[1])

                    # Modulation level (PB=B5, SB=0D)
                    elif pb == 0xB5 and sb == 0x0D and len(data) >= 1:
                        self.messages['modulation'] = data[0]

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def listen_loop(self):
        """Main listening loop."""
        self.logger.info("Starting eBUS listener")

        while self.running:
            try:
                message = self.read_message()
                if message:
                    self.process_message(message)
            except Exception as e:
                self.logger.error(f"Error in listen loop: {e}")
                time.sleep(1)

    def start_listening(self):
        """Start listening thread."""
        if not self.connect():
            return False

        self.running = True
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()
        self.logger.info("eBUS listener started")
        return True

    def stop_listening(self):
        """Stop listening thread."""
        self.running = False
        if hasattr(self, 'listen_thread'):
            self.listen_thread.join(timeout=5)
        self.disconnect()
        self.logger.info("eBUS listener stopped")

    def get_heater_status(self):
        """Get current heater status from parsed messages."""
        with self.lock:
            status = {
                'flow_temp': self.messages.get('flow_temp'),
                'return_temp': self.messages.get('return_temp'),
                'dhw_temp': self.messages.get('dhw_temp'),
                'water_pressure': self.messages.get('water_pressure'),
                'outside_temp': self.messages.get('outside_temp'),
                'flame_status': self.messages.get('flame_status'),
                'heating_active': self.messages.get('heating_active'),
                'dhw_active': self.messages.get('dhw_active'),
                'modulation': self.messages.get('modulation'),
            }

        return status

    def set_heating_temp(self, temperature):
        """Send heating temperature set command."""
        # This requires knowing the exact command for your heater
        # For now, log that it's not implemented
        self.logger.warning(f"Set temperature to {temperature}°C - command implementation needed")
        return False