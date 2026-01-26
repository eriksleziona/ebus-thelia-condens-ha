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
        self.raw_bytes_received = 0
        self.syn_bytes_seen = 0

    def connect(self):
        """Open serial connection to eBUS."""
        try:
            self.serial = serial.Serial(
                port=self.device,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1  # Short timeout for responsive reading
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
            self.logger.info(
                f"Disconnected from eBUS - received {self.raw_bytes_received} bytes, {self.syn_bytes_seen} SYN")

    def read_byte(self, timeout=0.1):
        """Read a single byte from eBUS."""
        try:
            if self.serial and self.serial.is_open:
                byte = self.serial.read(1)
                if byte:
                    self.raw_bytes_received += 1
                    return byte[0]
            return None
        except Exception as e:
            self.logger.error(f"Error reading byte: {e}")
            return None

    def listen_raw(self, duration=10):
        """Listen to raw bytes and show what's being received."""
        self.logger.info(f"Listening to raw eBUS data for {duration} seconds...")
        buffer = []
        start_time = time.time()

        while time.time() - start_time < duration:
            byte = self.read_byte()
            if byte is not None:
                buffer.append(byte)

                # Log every 50 bytes or when we see SYN
                if len(buffer) >= 50 or byte == self.SYN:
                    hex_str = ' '.join([f'{b:02X}' for b in buffer])
                    self.logger.info(f"Raw bytes: {hex_str}")
                    buffer = []

        if buffer:
            hex_str = ' '.join([f'{b:02X}' for b in buffer])
            self.logger.info(f"Raw bytes: {hex_str}")

        self.logger.info(f"Total bytes received: {self.raw_bytes_received}")

    def parse_temperature(self, high_byte, low_byte):
        """Parse temperature from two bytes."""
        try:
            # Try both big-endian and little-endian
            # Big-endian (standard eBUS)
            raw_value = (high_byte << 8) | low_byte

            # Convert to signed
            if raw_value > 32767:
                raw_value -= 65536

            # eBUS temperature is typically value/16
            temperature = raw_value / 16.0

            # Sanity check: temperature should be -50 to 150°C
            if -50 <= temperature <= 150:
                return round(temperature, 1)

            # Try little-endian
            raw_value = (low_byte << 8) | high_byte
            if raw_value > 32767:
                raw_value -= 65536
            temperature = raw_value / 16.0

            if -50 <= temperature <= 150:
                return round(temperature, 1)

            return None

        except Exception as e:
            self.logger.error(f"Error parsing temperature: {e}")
            return None

    def read_message(self):
        """Read and parse one eBUS message."""
        try:
            # Wait for SYN byte
            syn_timeout = time.time() + 2
            while time.time() < syn_timeout:
                byte = self.read_byte()
                if byte is None:
                    continue
                if byte == self.SYN:
                    self.syn_bytes_seen += 1
                    break
            else:
                return None  # No SYN found

            # Read QQ (source address)
            qq = self.read_byte(timeout=0.05)
            if qq is None or qq == self.SYN:
                return None

            # Read ZZ (destination address)
            zz = self.read_byte(timeout=0.05)
            if zz is None or zz == self.SYN:
                return None

            # Read PB (primary command)
            pb = self.read_byte(timeout=0.05)
            if pb is None or pb == self.SYN:
                return None

            # Read SB (secondary command)
            sb = self.read_byte(timeout=0.05)
            if sb is None or sb == self.SYN:
                return None

            # Read NN (number of data bytes)
            nn = self.read_byte(timeout=0.05)
            if nn is None or nn == self.SYN or nn > 16:  # Sanity check
                return None

            # Read data bytes
            data = []
            for i in range(nn):
                byte = self.read_byte(timeout=0.05)
                if byte is None:
                    return None
                data.append(byte)

            # Read CRC
            crc = self.read_byte(timeout=0.05)
            if crc is None:
                return None

            # Parse message (we'll skip CRC check for now to see what we're getting)
            message = {
                'source': qq,
                'dest': zz,
                'pb': pb,
                'sb': sb,
                'data': data,
                'crc': crc
            }

            self.logger.info(
                f"Msg: QQ={qq:02X} ZZ={zz:02X} PB={pb:02X} SB={sb:02X} NN={nn:02X} Data=[{' '.join([f'{b:02X}' for b in data])}] CRC={crc:02X}")

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

            with self.lock:
                # Look for temperature-like data in ANY message
                # This is a discovery mode to find what your heater sends

                if len(data) >= 2:
                    # Try parsing first two bytes as temperature
                    temp = self.parse_temperature(data[0], data[1])
                    if temp is not None:
                        key = f"temp_{pb:02X}_{sb:02X}"
                        self.messages[key] = temp
                        self.logger.info(f"Found temperature {temp}°C in PB={pb:02X} SB={sb:02X}")

                # Store raw message for analysis
                msg_key = f"{pb:02X}_{sb:02X}"
                self.messages[f"raw_{msg_key}"] = {
                    'pb': pb,
                    'sb': sb,
                    'data': data
                }

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def listen_loop(self):
        """Main listening loop."""
        self.logger.info("Starting eBUS message listener")

        while self.running:
            try:
                message = self.read_message()
                if message:
                    self.process_message(message)
            except Exception as e:
                self.logger.error(f"Error in listen loop: {e}")
                time.sleep(0.1)

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
            # Return all discovered data
            return dict(self.messages)

    def set_heating_temp(self, temperature):
        """Send heating temperature set command."""
        self.logger.warning(f"Set temperature to {temperature}°C - command implementation needed")
        return False