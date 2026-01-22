#!/usr/bin/env python3
"""List ALL active events on Polymarket to see what's available."""

import asyncio
import httpx
from datetime import datetime, timezone

GAMMA_URL = "https://gamma-api.polymarket.com"

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("Fetching ALL active events from Polymarket...\n")

        response = await client.get(
            f"{GAMMA_URL}/events",
            params={"active": "true", "closed": "false", "limit": 200}
        )
        events = response.json()

        now = datetime.now(timezone.utc)
        print(f"Current UTC time: {now.isoformat()}")
        print(f"Total active events: {len(events)}\n")
        print("=" * 80)

        # Show ALL events, not just BTC
        btc_events = []
        other_events = []

        for event in events:
            title = event.get("title", "")
            slug = event.get("slug", "")
            markets = event.get("markets", [])

            # Check for active markets
            active_count = 0
            soonest_end = None
            for m in markets:
                end_str = m.get("endDate", "")
                closed = m.get("closed", False)
                if end_str and not closed:
                    try:
                        if end_str.endswith("Z"):
                            end_str = end_str[:-1] + "+00:00"
                        end_time = datetime.fromisoformat(end_str)
                        if end_time > now:
                            active_count += 1
                            if soonest_end is None or end_time < soonest_end:
                                soonest_end = end_time
                    except:
                        pass

            info = {
                "title": title,
                "slug": slug,
                "total_markets": len(markets),
                "active_markets": active_count,
                "soonest_end": soonest_end,
            }

            if "bitcoin" in title.lower() or "btc" in title.lower():
                btc_events.append(info)
            else:
                other_events.append(info)

        # Show BTC events first
        print("\n=== BITCOIN/BTC EVENTS ===\n")
        if btc_events:
            for e in btc_events:
                mins = None
                if e["soonest_end"]:
                    mins = (e["soonest_end"] - now).total_seconds() / 60
                print(f"Title: {e['title']}")
                print(f"  Slug: {e['slug']}")
                print(f"  Markets: {e['active_markets']} active / {e['total_markets']} total")
                if mins:
                    print(f"  Next expiry: {mins:.1f} minutes")
                else:
                    print(f"  Next expiry: NONE (all expired)")
                print()
        else:
            print("  No BTC events found!")

        # Show first few other events
        print("\n=== OTHER EVENTS (first 20) ===\n")
        for e in other_events[:20]:
            print(f"  - {e['title'][:60]}")
            print(f"    slug: {e['slug']}")

if __name__ == "__main__":
    asyncio.run(main())
