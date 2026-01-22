#!/usr/bin/env python3
"""
Market Maker - Trade TODAY'S LIVE sports games only
"""
import os
import time
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
ORDER_SIZE = 5

def get_todays_games():
    """Get TODAY's live sports games from Gamma API."""
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = httpx.get(
        "https://gamma-api.polymarket.com/events",
        params={"active": "true", "closed": "false", "limit": 200},
        headers=headers,
        timeout=30.0
    )
    events = resp.json()

    print(f"Gamma API returned {len(events)} events")

    today = datetime.now(timezone.utc).date()
    games = []

    for event in events:
        title = event.get("title", "")

        # Must be a game matchup (contains "vs" or "@")
        if " vs " not in title.lower() and " @ " not in title.lower():
            continue

        for m in event.get("markets", []):
            # Check for game start time
            game_time_str = m.get("gameStartTime") or event.get("startDate")
            if not game_time_str:
                continue

            # Parse game time
            try:
                game_time_str = game_time_str.replace("Z", "+00:00")
                game_time = datetime.fromisoformat(game_time_str)
                game_date = game_time.date()
            except:
                continue

            # Only today's games
            if game_date != today:
                continue

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

            games.append({
                "title": title,
                "question": m.get("question", ""),
                "game_time": game_time,
                "yes_token": clob_ids[0],
                "no_token": clob_ids[1],
                "yes_price": prices[0],
                "no_price": prices[1],
                "outcome1": outcomes[0],
                "outcome2": outcomes[1],
            })

    return games

def main():
    print("\n=== TODAY'S LIVE SPORTS GAMES ===\n")
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n")

    # Get today's games
    print("Fetching today's games from Gamma API...")
    games = get_todays_games()
    print(f"Found {len(games)} games scheduled for today\n")

    if not games:
        print("No games found for today!")
        print("\nTrying to find ANY upcoming games...")

        # Fallback: get any games in next 7 days
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = httpx.get(
            "https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "limit": 200},
            headers=headers,
            timeout=30.0
        )
        events = resp.json()

        for event in events:
            title = event.get("title", "")
            if " vs " in title.lower() or " @ " in title.lower():
                for m in event.get("markets", []):
                    game_time_str = m.get("gameStartTime") or event.get("startDate")
                    if game_time_str:
                        print(f"  {title[:50]}")
                        print(f"    Time: {game_time_str}")
                        break
        return

    # Show games found
    print("Today's games:")
    for g in games[:10]:
        time_str = g["game_time"].strftime("%H:%M UTC")
        print(f"  - {g['title'][:45]} @ {time_str}")
        print(f"    {g['outcome1']}: ${g['yes_price']:.2f} | {g['outcome2']}: ${g['no_price']:.2f}")
    print()

    # Connect to CLOB
    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected to CLOB!\n")

    # Place orders
    print("Placing orders on today's games...\n")
    count = 0

    for g in games[:15]:
        title = g["title"][:45]
        yes_token = g["yes_token"]
        no_token = g["no_token"]
        yes_price = g["yes_price"]
        no_price = g["no_price"]

        # Skip extreme prices
        if yes_price < 0.1 or yes_price > 0.9:
            continue

        bid1 = max(0.01, round(yes_price - 0.05, 2))
        bid2 = max(0.01, round(no_price - 0.05, 2))

        print(f"{title}...")
        print(f"  {g['outcome1']}: ${yes_price:.2f} -> bid ${bid1:.2f}")
        print(f"  {g['outcome2']}: ${no_price:.2f} -> bid ${bid2:.2f}")

        # Check orderbook exists
        try:
            book = client.get_order_book(yes_token)
            has_book = book and (book.get("bids") or book.get("asks"))
        except Exception as e:
            print(f"  No orderbook: {str(e)[:40]}")
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

    print(f"=== Orders placed on {count} games ===")
    print("Check: https://polymarket.com/portfolio")

if __name__ == "__main__":
    main()
