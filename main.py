#!/usr/bin/env python3
"""
eBus Thelia Condens - Main Application

Reads eBus data from C6 adapter and provides parsed sensor values.
"""

import sys
import signal
import logging
import time
from pathlib import Path

import yaml

from ebus_core.connection import SerialConnection, ConnectionConfig
from thelia.parser import TheliaParser, MessageAggregator, ParsedMessage


class EbusReader:
    """Main eBus reader application."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()

        self.logger = logging.getLogger("EbusReader")

        # Create components
        conn_cfg = self._get_connection_config()
        self.connection = SerialConnection(conn_cfg)
        self.parser = TheliaParser()
        self.aggregator = MessageAggregator(
            max_age_seconds=self.config.get("parser", {}).get("max_age", 300)
        )

        # Wire up callbacks
        self.connection.register_telegram_callback(self._on_telegram)
        self.parser.register_callback(self.aggregator.update)
        self.parser.register_callback(self._on_parsed)

        self._running = False

    def _load_config(self, path: str) -> dict:
        """Load configuration."""
        config_path = Path(path)
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _setup_logging(self):
        """Configure logging."""
        log_cfg = self.config.get("logging", {})
        level = getattr(logging, log_cfg.get("level", "INFO"))

        logging.basicConfig(
            level=level,
            format=log_cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    def _get_connection_config(self) -> ConnectionConfig:
        """Create connection config from settings."""
        conn = self.config.get("connection", {})
        return ConnectionConfig(
            port=conn.get("port", "/dev/ttyAMA0"),
            baudrate=conn.get("baudrate", 2400),
            timeout=conn.get("timeout", 0.1),
            reconnect_delay=conn.get("reconnect_delay", 5.0)
        )

    def _on_telegram(self, telegram):
        """Handle raw telegram."""
        self.parser.parse(telegram)

    def _on_parsed(self, message: ParsedMessage):
        """Handle parsed message."""
        if message.valid and message.name not in ("unknown", "invalid"):
            self.logger.info(f"📨 {message}")

    def run(self):
        """Run the reader."""
        self.logger.info("🚀 Starting eBus Thelia reader...")

        if not self.connection.connect():
            self.logger.error("Failed to connect")
            return False

        self._running = True
        last_stats = time.time()

        try:
            while self._running:
                self.connection.read_telegrams()

                # Periodic stats
                if time.time() - last_stats > 60:
                    stats = self.parser.get_stats()
                    values = self.aggregator.get_flat()
                    self.logger.info(
                        f"📊 Stats: total={stats['total']}, "
                        f"parsed={stats['parsed']}, unknown={stats['unknown']}"
                    )
                    self.logger.info(f"📈 Active sensors: {len(values)}")
                    last_stats = time.time()

                time.sleep(0.01)

        except KeyboardInterrupt:
            self.logger.info("Interrupted")
        finally:
            self.stop()

        return True

    def stop(self):
        """Stop the reader."""
        self._running = False
        self.connection.disconnect()
        self.logger.info("Stopped")

    def get_current_values(self) -> dict:
        """Get current sensor values."""
        return self.aggregator.get_all()


def main():
    reader = EbusReader()

    # Handle signals
    def handle_signal(sig, frame):
        reader.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    reader.run()


if __name__ == "__main__":
    main()