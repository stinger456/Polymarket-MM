#!/usr/bin/env python3
"""
Simple BTC Hourly Market Maker - DIRECT TRADING
No complex discovery - just finds current hour's market and trades it.

Strategy: Place BUY orders on both YES and NO at prices that sum to < $1.00
When both fill, you're fully hedged with guaranteed profit.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

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


def get_market_tokens(event: dict) -> Optional[Tuple[str, str, str, float, float]]:
    """Extract YES and NO token IDs and prices from event."""
    markets = event.get("markets", [])

    for market in markets:
        if market.get("closed"):
            continue

        question = market.get("question", "")
        tokens = market.get("clobTokenIds", [])

        # Get current prices from event data
        outcome_prices = market.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)

        if isinstance(tokens, str):
            tokens = json.loads(tokens)

        if len(tokens) >= 2:
            # Parse prices (YES=index 0, NO=index 1)
            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.50
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.50
            return tokens[0], tokens[1], question, yes_price, no_price
    return None


def create_client() -> ClobClient:
    """Create authenticated CLOB client."""
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    print(f"\n[DEBUG] Creating client:")
    print(f"  Private key: {private_key[:8]}...{private_key[-8:]}")
    print(f"  Safe address: {safe_address}")
    print(f"  Signature type: {sig_type}")

    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=sig_type,
        funder=safe_address,
    )

    # Try derive first (doesn't create new key if one exists)
    try:
        print("  Deriving API key...")
        creds = client.derive_api_key()
        print(f"  ✅ Derived: {creds.api_key[:20]}...")
    except Exception as e:
        print(f"  derive_api_key failed: {e}")
        try:
            print("  Creating new API key...")
            creds = client.create_api_key()
            print(f"  ✅ Created: {creds.api_key[:20]}...")
        except Exception as e2:
            print(f"  ❌ create_api_key failed: {e2}")
            raise

    client.set_api_creds(creds)
    return client


def get_orderbook_prices(client: ClobClient, yes_token: str, no_token: str) -> Tuple[dict, dict]:
    """Fetch orderbook and analyze prices for both tokens."""
    yes_book = {"best_bid": 0, "best_ask": 1, "bid_depth": 0, "ask_depth": 0}
    no_book = {"best_bid": 0, "best_ask": 1, "bid_depth": 0, "ask_depth": 0}

    try:
        yes_ob = client.get_order_book(yes_token)
        if yes_ob.bids:
            yes_book["best_bid"] = float(yes_ob.bids[0].price)
            yes_book["bid_depth"] = sum(float(b.size) for b in yes_ob.bids[:3])
        if yes_ob.asks:
            yes_book["best_ask"] = float(yes_ob.asks[0].price)
            yes_book["ask_depth"] = sum(float(a.size) for a in yes_ob.asks[:3])
    except Exception as e:
        print(f"  YES orderbook error: {e}")

    try:
        no_ob = client.get_order_book(no_token)
        if no_ob.bids:
            no_book["best_bid"] = float(no_ob.bids[0].price)
            no_book["bid_depth"] = sum(float(b.size) for b in no_ob.bids[:3])
        if no_ob.asks:
            no_book["best_ask"] = float(no_ob.asks[0].price)
            no_book["ask_depth"] = sum(float(a.size) for a in no_ob.asks[:3])
    except Exception as e:
        print(f"  NO orderbook error: {e}")

    return yes_book, no_book


def calculate_optimal_prices(yes_book: dict, no_book: dict, event_yes_price: float, event_no_price: float) -> Tuple[float, float]:
    """
    Calculate optimal bid prices based on orderbook analysis.

    Strategy:
    - Bid just above the best bid to be first in queue
    - Ensure YES_bid + NO_bid < $1.00 for profit margin
    - Use event prices as fallback if orderbook is empty
    """
    # Use orderbook best bids, fallback to event prices
    yes_best_bid = yes_book["best_bid"] if yes_book["best_bid"] > 0 else event_yes_price - 0.02
    no_best_bid = no_book["best_bid"] if no_book["best_bid"] > 0 else event_no_price - 0.02

    # Bid 1 cent above best bid to be first in queue (more likely to fill)
    yes_bid = round(yes_best_bid + 0.01, 2)
    no_bid = round(no_best_bid + 0.01, 2)

    # Ensure we don't exceed the current market price (would be instant fill but not maker)
    yes_bid = min(yes_bid, event_yes_price - 0.01)
    no_bid = min(no_bid, event_no_price - 0.01)

    # Ensure minimum prices
    yes_bid = max(0.01, yes_bid)
    no_bid = max(0.01, no_bid)

    # CRITICAL: Ensure combined bids < $1.00 for guaranteed profit
    # Target: YES + NO <= $0.97 for at least $0.03 profit
    max_combined = 0.97
    if yes_bid + no_bid > max_combined:
        # Scale down proportionally
        total = yes_bid + no_bid
        scale = max_combined / total
        yes_bid = round(yes_bid * scale, 2)
        no_bid = round(no_bid * scale, 2)

    # Final clamp
    yes_bid = max(0.01, min(0.99, yes_bid))
    no_bid = max(0.01, min(0.99, no_bid))

    return yes_bid, no_bid


def place_order(client: ClobClient, token_id: str, price: float, size: float, name: str) -> Optional[str]:
    """Place a BUY order with neg_risk=True."""
    try:
        print(f"\n  Placing {name} order: {size} @ ${price:.2f}")
        print(f"    Token: {token_id[:40]}...")

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        options = PartialCreateOrderOptions(neg_risk=True)

        print(f"    Creating signed order...")
        signed = client.create_order(order_args, options)
        print(f"    Signed order created, posting...")

        resp = client.post_order(signed, OrderType.GTC)
        print(f"    Response: {resp}")

        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            print(f"    ✅ Order placed: {order_id}")
            return order_id
        else:
            # Check for error
            error = resp.get("error") or resp.get("message") or resp
            print(f"    ❌ Order rejected: {error}")
            return None

    except Exception as e:
        print(f"    ❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 60)
    print("BTC HOURLY MARKET MAKER - DIRECT TRADING")
    print("=" * 60)

    # Get current hour slug
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

    yes_token, no_token, question, yes_mkt_price, no_mkt_price = result
    print(f"\nMarket: {question}")
    print(f"Event prices: YES=${yes_mkt_price:.3f}, NO=${no_mkt_price:.3f}")

    # Connect to Polymarket
    print("\n" + "-" * 60)
    print("CONNECTING TO POLYMARKET")
    print("-" * 60)

    try:
        client = create_client()
        print("✅ Connected!")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        print("\nPlease run: python scripts/diagnose_wallet.py")
        print("to diagnose wallet configuration issues.")
        return

    # Test authentication with a simple request
    print("\nTesting authentication...")
    try:
        orders = client.get_orders()
        print(f"✅ Auth OK - {len(orders) if orders else 0} existing orders")
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        print("\nPlease run: python scripts/diagnose_wallet.py")
        return

    # Fetch orderbook
    print("\n" + "-" * 60)
    print("ANALYZING ORDERBOOK")
    print("-" * 60)

    yes_book, no_book = get_orderbook_prices(client, yes_token, no_token)

    print(f"\nYES token orderbook:")
    print(f"  Best bid: ${yes_book['best_bid']:.2f} (depth: {yes_book['bid_depth']:.1f})")
    print(f"  Best ask: ${yes_book['best_ask']:.2f} (depth: {yes_book['ask_depth']:.1f})")

    print(f"\nNO token orderbook:")
    print(f"  Best bid: ${no_book['best_bid']:.2f} (depth: {no_book['bid_depth']:.1f})")
    print(f"  Best ask: ${no_book['best_ask']:.2f} (depth: {no_book['ask_depth']:.1f})")

    # Calculate optimal prices
    yes_bid_price, no_bid_price = calculate_optimal_prices(
        yes_book, no_book, yes_mkt_price, no_mkt_price
    )

    ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
    potential_profit = 1.0 - yes_bid_price - no_bid_price

    print(f"\n" + "=" * 60)
    print("ORDER PLAN")
    print("=" * 60)
    print(f"YES BUY: {ORDER_SIZE} @ ${yes_bid_price:.2f}")
    print(f"NO BUY:  {ORDER_SIZE} @ ${no_bid_price:.2f}")
    print(f"Total cost if both fill: ${yes_bid_price + no_bid_price:.2f}")
    print(f"Guaranteed profit if both fill: ${potential_profit:.2f} ({potential_profit*100:.1f}%)")

    print(f"\n" + "=" * 60)
    print("PLACING ORDERS")
    print("=" * 60)

    yes_order_id = place_order(client, yes_token, yes_bid_price, ORDER_SIZE, "YES")
    no_order_id = place_order(client, no_token, no_bid_price, ORDER_SIZE, "NO")

    if yes_order_id or no_order_id:
        print(f"\n{'='*60}")
        print("ORDERS LIVE!")
        print(f"{'='*60}")
        print("Monitoring orders... Press Ctrl+C to cancel and exit")

        try:
            while True:
                time.sleep(10)

                # Check order status
                try:
                    orders = client.get_orders()
                    active = [o for o in (orders or []) if o.get("status") == "live"]
                    filled = [o for o in (orders or []) if o.get("status") == "filled"]

                    timestamp = datetime.now().strftime('%H:%M:%S')
                    print(f"[{timestamp}] Active: {len(active)}, Filled: {len(filled)}")

                    # If no active orders, we're done
                    if not active and (yes_order_id or no_order_id):
                        print("\nNo more active orders!")
                        break
                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Status check error: {e}")

        except KeyboardInterrupt:
            print("\n\nCancelling orders...")
            if yes_order_id:
                try:
                    client.cancel(yes_order_id)
                    print(f"Cancelled YES: {yes_order_id[:30]}...")
                except Exception as e:
                    print(f"Cancel YES failed: {e}")
            if no_order_id:
                try:
                    client.cancel(no_order_id)
                    print(f"Cancelled NO: {no_order_id[:30]}...")
                except Exception as e:
                    print(f"Cancel NO failed: {e}")
            print("Done!")
    else:
        print("\n❌ No orders were placed successfully.")
        print("\nTroubleshooting:")
        print("1. Run: python scripts/diagnose_wallet.py")
        print("2. Verify your POLY_PRIVATE_KEY matches your MetaMask")
        print("3. Verify your POLY_SAFE_ADDRESS is your Polymarket deposit address")
        print("4. Make sure you have USDC balance on Polymarket")


if __name__ == "__main__":
    main()
