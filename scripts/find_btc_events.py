#!/usr/bin/env python3
"""
Find available Bitcoin UP/DOWN hourly events on Polymarket.

Run this to see current events and their slugs for use with the bot.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from src.market.discovery import MarketDiscovery


async def main():
    print("=" * 70)
    print("  POLYMARKET BTC HOURLY EVENT FINDER")
    print("=" * 70)
    print()

    md = MarketDiscovery()

    try:
        print("Searching for Bitcoin UP/DOWN events...\n")

        # Get all events and look for BTC hourly ones
        events = await md.get_events(limit=200)

        btc_events = []
        for event in events:
            title = event.get("title", "").lower()
            slug = event.get("slug", "")

            # Match Bitcoin up/down pattern
            if ("bitcoin" in title or "btc" in title) and ("up" in title or "down" in title):
                btc_events.append(event)

        if btc_events:
            print(f"Found {len(btc_events)} Bitcoin UP/DOWN event(s):\n")
            print("-" * 70)

            for i, event in enumerate(btc_events, 1):
                title = event.get("title", "N/A")
                slug = event.get("slug", "N/A")
                markets = event.get("markets", [])
                active_markets = [m for m in markets if not m.get("closed", True)]

                print(f"{i}. {title}")
                print(f"   Slug: {slug}")
                print(f"   Markets: {len(active_markets)} active / {len(markets)} total")

                # Show some market details
                if active_markets:
                    print("   Active markets:")
                    for m in active_markets[:5]:
                        q = m.get("question", "")[:60]
                        end = m.get("endDate", "")[:19]
                        print(f"     - {q}...")
                        print(f"       End: {end}")
                print()

            print("-" * 70)
            print("\nTo run the bot with a specific event:")
            print(f"  python -m src.main --live --event \"{btc_events[0].get('slug')}\"")
            print()

        else:
            print("No Bitcoin UP/DOWN events found.")
            print("\nThis could mean:")
            print("  - Events haven't been created for this time period")
            print("  - All current events have ended")
            print()

            # Show any BTC-related events
            print("Looking for any BTC-related events...")
            btc_any = []
            for event in events:
                title = event.get("title", "").lower()
                if "bitcoin" in title or "btc" in title:
                    btc_any.append(event)

            if btc_any:
                print(f"\nFound {len(btc_any)} BTC-related events:")
                for event in btc_any[:5]:
                    print(f"  - {event.get('title', 'N/A')}")
                    print(f"    Slug: {event.get('slug', 'N/A')}")

    except Exception as e:
        print(f"\nError: {e}")
        print("\nThis might be a network issue or API problem.")

    finally:
        await md.close()

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
