#!/usr/bin/env python3
"""
Simple BTC Hourly Market Maker - Trades ONE specific event.

Usage:
    python scripts/trade_btc_hourly.py bitcoin-up-or-down-january-21-8pm-et
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
import json

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

load_dotenv()

GAMMA_URL = "https://gamma-api.polymarket.com"


async def get_event_markets(slug: str):
    """Get all markets from a specific event slug."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"\nFetching event: {slug}")
        print("=" * 70)

        response = await client.get(
            f"{GAMMA_URL}/events",
            params={"slug": slug}
        )
        response.raise_for_status()
        events = response.json()

        if not events:
            print(f"ERROR: Event '{slug}' not found!")
            print("\nSearching for similar events...")

            # Search for any bitcoin events
            response2 = await client.get(
                f"{GAMMA_URL}/events",
                params={"active": "true", "closed": "false", "limit": 50}
            )
            all_events = response2.json()

            print(f"\nFound {len(all_events)} active events. BTC-related:")
            for e in all_events:
                title = e.get("title", "").lower()
                if "bitcoin" in title or "btc" in title:
                    print(f"  - {e.get('title')}")
                    print(f"    slug: {e.get('slug')}")
            return None

        event = events[0]
        print(f"Event: {event.get('title')}")
        print(f"Total Markets in event: {len(event.get('markets', []))}")
        print("=" * 70)

        # Debug: print raw first market
        markets = event.get("markets", [])
        if markets:
            print("\nRAW FIRST MARKET DATA:")
            print(json.dumps(markets[0], indent=2, default=str)[:2000])
            print("=" * 70)

        return event


def parse_markets(event: dict):
    """Parse tradeable markets from event."""
    markets = []
    now = datetime.now(timezone.utc)

    print(f"\nCurrent UTC time: {now.isoformat()}")
    print(f"Parsing {len(event.get('markets', []))} markets...\n")

    for i, m in enumerate(event.get("markets", [])):
        question = m.get("question", "")
        condition_id = m.get("conditionId", "")
        clob_tokens = m.get("clobTokenIds", [])
        end_date_str = m.get("endDate", "")
        closed = m.get("closed", False)
        active = m.get("active", True)

        print(f"\nMarket {i+1}: {question[:60]}")
        print(f"  closed={closed}, active={active}")
        print(f"  endDate={end_date_str}")
        print(f"  clobTokenIds={len(clob_tokens)} tokens")

        # Parse end time
        end_time = None
        if end_date_str:
            try:
                if end_date_str.endswith("Z"):
                    end_date_str = end_date_str[:-1] + "+00:00"
                end_time = datetime.fromisoformat(end_date_str)
                print(f"  Parsed end_time: {end_time.isoformat()}")
            except Exception as e:
                print(f"  Failed to parse end time: {e}")

        # Check if tradeable
        if closed:
            print(f"  -> SKIPPED: Market is closed")
            continue

        if not end_time:
            print(f"  -> SKIPPED: No end time")
            continue

        time_diff = (end_time - now).total_seconds()
        if time_diff <= 0:
            print(f"  -> SKIPPED: Expired ({time_diff/60:.1f} minutes ago)")
            continue

        if len(clob_tokens) < 2:
            print(f"  -> SKIPPED: Not enough tokens ({len(clob_tokens)})")
            continue

        # This market is tradeable!
        minutes_left = time_diff / 60
        markets.append({
            "question": question,
            "condition_id": condition_id,
            "yes_token": clob_tokens[0],
            "no_token": clob_tokens[1],
            "end_time": end_time,
            "minutes_left": minutes_left,
        })

        print(f"  -> TRADEABLE! {minutes_left:.1f} minutes left")
        print(f"     YES: {clob_tokens[0][:50]}...")
        print(f"     NO:  {clob_tokens[1][:50]}...")

    return markets


async def main():
    if len(sys.argv) < 2:
        # Default to today's event
        slug = "bitcoin-up-or-down-january-21-8pm-et"
        print(f"No slug provided, using: {slug}")
    else:
        slug = sys.argv[1]

    event = await get_event_markets(slug)
    if not event:
        return

    print("\nParsing markets...")
    markets = parse_markets(event)

    print("\n" + "=" * 70)
    print(f"FOUND {len(markets)} TRADEABLE MARKET(S)")
    print("=" * 70)

    if not markets:
        print("\nNo tradeable markets found in this event.")
        print("Possible reasons:")
        print("  - All markets have expired (event is for a past time)")
        print("  - Markets are closed")
        print("  - Event hasn't started yet")
        return

    # Show the next market to trade
    markets.sort(key=lambda x: x["end_time"])
    next_market = markets[0]

    print(f"\nNEXT MARKET TO TRADE:")
    print(f"  {next_market['question']}")
    print(f"  Minutes until resolution: {next_market['minutes_left']:.1f}")
    print(f"\nTo trade this market, the bot needs these token IDs:")
    print(f"  YES: {next_market['yes_token']}")
    print(f"  NO:  {next_market['no_token']}")


if __name__ == "__main__":
    asyncio.run(main())
