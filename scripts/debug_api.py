#!/usr/bin/env python3
"""Debug script to see what events actually exist on Polymarket."""

import asyncio
import httpx

GAMMA_URL = "https://gamma-api.polymarket.com"

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("Fetching ALL events from Polymarket...\n")

        response = await client.get(
            f"{GAMMA_URL}/events",
            params={"active": "true", "closed": "false", "limit": 200}
        )
        events = response.json()

        print(f"Total events: {len(events)}\n")
        print("=" * 80)

        # Find any BTC-related events
        btc_events = []
        for event in events:
            title = event.get("title", "")
            slug = event.get("slug", "")

            if "bitcoin" in title.lower() or "btc" in title.lower():
                btc_events.append(event)
                markets = event.get("markets", [])
                print(f"TITLE: {title}")
                print(f"SLUG: {slug}")
                print(f"MARKETS: {len(markets)}")

                for m in markets[:3]:
                    print(f"  - {m.get('question', '')[:70]}")
                    print(f"    endDate: {m.get('endDate')}")
                    print(f"    closed: {m.get('closed')}")
                    print(f"    conditionId: {m.get('conditionId', '')[:40]}...")
                    tokens = m.get("clobTokenIds", [])
                    print(f"    clobTokenIds: {len(tokens)} tokens")
                    if tokens:
                        print(f"      YES: {tokens[0][:40]}..." if len(tokens) > 0 else "")
                        print(f"      NO: {tokens[1][:40]}..." if len(tokens) > 1 else "")
                print("-" * 80)

        print(f"\nFound {len(btc_events)} BTC-related events")

        if not btc_events:
            print("\nNo BTC events found. Showing first 10 events:")
            for event in events[:10]:
                print(f"  - {event.get('title', 'N/A')}")

if __name__ == "__main__":
    asyncio.run(main())
