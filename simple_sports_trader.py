#!/usr/bin/env python3
"""
Sports Market Maker - Places BUY orders on both sides of sports games.
"""
import os
import sys
import asyncio
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
ORDER_SIZE = 5

async def get_markets():
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get("https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "limit": 200})
        events = resp.json()

    # Debug: show what we got
    print(f"API returned {len(events)} events")

    # Show first 5 event titles
    print("Sample events:")
    for e in events[:5]:
        print(f"  - {e.get('title', 'NO TITLE')[:60]}")
    print()

    markets = []
    # Sports keywords - look for ANY of these
    sports_words = ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
                    "hockey", "baseball", "ufc", "boxing", "tennis", "golf",
                    "lakers", "celtics", "chiefs", "eagles", "yankees", "dodgers",
                    "warriors", "bulls", "heat", "knicks", "nets", "mavs",
                    "game", "match", "win", "beat"]

    for event in events:
        title = event.get("title", "").lower()

        # Check if ANY sports word is in title
        is_sports = any(word in title for word in sports_words)
        if not is_sports:
            continue

        for m in event.get("markets", []):
            tokens = m.get("clobTokenIds", [])
            if len(tokens) >= 2:
                try:
                    prices = [float(p) for p in eval(m.get("outcomePrices", "[]"))]
                    outcomes = eval(m.get("outcomes", "[]"))
                    if len(prices) >= 2:
                        markets.append({
                            "title": event.get("title", ""),
                            "yes_token": tokens[0],
                            "no_token": tokens[1],
                            "yes_price": prices[0],
                            "no_price": prices[1],
                            "team1": outcomes[0] if outcomes else "YES",
                            "team2": outcomes[1] if len(outcomes) > 1 else "NO",
                        })
                except:
                    pass
    return markets

def place_order(client, token_id, price, size):
    try:
        order = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)
        return resp.get("orderID")
    except Exception as e:
        print(f"  Error: {e}")
        return None

async def main():
    print("\n=== SPORTS MARKET MAKER ===\n")

    markets = await get_markets()
    print(f"Found {len(markets)} sports markets\n")

    if not markets:
        print("No sports markets found!")
        print("\nLet's try trading ANY market instead...")
        # Fallback: get ANY market
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.get("https://gamma-api.polymarket.com/events",
                params={"active": "true", "closed": "false", "limit": 50})
            events = resp.json()

        for event in events[:20]:
            for m in event.get("markets", []):
                tokens = m.get("clobTokenIds", [])
                if len(tokens) >= 2:
                    try:
                        prices = [float(p) for p in eval(m.get("outcomePrices", "[]"))]
                        outcomes = eval(m.get("outcomes", "[]"))
                        if len(prices) >= 2 and prices[0] > 0.1 and prices[0] < 0.9:
                            markets.append({
                                "title": event.get("title", ""),
                                "yes_token": tokens[0],
                                "no_token": tokens[1],
                                "yes_price": prices[0],
                                "no_price": prices[1],
                                "team1": outcomes[0] if outcomes else "YES",
                                "team2": outcomes[1] if len(outcomes) > 1 else "NO",
                            })
                    except:
                        pass
        print(f"Found {len(markets)} total markets\n")

    if not markets:
        print("Still no markets! Check Polymarket directly.")
        return

    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected to Polymarket\n")

    count = 0
    for m in markets[:10]:
        yes_bid = max(0.01, round(m["yes_price"] - 0.05, 2))
        no_bid = max(0.01, round(m["no_price"] - 0.05, 2))

        print(f"{m['title'][:50]}...")
        print(f"  {m['team1']}: ${m['yes_price']:.2f} -> bid ${yes_bid:.2f}")
        print(f"  {m['team2']}: ${m['no_price']:.2f} -> bid ${no_bid:.2f}")

        o1 = place_order(client, m["yes_token"], yes_bid, ORDER_SIZE)
        if o1: print(f"  YES ORDER: {o1[:16]}...")

        o2 = place_order(client, m["no_token"], no_bid, ORDER_SIZE)
        if o2: print(f"  NO ORDER: {o2[:16]}...")

        if o1 or o2: count += 1
        print()
        time.sleep(0.3)

    print(f"=== Placed orders on {count} markets ===")
    print("Check: https://polymarket.com/portfolio")

if __name__ == "__main__":
    asyncio.run(main())
