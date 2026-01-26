#!/usr/bin/env python3
import sys
from pathlib import Path
from src.heater_controller import HeaterController
from src.utils.config_loader import ConfigLoader

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    """Main entry point."""
    # Load configuration
    config = ConfigLoader()

    # Create and start controller
    controller = HeaterController(config)
    controller.start()


if __name__ == "__main__":
    main()
