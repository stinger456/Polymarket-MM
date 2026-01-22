#!/usr/bin/env python3
"""
BTC Hourly Market Maker - CORRECTED VERSION

Key insight from debugging:
- BTC hourly UP/DOWN markets use neg_risk=FALSE (regular exchange contract)
- Previous attempts failed because we were using neg_risk=TRUE (wrong contract)

The py-clob-client will automatically detect the correct neg_risk setting
from the API, so we just let it do its thing and not override.
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137


def get_btc_hourly_market():
    """Get current BTC hourly market tokens."""
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)

    for offset in [0, 1]:  # Try current hour, then next
        dt = now + timedelta(hours=offset)
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

        slug = f"bitcoin-up-or-down-{month}-{day}-{h}-et"
        print(f"Trying: {slug}")

        try:
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            with httpx.Client(headers=headers, timeout=30) as http:
                resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                events = resp.json()

                if events:
                    event = events[0]
                    print(f"Found: {event.get('title')}")

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
                            return {
                                "yes_token": tokens[0],
                                "no_token": tokens[1],
                                "yes_price": float(prices[0]) if prices else 0.5,
                                "no_price": float(prices[1]) if len(prices) > 1 else 0.5,
                                "question": market.get("question", ""),
                            }
        except Exception as e:
            print(f"Error: {e}")

    return None


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 70)
    print("BTC HOURLY MARKET MAKER - CORRECTED VERSION")
    print("=" * 70)
    print(f"Funder (Safe): {safe_address}")
    print(f"Signature Type: 2 (POLY_GNOSIS_SAFE)")

    # Get market
    print("\n" + "-" * 70)
    print("FINDING MARKET")
    print("-" * 70)

    market = get_btc_hourly_market()
    if not market:
        print("No BTC hourly market found!")
        return

    print(f"\nMarket: {market['question']}")
    print(f"YES Token: {market['yes_token'][:40]}...")
    print(f"NO Token: {market['no_token'][:40]}...")
    print(f"Prices: YES=${market['yes_price']:.3f}, NO=${market['no_price']:.3f}")

    # Connect
    print("\n" + "-" * 70)
    print("CONNECTING")
    print("-" * 70)

    try:
        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=2,  # POLY_GNOSIS_SAFE
            funder=safe_address,
        )

        # Get API credentials
        try:
            creds = client.derive_api_key()
        except:
            creds = client.create_api_key()
        client.set_api_creds(creds)
        print(f"API Key: {creds.api_key[:20]}...")

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return

    # Query API for actual market parameters
    print("\n" + "-" * 70)
    print("FETCHING MARKET PARAMETERS FROM API")
    print("-" * 70)

    try:
        # The library's get_neg_risk will tell us the ACTUAL setting
        api_neg_risk = client.get_neg_risk(market["yes_token"])
        print(f"API reports neg_risk: {api_neg_risk}")
    except Exception as e:
        print(f"get_neg_risk failed: {e} - will let library auto-detect")
        api_neg_risk = None

    try:
        tick_size = client.get_tick_size(market["yes_token"])
        print(f"API reports tick_size: {tick_size}")
    except Exception as e:
        print(f"get_tick_size failed: {e}")
        tick_size = None

    # Calculate bid prices - 2 cents below market price
    yes_bid = round(market["yes_price"] - 0.02, 2)
    no_bid = round(market["no_price"] - 0.02, 2)

    # Ensure combined < 1.00 for profit
    if yes_bid + no_bid > 0.96:
        yes_bid = 0.47
        no_bid = 0.47

    yes_bid = max(0.01, yes_bid)
    no_bid = max(0.01, no_bid)

    size = float(os.getenv("BASE_ORDER_SIZE", "5"))

    print(f"\n" + "=" * 70)
    print("ORDER PLAN")
    print("=" * 70)
    print(f"YES BUY: {size} @ ${yes_bid:.2f}")
    print(f"NO BUY:  {size} @ ${no_bid:.2f}")
    print(f"Total cost: ${yes_bid + no_bid:.2f}")
    print(f"Profit if both fill: ${1 - yes_bid - no_bid:.2f} ({(1 - yes_bid - no_bid)*100:.1f}%)")

    print(f"\n" + "=" * 70)
    print("PLACING ORDERS")
    print("=" * 70)

    # KEY FIX: Don't specify neg_risk - let the library auto-detect from API
    # Or explicitly use what the API reports
    yes_order_id = None
    no_order_id = None

    for token_id, price, name in [
        (market["yes_token"], yes_bid, "YES"),
        (market["no_token"], no_bid, "NO"),
    ]:
        print(f"\n--- Placing {name} order ---")
        print(f"  Token: {token_id[:40]}...")
        print(f"  Price: ${price:.2f}")
        print(f"  Size: {size}")

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
            )

            # KEY: Either don't pass options (let library auto-detect)
            # Or pass only tick_size if known, let neg_risk auto-detect
            options = None
            if tick_size:
                options = PartialCreateOrderOptions(tick_size=tick_size)

            print(f"  Creating order (neg_risk auto-detect)...")
            signed_order = client.create_order(order_args, options)

            print(f"  Posting order...")
            response = client.post_order(signed_order, OrderType.GTC)

            order_id = response.get("orderID") or response.get("id")
            if order_id:
                print(f"  ✅ SUCCESS! Order ID: {order_id}")
                if name == "YES":
                    yes_order_id = order_id
                else:
                    no_order_id = order_id
            else:
                error = response.get("error") or response.get("message") or response
                print(f"  ❌ Failed: {error}")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback
            traceback.print_exc()

    # Monitor orders
    if yes_order_id or no_order_id:
        print(f"\n" + "=" * 70)
        print("ORDERS LIVE!")
        print("=" * 70)
        print("Monitoring... Press Ctrl+C to cancel and exit")

        try:
            while True:
                time.sleep(10)
                try:
                    orders = client.get_orders()
                    active = [o for o in (orders or []) if o.get("status") == "live"]
                    filled = [o for o in (orders or []) if o.get("status") == "filled"]

                    ts = datetime.now().strftime('%H:%M:%S')
                    print(f"[{ts}] Active: {len(active)}, Filled: {len(filled)}")

                    if not active:
                        print("\nNo more active orders!")
                        break
                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Status check: {e}")

        except KeyboardInterrupt:
            print("\n\nCancelling orders...")
            for oid, name in [(yes_order_id, "YES"), (no_order_id, "NO")]:
                if oid:
                    try:
                        client.cancel(oid)
                        print(f"Cancelled {name}: {oid[:30]}...")
                    except Exception as e:
                        print(f"Cancel {name} failed: {e}")
    else:
        print("\n❌ No orders placed.")
        print("\nTroubleshooting:")
        print("1. Check your USDC balance on Polymarket")
        print("2. Verify credentials in .env file")
        print("3. Try with a smaller order size")


if __name__ == "__main__":
    main()
