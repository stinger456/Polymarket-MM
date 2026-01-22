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
            return None

        event = events[0]
        print(f"Event: {event.get('title')}")
        print(f"Markets: {len(event.get('markets', []))}")
        print("=" * 70)

        return event


def parse_markets(event: dict):
    """Parse tradeable markets from event."""
    markets = []
    now = datetime.now(timezone.utc)

    for m in event.get("markets", []):
        question = m.get("question", "")
        condition_id = m.get("conditionId", "")
        clob_tokens = m.get("clobTokenIds", [])
        end_date_str = m.get("endDate", "")
        closed = m.get("closed", False)

        # Parse end time
        end_time = None
        if end_date_str:
            try:
                if end_date_str.endswith("Z"):
                    end_date_str = end_date_str[:-1] + "+00:00"
                end_time = datetime.fromisoformat(end_date_str)
            except:
                pass

        # Check if tradeable
        if closed:
            print(f"  CLOSED: {question[:50]}")
            continue

        if not end_time:
            print(f"  NO END TIME: {question[:50]}")
            continue

        if end_time <= now:
            print(f"  EXPIRED: {question[:50]}")
            continue

        if len(clob_tokens) < 2:
            print(f"  NO TOKENS: {question[:50]}")
            continue

        # This market is tradeable!
        minutes_left = (end_time - now).total_seconds() / 60
        markets.append({
            "question": question,
            "condition_id": condition_id,
            "yes_token": clob_tokens[0],
            "no_token": clob_tokens[1],
            "end_time": end_time,
            "minutes_left": minutes_left,
        })

        print(f"\n  TRADEABLE: {question}")
        print(f"    Time left: {minutes_left:.1f} minutes")
        print(f"    Condition: {condition_id}")
        print(f"    YES token: {clob_tokens[0]}")
        print(f"    NO token:  {clob_tokens[1]}")

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
