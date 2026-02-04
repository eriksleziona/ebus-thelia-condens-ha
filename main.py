#!/usr/bin/env python3
"""
eBus Thelia Condens - Main Entry Point

Reads eBus data from the C6 shield adapter and parses Thelia Condens messages.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from datetime import datetime
import yaml
from typing import Optional

from ebus_core.connection import (
    ConnectionConfig,
    ConnectionType,
    create_connection,
    EbusConnection
)
from ebus_core.telegram import TelegramParser
from thelia.parser import TheliaParser, MessageAggregator, ParsedMessage


class EbusApplication:
    """Main application class."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()

        self.logger = logging.getLogger(self.__class__.__name__)
        self.connection: Optional[EbusConnection] = None
        self.parser = TheliaParser()
        self.aggregator = MessageAggregator(
            max_age_seconds=self.config.get("parser", {}).get("max_value_age", 300)
        )

        self._running = False
        self._tasks = []

        # Register aggregator as callback
        self.parser.register_callback(self.aggregator.update)
        # Register our own message handler
        self.parser.register_callback(self._on_message)

    def _load_config(self, path: str) -> dict:
        """Load configuration from YAML file."""
        config_path = Path(path)
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)
        return {}

    def _setup_logging(self) -> None:
        """Configure logging."""
        log_config = self.config.get("logging", {})
        level = getattr(logging, log_config.get("level", "INFO"))

        logging.basicConfig(
            level=level,
            format=log_config.get(
                "format",
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        )

        # File handler if specified
        log_file = log_config.get("file")
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter(log_config.get("format", "")))
            logging.getLogger().addHandler(fh)

    def _create_connection_config(self) -> ConnectionConfig:
        """Create connection config from settings."""
        conn_cfg = self.config.get("connection", {})

        conn_type = ConnectionType(conn_cfg.get("type", "serial"))

        return ConnectionConfig(
            type=conn_type,
            port=conn_cfg.get("port", "/dev/ttyAMA0"),
            baudrate=conn_cfg.get("baudrate", 2400),
            host=conn_cfg.get("host", "localhost"),
            tcp_port=conn_cfg.get("tcp_port", 8888),
            timeout=conn_cfg.get("timeout", 1.0),
            reconnect_delay=conn_cfg.get("reconnect_delay", 5.0)
        )

    def _on_message(self, message: ParsedMessage) -> None:
        """Handle parsed message."""
        if message.valid and message.name != "unknown":
            self.logger.info(f"📨 {message}")
        elif message.name == "unknown":
            self.logger.debug(f"Unknown message: {message.command}")

    async def _read_loop(self) -> None:
        """Main read loop."""
        conn_config = self._create_connection_config()

        while self._running:
            try:
                # Create and connect
                self.connection = create_connection(conn_config)

                if not await self.connection.connect():
                    self.logger.error("Connection failed, retrying...")
                    await asyncio.sleep(conn_config.reconnect_delay)
                    continue

                self.logger.info("Connected, starting read loop...")

                # Read telegrams
                async for raw_telegram in self.connection.read_loop():
                    if not self._running:
                        break

                    timestamp = datetime.now().timestamp()
                    self.parser.parse_raw(raw_telegram, timestamp)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in read loop: {e}")
                await asyncio.sleep(conn_config.reconnect_delay)
            finally:
                if self.connection:
                    await self.connection.disconnect()

    async def _status_loop(self) -> None:
        """Periodic status reporting."""
        while self._running:
            await asyncio.sleep(60)

            stats = self.parser.get_stats()
            values = self.aggregator.get_flat_values()

            self.logger.info(
                f"📊 Stats: telegrams={stats['total_telegrams']}, "
                f"ok={stats['parsed_ok']}, errors={stats['parse_errors']}, "
                f"unknown={stats['unknown_messages']}"
            )

            if values:
                self.logger.info(f"📈 Current values: {len(values)} sensors active")
                for key, value in list(values.items())[:5]:  # Show first 5
                    self.logger.info(f"   {key}: {value}")

    async def run(self) -> None:
        """Run the application."""
        self._running = True

        self.logger.info("🚀 Starting eBus Thelia Condens reader...")

        # Create tasks
        self._tasks = [
            asyncio.create_task(self._read_loop()),
            asyncio.create_task(self._status_loop()),
        ]

        # Wait for all tasks
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the application."""
        self.logger.info("Stopping...")
        self._running = False

        for task in self._tasks:
            task.cancel()

        if self.connection:
            await self.connection.disconnect()

        self.logger.info("Stopped")


async def main():
    """Main entry point."""
    app = EbusApplication()

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))

    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)