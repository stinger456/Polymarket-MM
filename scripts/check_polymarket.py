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
    print("=" * 60)
    print("  POLYMARKET CONNECTION TEST")
    print("=" * 60)
    print()

    print("Connecting to Polymarket Gamma API...")
    md = MarketDiscovery()

    try:
        # Get all markets first
        print("Fetching markets...")
        raw_markets = await md.get_all_markets(limit=100)
        print(f"Found {len(raw_markets)} total active markets\n")

        # Look for hourly BTC markets
        print("Searching for hourly BTC markets...")
        markets = await md.get_hourly_btc_markets()

        if markets:
            print(f"\n✅ Found {len(markets)} hourly BTC markets:\n")
            for i, m in enumerate(markets[:10], 1):
                print(f"{i}. {m.question}")
                print(f"   Strike: ${m.strike_price:,.0f}")
                print(f"   Time remaining: {m.time_remaining_seconds // 60} minutes")
                print(f"   YES Token: {m.yes_token_id[:20]}...")
                print(f"   NO Token: {m.no_token_id[:20]}...")
                print(f"   Condition ID: {m.condition_id[:20]}...")
                print()
        else:
            print("\n⚠️  No hourly BTC markets found right now.")
            print("   This could mean:")
            print("   - Markets haven't been created yet for this hour")
            print("   - The search pattern needs adjustment")
            print()

            # Show any BTC-related markets
            print("Looking for any BTC-related markets...")
            btc_markets = []
            for market in raw_markets:
                q = market.get("question", "").lower()
                if "btc" in q or "bitcoin" in q:
                    btc_markets.append(market)

            if btc_markets:
                print(f"\nFound {len(btc_markets)} BTC-related markets:")
                for m in btc_markets[:5]:
                    print(f"  - {m.get('question', 'N/A')[:70]}...")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nThis might be a network issue or API problem.")

    finally:
        await md.close()

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
