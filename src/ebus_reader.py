import subprocess
import json
import time
from src.utils.logger import setup_logger


class EbusReader:
    """Read data from ebusd via ebusctl command."""

    def __init__(self, config):
        self.config = config
        self.logger = setup_logger(__name__)
        self.cache = {}

    def read_value(self, circuit, message):
        """Read a specific value from ebusd."""
        try:
            cmd = f"ebusctl read -c {circuit} {message}"
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                value = result.stdout.strip()
                self.logger.debug(f"Read {circuit}.{message}: {value}")
                return value
            else:
                self.logger.error(f"Error reading {circuit}.{message}: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout reading {circuit}.{message}")
            return None
        except Exception as e:
            self.logger.error(f"Exception reading {circuit}.{message}: {e}")
            return None

    def get_heater_status(self):
        """Get complete heater status."""
        status = {
            'flow_temp': self.read_value('bai', 'FlowTemp'),
            'return_temp': self.read_value('bai', 'ReturnTemp'),
            'water_pressure': self.read_value('bai', 'WaterPressure'),
            'flame_status': self.read_value('bai', 'FlameStatus'),
            'heating_mode': self.read_value('bai', 'HeatingMode'),
            'dhw_temp': self.read_value('bai', 'DHWTemp'),
            'current_power': self.read_value('bai', 'ModulationLevel'),
        }

        self.cache = status
        return status

    def set_heating_temp(self, temperature):
        """Set heating target temperature."""
        try:
            cmd = f"ebusctl write -c bai HeatingTemp {temperature}"
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                self.logger.info(f"Set heating temperature to {temperature}°C")
                return True
            else:
                self.logger.error(f"Error setting temperature: {result.stderr}")
                return False

        except Exception as e:
            self.logger.error(f"Exception setting temperature: {e}")
            return False