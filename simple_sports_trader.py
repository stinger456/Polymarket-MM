#!/usr/bin/env python3
"""
Simple Sports Market Maker - Places orders on BOTH sides of sports markets.

Strategy:
- Find sports markets (games with "vs")
- Place BUY orders on BOTH YES and NO at prices below market
- If both fill: YES + NO = $1.00 payout, we paid less = PROFIT
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

# Config
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))  # Shares per side
SPREAD = 0.03  # How far below market to bid (3%)

GAMMA_URL = "https://gamma-api.polymarket.com"


async def fetch_sports_markets():
    """Fetch active sports markets with 'vs' in title."""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(f"{GAMMA_URL}/events", params={
            "active": "true", "closed": "false", "limit": 200,
        })
        events = resp.json()

    markets = []
    for event in events:
        title = event.get("title", "")
        # Only "vs" games (actual sports matchups)
        if " vs " not in title.lower() and " vs. " not in title.lower():
            continue

        for market in event.get("markets", []):
            clob_ids = market.get("clobTokenIds", [])
            if len(clob_ids) < 2:
                continue

            try:
                outcomes = eval(market.get("outcomes", "[]"))
                prices = [float(p) for p in eval(market.get("outcomePrices", "[]"))]
            except:
                continue

            if len(outcomes) >= 2 and len(prices) >= 2:
                markets.append({
                    "title": title,
                    "question": market.get("question", title),
                    "yes_token": clob_ids[0],
                    "no_token": clob_ids[1],
                    "yes_price": prices[0],
                    "no_price": prices[1],
                    "outcomes": outcomes,
                })

    return markets


def create_client():
    """Create authenticated CLOB client."""
    if not PRIVATE_KEY or not FUNDER:
        print("ERROR: Set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS in .env")
        sys.exit(1)

    client = ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=SIG_TYPE,
        funder=FUNDER,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client


def place_order(client, token_id, price, size):
    """Place a BUY limit order."""
    try:
        order = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)
        return resp.get("orderID")
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


async def main():
    print("=" * 60)
    print("   SPORTS MARKET MAKER")
    print("=" * 60)
    print(f"Order size: {ORDER_SIZE} shares per side")
    print(f"Spread: {SPREAD:.1%} below market")
    print()

    # Fetch markets
    print("Fetching sports 'vs' markets...")
    markets = await fetch_sports_markets()
    print(f"Found {len(markets)} sports matchups")
    print()

    if not markets:
        print("No sports markets found!")
        return

    # Create client
    print("Connecting to Polymarket...")
    client = create_client()
    print("Connected!")
    print()

    orders_placed = 0
    markets_traded = 0

    for market in markets:
        yes_price = market["yes_price"]
        no_price = market["no_price"]

        # Calculate our bid prices (below market)
        yes_bid = round(yes_price * (1 - SPREAD), 2)
        no_bid = round(no_price * (1 - SPREAD), 2)

        # Minimum price is $0.01
        yes_bid = max(0.01, yes_bid)
        no_bid = max(0.01, no_bid)

        # Check if we'd profit: our cost < $1.00
        total_cost = yes_bid + no_bid
        if total_cost >= 0.98:  # Need at least 2% margin
            continue

        profit_if_both_fill = 1.00 - total_cost

        print(f"MARKET: {market['title'][:55]}...")
        print(f"  {market['outcomes'][0]}: market ${yes_price:.2f} -> bid ${yes_bid:.2f}")
        print(f"  {market['outcomes'][1]}: market ${no_price:.2f} -> bid ${no_bid:.2f}")
        print(f"  If both fill: ${total_cost:.2f} cost -> $1.00 = ${profit_if_both_fill:.2f} profit ({profit_if_both_fill/total_cost*100:.1f}%)")

        # Place YES order
        print(f"  Placing YES bid @ ${yes_bid:.2f}...")
        yes_order = place_order(client, market["yes_token"], yes_bid, ORDER_SIZE)
        if yes_order:
            print(f"    ORDER PLACED: {yes_order[:20]}...")
            orders_placed += 1

        # Place NO order
        print(f"  Placing NO bid @ ${no_bid:.2f}...")
        no_order = place_order(client, market["no_token"], no_bid, ORDER_SIZE)
        if no_order:
            print(f"    ORDER PLACED: {no_order[:20]}...")
            orders_placed += 1

        if yes_order or no_order:
            markets_traded += 1

        print()

        # Rate limit
        time.sleep(0.5)

        # Limit per run
        if markets_traded >= 5:
            print("Reached 5 markets for this run")
            break

    print("=" * 60)
    print(f"Markets traded: {markets_traded}")
    print(f"Total orders placed: {orders_placed}")
    print("=" * 60)
    print()
    print("Orders are now LIVE on Polymarket!")
    print("Check your positions at: https://polymarket.com/portfolio")


if __name__ == "__main__":
    asyncio.run(main())
