#!/usr/bin/env python3
"""
Bitcoin Hourly Market Maker - Trades ONLY the hourly UP/DOWN markets.
Example: bitcoin-up-or-down-january-21-11pm-et
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY

ET = ZoneInfo("America/New_York")

def get_hourly_slug(dt=None):
    """Generate slug like: bitcoin-up-or-down-january-22-10am-et"""
    if dt is None:
        dt = datetime.now(ET)
    
    month = dt.strftime("%B").lower()
    day = dt.day
    hour = dt.hour
    
    if hour == 0:
        h = "12am"
    elif hour < 12:
        h = f"{hour}am"
    elif hour == 12:
        h = "12pm"
    else:
        h = f"{hour-12}pm"
    
    return f"bitcoin-up-or-down-{month}-{day}-{h}-et"

def fetch_event(slug):
    """Fetch event by slug from Gamma API."""
    print(f"   Fetching: {slug}")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    
    with httpx.Client(headers=headers, timeout=30) as client:
        resp = client.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug}
        )
        events = resp.json()
        return events[0] if events else None

def get_tokens(event):
    """Get YES/NO token IDs from event."""
    for market in event.get("markets", []):
        if market.get("closed"):
            continue
        
        tokens = market.get("clobTokenIds", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        
        if len(tokens) >= 2:
            return tokens[0], tokens[1], market.get("question", "")
    
    return None, None, None

def main():
    print("=" * 60)
    print("BITCOIN HOURLY MARKET MAKER")
    print("=" * 60)
    
    now = datetime.now(ET)
    print(f"\nCurrent time (ET): {now.strftime('%Y-%m-%d %I:%M %p')}")
    
    # Try current hour, then next hour
    event = None
    for offset in [0, 1]:
        dt = now + timedelta(hours=offset)
        slug = get_hourly_slug(dt)
        
        print(f"\n{'Current' if offset == 0 else 'Next'} hour market:")
        event = fetch_event(slug)
        
        if event:
            print(f"   ✅ Found: {event.get('title')}")
            break
        else:
            print(f"   ❌ Not found")
    
    if not event:
        print("\n❌ No hourly BTC market available!")
        print("   These markets may only exist at certain times.")
        return
    
    yes_token, no_token, question = get_tokens(event)
    
    if not yes_token:
        print("❌ No open market with tokens found")
        return
    
    print(f"\nMarket: {question}")
    print(f"YES: {yes_token[:40]}...")
    print(f"NO:  {no_token[:40]}...")
    
    # Connect to Polymarket
    print("\nConnecting to Polymarket...")
    
    client = ClobClient(
        "https://clob.polymarket.com",
        key=os.getenv("POLY_PRIVATE_KEY"),
        chain_id=137,
        signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "2")),
        funder=os.getenv("POLY_SAFE_ADDRESS"),
    )
    
    try:
        creds = client.create_api_key()
    except:
        creds = client.derive_api_key()
    client.set_api_creds(creds)
    print("✅ Connected!")
    
    # Get orderbook
    print("\nFetching orderbook...")
    try:
        yes_book = client.get_order_book(yes_token)
        no_book = client.get_order_book(no_token)
        
        yes_bid = float(yes_book.bids[0].price) if yes_book.bids else 0.45
        no_bid = float(no_book.bids[0].price) if no_book.bids else 0.45
        
        print(f"YES best bid: ${yes_bid:.2f}")
        print(f"NO best bid:  ${no_bid:.2f}")
    except Exception as e:
        print(f"Orderbook error: {e}")
        yes_bid = 0.45
        no_bid = 0.45
    
    # Calculate order prices (bid below best bid)
    yes_price = round(max(0.01, yes_bid - 0.01), 2)
    no_price = round(max(0.01, no_bid - 0.01), 2)
    
    # Ensure profit margin
    if yes_price + no_price >= 0.97:
        yes_price = 0.47
        no_price = 0.47
    
    size = float(os.getenv("BASE_ORDER_SIZE", "5"))
    
    print(f"\n{'='*60}")
    print("PLACING ORDERS (neg_risk=True for BTC markets)")
    print(f"{'='*60}")
    print(f"YES BUY: {size} @ ${yes_price:.2f}")
    print(f"NO BUY:  {size} @ ${no_price:.2f}")
    print(f"Total cost: ${yes_price + no_price:.2f}")
    print(f"Profit if both fill: ${1 - yes_price - no_price:.2f}")
    
    orders = []
    
    for token, price, name in [(yes_token, yes_price, "YES"), (no_token, no_price, "NO")]:
        print(f"\nPlacing {name}...")
        try:
            args = OrderArgs(token_id=token, price=price, size=size, side=BUY)
            opts = PartialCreateOrderOptions(neg_risk=True)
            signed = client.create_order(args, opts)
            resp = client.post_order(signed, OrderType.GTC)
            
            oid = resp.get("orderID") or resp.get("id")
            if oid:
                print(f"✅ {name}: {oid[:40]}...")
                orders.append(oid)
            else:
                print(f"❌ {name} failed: {resp}")
        except Exception as e:
            print(f"❌ {name} error: {e}")
    
    if orders:
        print(f"\n{'='*60}")
        print(f"ORDERS LIVE! Press Ctrl+C to cancel and exit")
        print(f"{'='*60}")
        
        try:
            while True:
                time.sleep(15)
                active = client.get_orders()
                live = [o for o in (active or []) if o.get("status") == "live"]
                print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] Active orders: {len(live)}")
        except KeyboardInterrupt:
            print("\nCancelling orders...")
            for oid in orders:
                try:
                    client.cancel(oid)
                    print(f"Cancelled: {oid[:30]}...")
                except:
                    pass
            print("Done!")

if __name__ == "__main__":
    main()
