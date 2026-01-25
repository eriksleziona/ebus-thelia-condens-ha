import yaml
from pathlib import Path


class ConfigLoader:
    """Load and manage configuration from YAML file."""

    def __init__(self, config_path="config/config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self):
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def get(self, key_path, default=None):
        """Get configuration value using dot notation (e.g., 'mqtt.broker')."""
        keys = key_path.split('.')
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value