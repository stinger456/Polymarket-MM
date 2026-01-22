#!/usr/bin/env python3
"""
Simple direct trade script - just places orders on BTC hourly.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 60)
    print("POLYMARKET BTC HOURLY TRADER")
    print("=" * 60)
    print(f"Safe: {safe_address}")

    # Get current hour market
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    month = now.strftime("%B").lower()
    day = now.day
    hour = now.hour

    if hour == 0:
        h = "12am"
    elif hour < 12:
        h = f"{hour}am"
    elif hour == 12:
        h = "12pm"
    else:
        h = f"{hour-12}pm"

    slug = f"bitcoin-up-or-down-{month}-{day}-{h}-et"
    print(f"\nFetching: {slug}")

    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=30) as http:
        resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
        events = resp.json()

        if not events:
            # Try next hour
            next_h = now + timedelta(hours=1)
            month = next_h.strftime("%B").lower()
            day = next_h.day
            hour = next_h.hour
            if hour == 0:
                h = "12am"
            elif hour < 12:
                h = f"{hour}am"
            elif hour == 12:
                h = "12pm"
            else:
                h = f"{hour-12}pm"
            slug = f"bitcoin-up-or-down-{month}-{day}-{h}-et"
            print(f"Trying next hour: {slug}")
            resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
            events = resp.json()

    if not events:
        print("No BTC hourly market found!")
        return

    event = events[0]
    print(f"Found: {event.get('title')}")

    # Get tokens
    for market in event.get("markets", []):
        if market.get("closed"):
            continue
        tokens = market.get("clobTokenIds", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        prices = market.get("outcomePrices", [])
        if isinstance(prices, str):
            prices = json.loads(prices)
        if len(tokens) >= 2:
            yes_token = tokens[0]
            no_token = tokens[1]
            yes_price = float(prices[0]) if prices else 0.5
            no_price = float(prices[1]) if len(prices) > 1 else 0.5
            break
    else:
        print("No open market found!")
        return

    print(f"\nYES token: {yes_token[:30]}...")
    print(f"NO token: {no_token[:30]}...")
    print(f"Market prices: YES=${yes_price:.2f}, NO=${no_price:.2f}")

    # Create client
    print("\nConnecting...")
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=2,
        funder=safe_address,
    )

    try:
        creds = client.derive_api_key()
    except:
        creds = client.create_api_key()
    client.set_api_creds(creds)
    print("Connected!")

    # Calculate bid prices
    yes_bid = round(yes_price - 0.02, 2)
    no_bid = round(no_price - 0.02, 2)

    # Ensure profit margin
    if yes_bid + no_bid > 0.96:
        yes_bid = 0.47
        no_bid = 0.47

    yes_bid = max(0.01, yes_bid)
    no_bid = max(0.01, no_bid)

    size = 5.0  # $5 orders

    print(f"\n{'='*60}")
    print("PLACING ORDERS")
    print(f"{'='*60}")
    print(f"YES BUY: {size} @ ${yes_bid:.2f}")
    print(f"NO BUY:  {size} @ ${no_bid:.2f}")
    print(f"Total: ${yes_bid + no_bid:.2f} | Profit if both fill: ${1 - yes_bid - no_bid:.2f}")

    # Place YES order
    print("\nPlacing YES order...")
    try:
        args = OrderArgs(token_id=yes_token, price=yes_bid, size=size, side=BUY)
        opts = PartialCreateOrderOptions(neg_risk=True)
        signed = client.create_order(args, opts)
        resp = client.post_order(signed, OrderType.GTC)
        yes_id = resp.get("orderID") or resp.get("id")
        if yes_id:
            print(f"YES ORDER PLACED: {yes_id}")
        else:
            print(f"YES failed: {resp}")
            yes_id = None
    except Exception as e:
        print(f"YES error: {e}")
        yes_id = None

    # Place NO order
    print("\nPlacing NO order...")
    try:
        args = OrderArgs(token_id=no_token, price=no_bid, size=size, side=BUY)
        opts = PartialCreateOrderOptions(neg_risk=True)
        signed = client.create_order(args, opts)
        resp = client.post_order(signed, OrderType.GTC)
        no_id = resp.get("orderID") or resp.get("id")
        if no_id:
            print(f"NO ORDER PLACED: {no_id}")
        else:
            print(f"NO failed: {resp}")
            no_id = None
    except Exception as e:
        print(f"NO error: {e}")
        no_id = None

    if yes_id or no_id:
        print(f"\n{'='*60}")
        print("ORDERS LIVE!")
        print(f"{'='*60}")
    else:
        print("\nNo orders placed.")


if __name__ == "__main__":
    main()
