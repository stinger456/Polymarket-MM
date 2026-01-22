#!/usr/bin/env python3
"""Fetch a specific event by slug to debug why it's not appearing."""

import httpx
from datetime import datetime

def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    # Try multiple hourly slugs
    current_hour = datetime.now().hour
    slugs_to_try = [
        "bitcoin-up-or-down-january-22-9am-et",
        "bitcoin-up-or-down-january-22-10am-et",
        "bitcoin-up-or-down-january-22-11am-et",
        "bitcoin-up-or-down-january-22-12pm-et",
        "bitcoin-up-or-down-january-22-1pm-et",
        "bitcoin-up-or-down-january-22-2pm-et",
        "bitcoin-up-or-down-january-22-3pm-et",
    ]

    print("=" * 70)
    print("FETCHING SPECIFIC BTC HOURLY EVENTS")
    print("=" * 70)

    with httpx.Client(headers=headers, timeout=30) as client:
        for slug in slugs_to_try:
            print(f"\nTrying slug: {slug}")
            try:
                resp = client.get(
                    "https://gamma-api.polymarket.com/events",
                    params={"slug": slug}
                )
                events = resp.json()

                if events:
                    event = events[0]
                    print(f"  ✅ FOUND!")
                    print(f"  Title: {event.get('title')}")
                    print(f"  Active: {event.get('active')}")
                    print(f"  Closed: {event.get('closed')}")
                    markets = event.get("markets", [])
                    print(f"  Markets: {len(markets)}")

                    for m in markets:
                        print(f"\n    Market: {m.get('question', '')[:60]}")
                        print(f"    Closed: {m.get('closed')}")
                        print(f"    End Date: {m.get('endDate')}")
                        tokens = m.get("clobTokenIds", [])
                        if isinstance(tokens, str):
                            import json
                            tokens = json.loads(tokens)
                        print(f"    Tokens: {len(tokens)}")
                        if tokens:
                            print(f"    YES Token: {tokens[0][:40]}...")
                else:
                    print(f"  ❌ Not found")

            except Exception as e:
                print(f"  Error: {e}")

        # Also try the events endpoint without any filters
        print("\n" + "=" * 70)
        print("TRYING EVENTS WITHOUT FILTERS:")
        print("=" * 70)

        resp = client.get(
            "https://gamma-api.polymarket.com/events",
            params={"limit": 300}
        )
        all_events = resp.json()

        btc_hourly = []
        for e in all_events:
            slug = e.get("slug", "").lower()
            if "up-or-down" in slug and "bitcoin" in slug:
                btc_hourly.append(e)

        print(f"\nFound {len(btc_hourly)} Bitcoin Up/Down events:")
        for e in btc_hourly[:10]:
            print(f"  - {e.get('title')} (closed={e.get('closed')}, active={e.get('active')})")
            print(f"    Slug: {e.get('slug')}")

if __name__ == "__main__":
    main()
