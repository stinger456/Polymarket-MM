#!/usr/bin/env python3
"""
Market Maker - Trade LIVE sports markets from Gamma API
"""
import os
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

def get_live_sports_markets():
    """Get live sports markets from Gamma API."""
    headers = {"User-Agent": "Mozilla/5.0"}

    # Fetch events tagged as sports
    resp = httpx.get(
        "https://gamma-api.polymarket.com/events",
        params={"active": "true", "closed": "false", "limit": 100},
        headers=headers,
        timeout=30.0
    )
    events = resp.json()

    print(f"Gamma API returned {len(events)} events")

    # Find sports events
    sports_keywords = ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
                       "hockey", "baseball", "ufc", "boxing", "tennis",
                       "lakers", "celtics", "chiefs", "eagles", "yankees", "warriors",
                       "bulls", "heat", "knicks", "nets", "mavs", "nuggets", "bucks",
                       "pistons", "wizards", "rockets", "spurs", "suns", "grizzlies",
                       "cavaliers", "magic", "pacers", "hornets", "hawks", "raptors",
                       "76ers", "clippers", "kings", "blazers", "jazz", "pelicans",
                       "timberwolves", "thunder", "vs", "game", "match", "win"]

    markets = []
    for event in events:
        title = event.get("title", "").lower()

        # Check if sports related
        if not any(kw in title for kw in sports_keywords):
            continue

        for m in event.get("markets", []):
            clob_ids = m.get("clobTokenIds", [])
            if len(clob_ids) < 2:
                continue

            try:
                prices = [float(p) for p in eval(str(m.get("outcomePrices", "[]")))]
                outcomes = eval(str(m.get("outcomes", "[]")))
            except:
                continue

            if len(prices) < 2 or len(outcomes) < 2:
                continue

            markets.append({
                "title": event.get("title", ""),
                "question": m.get("question", ""),
                "condition_id": m.get("conditionId", ""),
                "yes_token": clob_ids[0],
                "no_token": clob_ids[1],
                "yes_price": prices[0],
                "no_price": prices[1],
                "outcome1": outcomes[0],
                "outcome2": outcomes[1],
                "active": m.get("active", False),
            })

    return markets

def main():
    print("\n=== LIVE SPORTS MARKET MAKER ===\n")

    # Get sports markets from Gamma
    print("Fetching sports markets from Gamma API...")
    gamma_markets = get_live_sports_markets()
    print(f"Found {len(gamma_markets)} sports markets\n")

    if gamma_markets:
        print("Sports markets found:")
        for m in gamma_markets[:10]:
            print(f"  - {m['title'][:50]}")
            print(f"    {m['outcome1']}: ${m['yes_price']:.2f} | {m['outcome2']}: ${m['no_price']:.2f}")
        print()

    # Connect to CLOB
    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected to CLOB!\n")

    # For each Gamma market, try to find matching CLOB market by condition_id
    print("Placing orders on sports markets...\n")
    count = 0

    for m in gamma_markets[:15]:
        title = m["title"][:45]
        yes_token = m["yes_token"]
        no_token = m["no_token"]
        yes_price = m["yes_price"]
        no_price = m["no_price"]

        # Skip extreme prices
        if yes_price < 0.1 or yes_price > 0.9:
            continue

        bid1 = max(0.01, round(yes_price - 0.05, 2))
        bid2 = max(0.01, round(no_price - 0.05, 2))

        print(f"{title}...")
        print(f"  {m['outcome1']}: ${yes_price:.2f} -> bid ${bid1:.2f}")
        print(f"  {m['outcome2']}: ${no_price:.2f} -> bid ${bid2:.2f}")

        # First verify this market exists in CLOB by getting orderbook
        try:
            book = client.get_order_book(yes_token)
            has_book = book and (book.get("bids") or book.get("asks"))
        except Exception as e:
            print(f"  No CLOB orderbook: {str(e)[:40]}")
            print()
            continue

        if not has_book:
            print(f"  Empty orderbook, skipping")
            print()
            continue

        oid1 = None
        oid2 = None

        try:
            order1 = OrderArgs(token_id=yes_token, price=bid1, size=ORDER_SIZE, side=BUY)
            signed1 = client.create_order(order1)
            resp1 = client.post_order(signed1, OrderType.GTC)
            oid1 = resp1.get("orderID")
            if oid1:
                print(f"  ORDER 1: {oid1[:20]}...")
        except Exception as e:
            print(f"  Error 1: {str(e)[:50]}")

        try:
            order2 = OrderArgs(token_id=no_token, price=bid2, size=ORDER_SIZE, side=BUY)
            signed2 = client.create_order(order2)
            resp2 = client.post_order(signed2, OrderType.GTC)
            oid2 = resp2.get("orderID")
            if oid2:
                print(f"  ORDER 2: {oid2[:20]}...")
        except Exception as e:
            print(f"  Error 2: {str(e)[:50]}")

        if oid1 or oid2:
            count += 1
        print()
        time.sleep(0.3)

    print(f"=== Orders placed on {count} markets ===")
    print("Check: https://polymarket.com/portfolio")

if __name__ == "__main__":
    main()
