#!/usr/bin/env python3
"""
Discover active sports markets on Polymarket.

Run this to see what sports markets are currently available.
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sports.sports_discovery import (
    get_all_sports_markets,
    get_game_markets,
    get_markets_by_sport,
    search_markets,
    SPORTS_TAGS,
)


async def main():
    print("=" * 60)
    print("   POLYMARKET SPORTS MARKET DISCOVERY")
    print("=" * 60)
    print()

    # Get all sports markets
    print("Fetching all sports markets...")
    markets = await get_all_sports_markets()

    if not markets:
        print("\nNo sports markets found.")
        print("\nTip: Sports markets may not be available during off-seasons.")
        print("Try checking during NBA, NFL, MLB, or major soccer seasons.")
        return

    print(f"\nFound {len(markets)} sports markets:\n")

    # Group by sport
    by_sport = {}
    for market in markets:
        sport = market.sport
        if sport not in by_sport:
            by_sport[sport] = []
        by_sport[sport].append(market)

    # Print summary by sport
    print("-" * 60)
    print("MARKETS BY SPORT:")
    print("-" * 60)
    for sport, sport_markets in sorted(by_sport.items()):
        print(f"\n{sport}: {len(sport_markets)} markets")
        for market in sport_markets[:3]:  # Show first 3
            print(f"  - {market.question[:55]}...")
            if market.outcomes:
                print(f"    Teams: {market.outcomes[0]} vs {market.outcomes[1] if len(market.outcomes) > 1 else 'N/A'}")
                prices = [f"${p:.2f}" for p in market.outcome_prices]
                print(f"    Prices: {' / '.join(prices)}")
        if len(sport_markets) > 3:
            print(f"  ... and {len(sport_markets) - 3} more")

    # Show markets with best edge
    print("\n" + "-" * 60)
    print("MARKETS WITH POTENTIAL EDGE:")
    print("-" * 60)

    for market in markets[:10]:
        if len(market.outcome_prices) >= 2:
            total_price = sum(market.outcome_prices)
            edge = max(0, 1.0 - total_price)
            if edge > 0.01:  # 1% edge
                print(f"\n{market.question[:50]}...")
                print(f"  Edge: {edge:.2%}")
                print(f"  Prices: {market.outcome_prices}")
                print(f"  Volume: ${market.volume:,.0f}")

    print("\n" + "=" * 60)
    print("Run the sports market maker with:")
    print("  python -m src.sports.sports_main --paper")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
