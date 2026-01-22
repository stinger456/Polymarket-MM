#!/usr/bin/env python3
"""
Check Polymarket connection and find hourly BTC markets.

Run this first to verify everything is working.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from src.market.discovery import MarketDiscovery


async def main():
    print("=" * 70)
    print("  POLYMARKET CONNECTION TEST")
    print("=" * 70)
    print()

    # Check for command-line event slug
    event_slug = None
    if len(sys.argv) > 1:
        event_slug = sys.argv[1]
        print(f"Using event slug: {event_slug}\n")

    print("Connecting to Polymarket Gamma API...")
    md = MarketDiscovery(event_slug=event_slug)

    try:
        # First, search for BTC hourly events
        print("Searching for Bitcoin UP/DOWN events...")
        btc_events = await md.find_btc_hourly_events()

        if btc_events:
            print(f"\nFound {len(btc_events)} Bitcoin UP/DOWN event(s):\n")
            for event in btc_events[:3]:
                print(f"  Event: {event.get('title', 'N/A')}")
                print(f"  Slug:  {event.get('slug', 'N/A')}")
                print(f"  Markets: {len(event.get('markets', []))}")
                print()

        # Look for hourly BTC markets
        print("Fetching tradeable markets...")
        markets = await md.get_hourly_btc_markets()

        if markets:
            print(f"\n{'='*70}")
            print(f"  FOUND {len(markets)} TRADEABLE BTC MARKET(S)")
            print(f"{'='*70}\n")

            for i, m in enumerate(markets[:10], 1):
                print(f"{i}. {m.question}")
                print(f"   Strike: ${m.strike_price:,.0f}")
                print(f"   Time remaining: {m.time_remaining_seconds // 60} minutes")
                print(f"   YES Token: {m.yes_token_id[:40]}...")
                print(f"   NO Token:  {m.no_token_id[:40]}...")
                print(f"   Condition: {m.condition_id[:40]}...")
                print()

            print("-" * 70)
            print("\nTo run the bot with these markets:")
            if event_slug:
                print(f'  python -m src.main --live --event "{event_slug}"')
            elif btc_events:
                print(f'  python -m src.main --live --event "{btc_events[0].get("slug")}"')
            else:
                print("  python -m src.main --live")
            print()

        else:
            print("\n" + "=" * 70)
            print("  NO TRADEABLE MARKETS FOUND")
            print("=" * 70)
            print()
            print("This could mean:")
            print("  - Markets haven't been created for this time period")
            print("  - All hourly markets have expired")
            print("  - Try a different event slug")
            print()

            if btc_events:
                print("Try running with a specific event:")
                print(f'  python scripts/check_polymarket.py "{btc_events[0].get("slug")}"')
            print()

    except Exception as e:
        print(f"\n Error: {e}")
        import traceback
        traceback.print_exc()
        print("\nThis might be a network issue or API problem.")

    finally:
        await md.close()

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
