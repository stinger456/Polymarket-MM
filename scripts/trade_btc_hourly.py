#!/usr/bin/env python3
"""
Simple BTC Hourly Market Maker - DIRECT TRADING
No complex discovery - just finds current hour's market and trades it.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY


def get_current_hour_slug() -> str:
    """Generate the slug for current hour's BTC market."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)

    month = now.strftime("%B").lower()
    day = now.day
    hour = now.hour

    if hour == 0:
        hour_str = "12am"
    elif hour < 12:
        hour_str = f"{hour}am"
    elif hour == 12:
        hour_str = "12pm"
    else:
        hour_str = f"{hour - 12}pm"

    slug = f"bitcoin-up-or-down-{month}-{day}-{hour_str}-et"
    return slug


def fetch_event_by_slug(slug: str) -> Optional[dict]:
    """Fetch event directly by slug."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    with httpx.Client(headers=headers, timeout=30) as client:
        resp = client.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug}
        )
        events = resp.json()
        if events:
            return events[0]
    return None


def get_market_tokens(event: dict) -> Optional[Tuple[str, str, str]]:
    """Extract YES and NO token IDs from event."""
    markets = event.get("markets", [])

    for market in markets:
        if market.get("closed"):
            continue

        question = market.get("question", "")
        tokens = market.get("clobTokenIds", [])

        if isinstance(tokens, str):
            tokens = json.loads(tokens)

        if len(tokens) >= 2:
            return tokens[0], tokens[1], question
    return None


def create_client() -> ClobClient:
    """Create authenticated CLOB client."""
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=sig_type,
        funder=safe_address,
    )

    try:
        creds = client.create_api_key()
    except:
        creds = client.derive_api_key()
    client.set_api_creds(creds)
    return client


def place_order(client: ClobClient, token_id: str, price: float, size: float) -> Optional[str]:
    """Place a BUY order with neg_risk=True."""
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        options = PartialCreateOrderOptions(neg_risk=True)
        signed = client.create_order(order_args, options)
        resp = client.post_order(signed, OrderType.GTC)
        return resp.get("orderID") or resp.get("id")
    except Exception as e:
        print(f"Order failed: {e}")
        return None


def main():
    print("=" * 60)
    print("BTC HOURLY MARKET MAKER - DIRECT TRADING")
    print("=" * 60)

    slug = get_current_hour_slug()
    print(f"\nLooking for: {slug}")

    event = fetch_event_by_slug(slug)

    if not event:
        print(f"Current hour not found, trying next hour...")
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        next_hour = datetime.now(et) + timedelta(hours=1)
        month = next_hour.strftime("%B").lower()
        day = next_hour.day
        hour = next_hour.hour
        if hour == 0:
            hour_str = "12am"
        elif hour < 12:
            hour_str = f"{hour}am"
        elif hour == 12:
            hour_str = "12pm"
        else:
            hour_str = f"{hour - 12}pm"
        slug = f"bitcoin-up-or-down-{month}-{day}-{hour_str}-et"
        print(f"Trying: {slug}")
        event = fetch_event_by_slug(slug)

    if not event:
        print("\n❌ No active BTC hourly market found!")
        return

    print(f"\n✅ Found: {event.get('title')}")

    result = get_market_tokens(event)
    if not result:
        print("❌ No open markets found")
        return

    yes_token, no_token, question = result
    print(f"Market: {question}")
    print(f"YES: {yes_token[:40]}...")
    print(f"NO: {no_token[:40]}...")

    print("\nConnecting...")
    client = create_client()
    print("✅ Connected!")

    ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "10"))

    print("\nFetching orderbook...")
    try:
        yes_book = client.get_order_book(yes_token)
        no_book = client.get_order_book(no_token)
        yes_best_bid = float(yes_book.bids[0].price) if yes_book.bids else 0.40
        no_best_bid = float(no_book.bids[0].price) if no_book.bids else 0.40
        print(f"YES best bid: {yes_best_bid:.2f}")
        print(f"NO best bid: {no_best_bid:.2f}")
        yes_bid_price = round(yes_best_bid - 0.01, 2)
        no_bid_price = round(no_best_bid - 0.01, 2)
    except Exception as e:
        print(f"Orderbook error: {e}")
        yes_bid_price = 0.45
        no_bid_price = 0.45

    if yes_bid_price + no_bid_price >= 0.98:
        yes_bid_price = 0.48
        no_bid_price = 0.48

    print(f"\n{'='*60}")
    print("PLACING ORDERS")
    print(f"{'='*60}")
    print(f"YES BUY: {ORDER_SIZE} @ ${yes_bid_price:.2f}")
    print(f"NO BUY:  {ORDER_SIZE} @ ${no_bid_price:.2f}")
    print(f"Total: ${yes_bid_price + no_bid_price:.2f} | Profit if filled: ${1 - yes_bid_price - no_bid_price:.2f}")

    print("\nPlacing YES order...")
    yes_order_id = place_order(client, yes_token, yes_bid_price, ORDER_SIZE)
    if yes_order_id:
        print(f"✅ YES: {yes_order_id}")
    else:
        print("❌ YES failed")

    print("Placing NO order...")
    no_order_id = place_order(client, no_token, no_bid_price, ORDER_SIZE)
    if no_order_id:
        print(f"✅ NO: {no_order_id}")
    else:
        print("❌ NO failed")

    if yes_order_id or no_order_id:
        print(f"\n{'='*60}")
        print("ORDERS LIVE - Press Ctrl+C to cancel and exit")
        print(f"{'='*60}")
        try:
            while True:
                time.sleep(10)
                orders = client.get_orders()
                active = [o for o in (orders or []) if o.get("status") == "live"]
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Active: {len(active)}")
        except KeyboardInterrupt:
            print("\nCancelling...")
            if yes_order_id:
                try: client.cancel(yes_order_id)
                except: pass
            if no_order_id:
                try: client.cancel(no_order_id)
                except: pass
            print("Done!")


if __name__ == "__main__":
    main()
