#!/usr/bin/env python3
"""
Test script for the parser with sample data.
"""

from thelia.parser import TheliaParser, MessageAggregator
from ebus_core.telegram import EbusTelegram, TelegramParser
from datetime import datetime


def test_parser():
    """Test the parser with sample telegrams."""
    parser = TheliaParser()
    aggregator = MessageAggregator()
    parser.register_callback(aggregator.update)

    # Sample test telegrams (you'll need to capture real ones)
    # Format: source, dest, primary_cmd, secondary_cmd, data...
    test_telegrams = [
        # Example: Flow temperature broadcast
        bytes([0x08, 0xFE, 0x05, 0x07, 0x04, 0x00, 0x00, 0x14, 0x80]),
        # Add more test cases as you capture real data
    ]

    print("Testing parser with sample telegrams...")
    print("=" * 60)

    for i, telegram_data in enumerate(test_telegrams):
        print(f"\nTest {i + 1}: {telegram_data.hex()}")

        # Create a telegram object
        telegram = EbusTelegram(
            source=telegram_data[0],
            destination=telegram_data[1],
            primary_command=telegram_data[2],
            secondary_command=telegram_data[3],
            data=telegram_data[5:5 + telegram_data[4]] if len(telegram_data) > 5 else b'',
            valid=True,
            timestamp=datetime.now().timestamp()
        )

        result = parser.parse_telegram(telegram)
        if result:
            print(f"  Result: {result}")
            print(f"  Values: {result.values}")

    print("\n" + "=" * 60)
    print("Aggregated state:")
    print(aggregator.get_all_current_values())

    print("\nParser stats:")
    print(parser.get_stats())


if __name__ == "__main__":
    test_parser()