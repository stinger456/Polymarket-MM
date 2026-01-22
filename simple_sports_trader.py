#!/usr/bin/env python3
"""
Simple Sports Trader - Actually places orders on Polymarket sports markets.
"""
import os
import sys
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# Config from .env
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))  # $5 per order
MIN_EDGE = float(os.getenv("MIN_EDGE_THRESHOLD", "0.02"))  # 2% minimum edge

GAMMA_URL = "https://gamma-api.polymarket.com"


async def fetch_sports_markets():
    """Fetch active sports markets."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(f"{GAMMA_URL}/events", params={
            "active": "true",
            "closed": "false",
            "limit": 100,
        })
        events = resp.json()

    # Find sports events
    sports_keywords = ["vs", "NBA", "NFL", "MLB", "NHL", "soccer", "UFC", "boxing"]
    markets = []

    for event in events:
        title = event.get("title", "").lower()
        if not any(kw.lower() in title for kw in sports_keywords):
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
                    "question": market.get("question", event.get("title", "")),
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

    # Set API credentials
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)

    return client


def place_order(client, token_id, price, size):
    """Place a limit order."""
    try:
        order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)
        return resp.get("orderID")
    except Exception as e:
        print(f"  Order failed: {e}")
        return None


async def main():
    print("=" * 60)
    print("   SIMPLE SPORTS TRADER")
    print("=" * 60)
    print(f"Order size: ${ORDER_SIZE}")
    print(f"Min edge: {MIN_EDGE:.1%}")
    print()

    # Fetch markets
    print("Fetching sports markets...")
    markets = await fetch_sports_markets()
    print(f"Found {len(markets)} sports markets")
    print()

    if not markets:
        print("No sports markets found!")
        return

    # Create client
    print("Connecting to Polymarket...")
    client = create_client()
    print("Connected!")
    print()

    # Find markets with edge and place orders
    orders_placed = 0

    for market in markets:
        yes_price = market["yes_price"]
        no_price = market["no_price"]
        total = yes_price + no_price

        # Check for edge (prices sum to less than $1)
        edge = 1.0 - total
        if edge < MIN_EDGE:
            continue

        print(f"Found edge: {market['question'][:50]}...")
        print(f"  {market['outcomes'][0]}: ${yes_price:.3f}")
        print(f"  {market['outcomes'][1]}: ${no_price:.3f}")
        print(f"  Total: ${total:.3f} | Edge: {edge:.2%}")

        # Calculate bid prices (slightly below current)
        yes_bid = round(yes_price - 0.01, 2)
        no_bid = round(no_price - 0.01, 2)

        # Make sure bids still have edge
        if yes_bid + no_bid >= 0.98:
            yes_bid = round((0.98 - edge/2) * yes_price / total, 2)
            no_bid = round((0.98 - edge/2) * no_price / total, 2)

        print(f"  Placing bids: YES @ ${yes_bid:.2f}, NO @ ${no_bid:.2f}")

        # Place orders
        yes_order = place_order(client, market["yes_token"], yes_bid, ORDER_SIZE)
        if yes_order:
            print(f"  YES order placed: {yes_order}")
            orders_placed += 1

        no_order = place_order(client, market["no_token"], no_bid, ORDER_SIZE)
        if no_order:
            print(f"  NO order placed: {no_order}")
            orders_placed += 1

        print()

        # Limit orders per run
        if orders_placed >= 10:
            print("Reached order limit for this run")
            break

    print("=" * 60)
    print(f"Total orders placed: {orders_placed}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
