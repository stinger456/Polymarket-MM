#!/usr/bin/env python3
"""Find what BTC markets actually exist on Polymarket right now."""

import httpx
import json

def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    print("=" * 70)
    print("FINDING BTC MARKETS ON POLYMARKET")
    print("=" * 70)

    with httpx.Client(headers=headers, timeout=30) as client:
        # Get all events
        resp = client.get(
            "https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "limit": 200}
        )
        events = resp.json()

        print(f"\nTotal events: {len(events)}")
        print("\n" + "=" * 70)
        print("ALL BITCOIN/BTC RELATED EVENTS:")
        print("=" * 70)

        btc_events = []
        for event in events:
            title = event.get("title", "")
            slug = event.get("slug", "")
            title_lower = title.lower()

            if "bitcoin" in title_lower or "btc" in title_lower:
                btc_events.append(event)
                markets = event.get("markets", [])
                print(f"\nTitle: {title}")
                print(f"Slug: {slug}")
                print(f"Markets: {len(markets)}")

                # Check for hourly patterns
                has_up = "up" in title_lower
                has_down = "down" in title_lower
                has_hourly_slug = "am-et" in slug or "pm-et" in slug

                if has_up and has_down:
                    print("  ✅ Has 'up' AND 'down'")
                if has_hourly_slug:
                    print("  ✅ Has hourly slug pattern")

        print(f"\n\nFound {len(btc_events)} BTC-related events")

        # Now specifically look for hourly markets
        print("\n" + "=" * 70)
        print("SEARCHING FOR HOURLY UP/DOWN PATTERN:")
        print("=" * 70)

        hourly_found = []
        for event in events:
            title = event.get("title", "").lower()
            slug = event.get("slug", "").lower()

            # Various patterns that might indicate hourly BTC markets
            patterns = [
                "up or down" in title,
                "up-or-down" in slug,
                ("hourly" in title and "bitcoin" in title),
                ("hour" in slug and "bitcoin" in title),
            ]

            if any(patterns):
                hourly_found.append(event)
                print(f"\nMatched: {event.get('title')}")
                print(f"Slug: {slug}")

        if not hourly_found:
            print("\nNo hourly markets found with current patterns.")
            print("\nLet me show you the first 20 event titles:")
            for i, e in enumerate(events[:20]):
                print(f"{i+1}. {e.get('title', '')[:70]}")

if __name__ == "__main__":
    main()
