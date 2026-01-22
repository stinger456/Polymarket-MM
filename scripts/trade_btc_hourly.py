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


async def find_current_btc_event():
    """Find the current active BTC UP/DOWN event automatically."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("\n" + "=" * 70)
        print("SEARCHING FOR ACTIVE BTC UP/DOWN EVENTS...")
        print("=" * 70)

        response = await client.get(
            f"{GAMMA_URL}/events",
            params={"active": "true", "closed": "false", "limit": 100}
        )
        events = response.json()
        now = datetime.now(timezone.utc)

        active_events = []
        for event in events:
            title = event.get("title", "").lower()
            slug = event.get("slug", "")

            # Match Bitcoin UP/DOWN events
            if ("bitcoin" in title or "btc" in title) and ("up" in title or "down" in title):
                # Check for active markets
                for m in event.get("markets", []):
                    end_str = m.get("endDate", "")
                    closed = m.get("closed", False)
                    if end_str and not closed:
                        try:
                            if end_str.endswith("Z"):
                                end_str = end_str[:-1] + "+00:00"
                            end_time = datetime.fromisoformat(end_str)
                            mins_left = (end_time - now).total_seconds() / 60
                            if mins_left > 0:
                                active_events.append({
                                    "title": event.get("title"),
                                    "slug": slug,
                                    "end_time": end_time,
                                    "minutes_left": mins_left,
                                    "event": event
                                })
                                break
                        except:
                            pass

        if active_events:
            active_events.sort(key=lambda x: x["end_time"])
            print(f"\nFound {len(active_events)} active BTC UP/DOWN event(s):\n")
            for i, e in enumerate(active_events[:5]):
                print(f"  {i+1}. {e['title']}")
                print(f"     slug: {e['slug']}")
                print(f"     expires in: {e['minutes_left']:.1f} minutes")
            return active_events[0]
        return None


async def main():
    if len(sys.argv) >= 2:
        slug = sys.argv[1]
        print(f"Using provided slug: {slug}")
        event = await get_event_markets(slug)
    else:
        # Auto-find current active event
        result = await find_current_btc_event()
        if result:
            event = result["event"]
            slug = result["slug"]
            print(f"\nUsing: {slug}")
        else:
            print("\nNo active BTC UP/DOWN events found!")
            print("Check https://polymarket.com for current events.")
            return

    if not event:
        return

    print("\nParsing markets...")
    markets = parse_markets(event)

    print("\n" + "=" * 70)
    print(f"FOUND {len(markets)} TRADEABLE MARKET(S)")
    print("=" * 70)

    if not markets:
        print("\nNo tradeable markets in this event (may have just expired).")
        print("\nSearching for next active event...")
        result = await find_current_btc_event()
        if result:
            print(f"\nTry running:")
            print(f"  python scripts/trade_btc_hourly.py {result['slug']}")
        return

    # Show the market to trade
    markets.sort(key=lambda x: x["end_time"])
    next_market = markets[0]

    print(f"\nMARKET TO TRADE:")
    print(f"  {next_market['question']}")
    print(f"  Minutes left: {next_market['minutes_left']:.1f}")
    print(f"\nToken IDs:")
    print(f"  YES: {next_market['yes_token']}")
    print(f"  NO:  {next_market['no_token']}")
    print(f"\nRun the bot:")
    print(f"  python -m src.main --live --event {slug}")


if __name__ == "__main__":
    asyncio.run(main())
