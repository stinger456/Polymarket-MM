#!/usr/bin/env python3
"""
Find ANY tradeable BTC market and place orders.
"""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY

def main():
    print("=" * 60)
    print("POLYMARKET BTC MARKET FINDER & TRADER")
    print("=" * 60)

    # Step 1: Find ALL bitcoin events
    print("\n1. Searching for Bitcoin markets...")
    
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    
    with httpx.Client(headers=headers, timeout=30) as http:
        # Get events without filters
        resp = http.get("https://gamma-api.polymarket.com/events", params={"limit": 500})
        events = resp.json()
        
        print(f"   Total events: {len(events)}")
        
        # Find ALL bitcoin-related events
        btc_events = []
        for e in events:
            slug = e.get("slug", "").lower()
            title = e.get("title", "").lower()
            if "bitcoin" in slug or "bitcoin" in title or "btc" in slug or "btc" in title:
                markets = e.get("markets", [])
                open_markets = [m for m in markets if not m.get("closed")]
                if open_markets:
                    btc_events.append({
                        "title": e.get("title"),
                        "slug": e.get("slug"),
                        "markets": open_markets
                    })
        
        print(f"\n2. Found {len(btc_events)} Bitcoin events with open markets:")
        
        for i, e in enumerate(btc_events[:10]):
            print(f"\n   [{i}] {e['title']}")
            print(f"       Slug: {e['slug']}")
            print(f"       Open markets: {len(e['markets'])}")
        
        if not btc_events:
            print("\n❌ No Bitcoin markets found!")
            return
        
        # Let user pick or use first one
        print(f"\n3. Using first available market...")
        
        event = btc_events[0]
        market = event["markets"][0]
        
        question = market.get("question", "")
        tokens = market.get("clobTokenIds", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        
        if len(tokens) < 2:
            print("❌ Market has no tokens")
            return
        
        yes_token = tokens[0]
        no_token = tokens[1]
        
        print(f"\n   Market: {question}")
        print(f"   YES token: {yes_token[:50]}...")
        print(f"   NO token: {no_token[:50]}...")
        
        # Step 2: Connect to Polymarket
        print("\n4. Connecting to Polymarket...")
        
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
        
        print("   ✅ Connected!")
        
        # Step 3: Get orderbook
        print("\n5. Fetching orderbook...")
        
        try:
            yes_book = client.get_order_book(yes_token)
            no_book = client.get_order_book(no_token)
            
            yes_bid = float(yes_book.bids[0].price) if yes_book.bids else 0.40
            yes_ask = float(yes_book.asks[0].price) if yes_book.asks else 0.60
            no_bid = float(no_book.bids[0].price) if no_book.bids else 0.40
            no_ask = float(no_book.asks[0].price) if no_book.asks else 0.60
            
            print(f"   YES: Bid={yes_bid:.2f} Ask={yes_ask:.2f}")
            print(f"   NO:  Bid={no_bid:.2f} Ask={no_ask:.2f}")
        except Exception as e:
            print(f"   Orderbook error: {e}")
            yes_bid, no_bid = 0.45, 0.45
        
        # Step 4: Place orders
        print("\n6. Placing orders...")
        
        # Place bids slightly below best bid
        yes_price = round(max(0.01, yes_bid - 0.02), 2)
        no_price = round(max(0.01, no_bid - 0.02), 2)
        size = float(os.getenv("BASE_ORDER_SIZE", "5"))
        
        print(f"   YES BUY: {size} @ ${yes_price}")
        print(f"   NO BUY:  {size} @ ${no_price}")
        
        # Check if this is a neg_risk market (BTC markets usually are)
        is_neg_risk = "up" in question.lower() or "down" in question.lower() or "above" in question.lower()
        
        for token, price, name in [(yes_token, yes_price, "YES"), (no_token, no_price, "NO")]:
            try:
                order_args = OrderArgs(
                    token_id=token,
                    price=price,
                    size=size,
                    side=BUY,
                )
                
                if is_neg_risk:
                    options = PartialCreateOrderOptions(neg_risk=True)
                    signed = client.create_order(order_args, options)
                else:
                    signed = client.create_order(order_args)
                
                resp = client.post_order(signed, OrderType.GTC)
                order_id = resp.get("orderID") or resp.get("id")
                
                if order_id:
                    print(f"   ✅ {name} order placed: {order_id[:30]}...")
                else:
                    print(f"   ❌ {name} order failed: {resp}")
                    
            except Exception as e:
                print(f"   ❌ {name} order error: {e}")
        
        print("\n" + "=" * 60)
        print("DONE!")
        print("=" * 60)

if __name__ == "__main__":
    main()
