#!/usr/bin/env python3
"""
eBus Traffic Capture Tool

Captures and analyzes raw eBus traffic from the C6 adapter.
"""

import sys
import time
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ebus_core.connection import SerialConnection, ConnectionConfig
from ebus_core.telegram import EbusTelegram
from thelia.parser import TheliaParser


class EbusCapturer:
    """eBus traffic capture and analysis tool."""

    def __init__(self, port: str = "/dev/ttyAMA0", baudrate: int = 2400):
        self.config = ConnectionConfig(port=port, baudrate=baudrate)
        self.connection = SerialConnection(self.config)
        self.parser = TheliaParser()

        self.stats = {
            "telegrams": 0,
            "bytes": 0,
            "by_source": {},
            "by_command": {},
            "by_message": {}
        }

    def connect(self) -> bool:
        """Connect to serial port."""
        return self.connection.connect()

    def disconnect(self) -> None:
        """Disconnect from serial port."""
        self.connection.disconnect()

    def capture_raw(self, duration: int = 60, output_file: str = None):
        """Capture raw bytes."""
        print(f"\n📡 Capturing raw eBus traffic for {duration} seconds...")
        print("=" * 70)

        start_time = time.time()
        out_file = None

        if output_file:
            out_file = open(output_file, 'wb')
            print(f"💾 Saving to {output_file}")

        def on_raw(data: bytes):
            self.stats["bytes"] += len(data)
            if out_file:
                out_file.write(data)
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            hex_str = ' '.join(f'{b:02X}' for b in data)
            print(f"[{ts}] {hex_str}")

        self.connection.register_raw_callback(on_raw)

        try:
            while (time.time() - start_time) < duration:
                self.connection.read_telegrams()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n⚠️  Interrupted")
        finally:
            if out_file:
                out_file.close()

        print("=" * 70)
        print(f"📊 Captured {self.stats['bytes']} bytes")

    def capture_telegrams(self, count: int = 20, parsed: bool = False):
        """Capture and display telegrams."""
        mode = "parsed" if parsed else "raw"
        print(f"\n📡 Capturing {count} telegrams ({mode} mode)...")
        print("=" * 70)

        captured = 0

        def on_telegram(telegram: EbusTelegram):
            nonlocal captured
            captured += 1
            self._update_stats(telegram)

            if parsed:
                msg = self.parser.parse(telegram)
                self._print_parsed(captured, msg)
            else:
                self._print_telegram(captured, telegram)

        self.connection.register_telegram_callback(on_telegram)

        try:
            while captured < count:
                self.connection.read_telegrams()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n⚠️  Interrupted")

        print("=" * 70)
        print(f"📊 Captured {captured} telegrams")

    def monitor(self, parsed: bool = True):
        """Continuous monitoring."""
        mode = "parsed" if parsed else "raw"
        print(f"\n📡 Monitoring eBus ({mode} mode) - Ctrl+C to stop...")
        print("=" * 70)

        last_stats = time.time()

        def on_telegram(telegram: EbusTelegram):
            self._update_stats(telegram)

            if parsed:
                msg = self.parser.parse(telegram)
                self._print_parsed(self.stats["telegrams"], msg)
            else:
                self._print_telegram(self.stats["telegrams"], telegram)

        self.connection.register_telegram_callback(on_telegram)

        try:
            while True:
                self.connection.read_telegrams()

                # Print stats periodically
                if time.time() - last_stats > 60:
                    self._print_stats()
                    last_stats = time.time()

                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n\n" + "=" * 70)
            print("Final Statistics:")
            self._print_stats()

    def _update_stats(self, telegram: EbusTelegram):
        """Update statistics."""
        self.stats["telegrams"] += 1

        src = telegram.source
        cmd = telegram.command_hex

        self.stats["by_source"][src] = self.stats["by_source"].get(src, 0) + 1
        self.stats["by_command"][cmd] = self.stats["by_command"].get(cmd, 0) + 1

    def _print_telegram(self, num: int, telegram: EbusTelegram):
        """Print raw telegram."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        valid = "✓" if telegram.valid else "✗"

        print(f"\n[{num:4d}] {ts} {valid}")
        print(f"       SRC: 0x{telegram.source:02X}  "
              f"DST: 0x{telegram.destination:02X}  "
              f"CMD: {telegram.command_hex}  "
              f"LEN: {len(telegram.data)}")
        print(f"       DATA: {telegram.data.hex()}")

        if telegram.destination == 0xFE:
            print(f"       TYPE: Broadcast")
        elif telegram.response_data:
            print(f"       TYPE: Master-Slave  RESP: {telegram.response_data.hex()}")

    def _print_parsed(self, num: int, msg):
        """Print parsed message."""
        ts = msg.timestamp.strftime("%H:%M:%S.%f")[:-3]

        if msg.name == "unknown":
            print(f"[{num:4d}] {ts} ❓ Unknown CMD:{msg.command[0]:02X}{msg.command[1]:02X} "
                  f"DATA:{msg.values.get('raw_data', '')}")
        elif msg.name == "invalid":
            print(f"[{num:4d}] {ts} ❌ Invalid telegram")
        else:
            values = ", ".join(
                f"{k}={v}{msg.units.get(k, '')}"
                for k, v in msg.values.items()
            )
            print(f"[{num:4d}] {ts} ✅ {msg.name}: {values}")

            # Update message stats
            name = msg.name
            self.stats["by_message"][name] = self.stats["by_message"].get(name, 0) + 1

    def _print_stats(self):
        """Print statistics."""
        print("\n" + "-" * 40)
        print(f"📊 Total telegrams: {self.stats['telegrams']}")

        print("\n   By Source:")
        for src, count in sorted(self.stats["by_source"].items()):
            print(f"      0x{src:02X}: {count}")

        print("\n   By Command (top 10):")
        sorted_cmds = sorted(
            self.stats["by_command"].items(),
            key=lambda x: -x[1]
        )[:10]
        for cmd, count in sorted_cmds:
            print(f"      {cmd}: {count}")

        if self.stats["by_message"]:
            print("\n   By Message Type:")
            for name, count in sorted(self.stats["by_message"].items()):
                print(f"      {name}: {count}")

        print("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description="eBus Traffic Capture Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -m monitor              # Monitor with parsing
  %(prog)s -m monitor --raw        # Monitor raw telegrams
  %(prog)s -m telegrams -c 50      # Capture 50 telegrams
  %(prog)s -m raw -d 120 -o cap.bin  # Capture raw for 2 min
        """
    )

    parser.add_argument(
        "-p", "--port",
        default="/dev/ttyAMA0",
        help="Serial port (default: /dev/ttyAMA0)"
    )
    parser.add_argument(
        "-b", "--baudrate",
        type=int,
        default=2400,
        help="Baud rate (default: 2400)"
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["raw", "telegrams", "monitor"],
        default="monitor",
        help="Capture mode (default: monitor)"
    )
    parser.add_argument(
        "-d", "--duration",
        type=int,
        default=60,
        help="Duration in seconds for raw mode (default: 60)"
    )
    parser.add_argument(
        "-c", "--count",
        type=int,
        default=20,
        help="Number of telegrams to capture (default: 20)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file for raw capture"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Show raw telegrams instead of parsed"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    # Create and run capturer
    capturer = EbusCapturer(port=args.port, baudrate=args.baudrate)

    if not capturer.connect():
        print(f"❌ Failed to connect to {args.port}")
        sys.exit(1)

    try:
        if args.mode == "raw":
            capturer.capture_raw(args.duration, args.output)
        elif args.mode == "telegrams":
            capturer.capture_telegrams(args.count, parsed=not args.raw)
        elif args.mode == "monitor":
            capturer.monitor(parsed=not args.raw)
    finally:
        capturer.disconnect()


if __name__ == "__main__":
    main()