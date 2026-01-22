#!/usr/bin/env python3
"""
Fixed trading script based on official Polymarket documentation.
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


def get_btc_hourly_market():
    """Get current BTC hourly market tokens and info."""
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

        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        with httpx.Client(headers=headers, timeout=30) as http:
            resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
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
                            "condition_id": market.get("conditionId", ""),
                        }

    return None


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    # Ensure 0x prefix for private key
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 60)
    print("POLYMARKET BTC HOURLY TRADER (Fixed)")
    print("=" * 60)
    print(f"Safe (funder): {safe_address}")
    print(f"Signature type: 2 (GNOSIS_SAFE)")

    # Get market
    market = get_btc_hourly_market()
    if not market:
        print("No BTC hourly market found!")
        return

    print(f"\nMarket: {market['question']}")
    print(f"YES price: ${market['yes_price']:.2f}")
    print(f"NO price: ${market['no_price']:.2f}")

    # Initialize client per documentation
    print("\nInitializing client...")
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=private_key,
        signature_type=2,  # GNOSIS_SAFE for browser wallet
        funder=safe_address,
    )

    # Create or derive API credentials
    print("Getting API credentials...")
    try:
        creds = client.create_or_derive_api_creds()
        print(f"API Key: {creds.api_key[:20]}...")
    except Exception as e:
        print(f"create_or_derive_api_creds failed: {e}")
        try:
            creds = client.derive_api_key()
            print(f"Derived API Key: {creds.api_key[:20]}...")
        except Exception as e2:
            print(f"derive_api_key also failed: {e2}")
            return

    client.set_api_creds(creds)

    # Get market info from CLOB to get tick_size
    print("\nFetching market info from CLOB...")
    try:
        clob_market = client.get_market(market["yes_token"])
        tick_size = clob_market.get("minimum_tick_size", "0.01")
        neg_risk = clob_market.get("neg_risk", True)
        print(f"Tick size: {tick_size}")
        print(f"Neg risk: {neg_risk}")
    except Exception as e:
        print(f"Could not get market info: {e}")
        tick_size = "0.01"
        neg_risk = True

    # Calculate prices
    yes_bid = round(market["yes_price"] - 0.02, 2)
    no_bid = round(market["no_price"] - 0.02, 2)

    # Ensure profit margin
    if yes_bid + no_bid > 0.96:
        yes_bid = 0.47
        no_bid = 0.47

    yes_bid = max(0.01, yes_bid)
    no_bid = max(0.01, no_bid)

    size = 5.0

    print(f"\n{'='*60}")
    print("ORDER PLAN")
    print(f"{'='*60}")
    print(f"YES BUY: {size} @ ${yes_bid:.2f}")
    print(f"NO BUY:  {size} @ ${no_bid:.2f}")
    print(f"Total: ${yes_bid + no_bid:.2f}")
    print(f"Profit if both fill: ${1 - yes_bid - no_bid:.2f}")

    # Place orders using the documented method
    print(f"\n{'='*60}")
    print("PLACING ORDERS")
    print(f"{'='*60}")

    for token_id, price, name in [(market["yes_token"], yes_bid, "YES"),
                                   (market["no_token"], no_bid, "NO")]:
        print(f"\nPlacing {name} order...")
        print(f"  Token: {token_id[:30]}...")
        print(f"  Price: ${price:.2f}")
        print(f"  Size: {size}")

        try:
            # Create order args
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
            )

            # Options with tick_size and neg_risk
            options = PartialCreateOrderOptions(
                neg_risk=neg_risk,
                # tick_size is sometimes needed
            )

            # Create signed order
            print("  Creating signed order...")
            signed_order = client.create_order(order_args, options)

            # Post order
            print("  Posting order...")
            response = client.post_order(signed_order, OrderType.GTC)

            order_id = response.get("orderID") or response.get("id")
            if order_id:
                print(f"  ✅ SUCCESS! Order ID: {order_id}")
            else:
                print(f"  ❌ Failed: {response}")

        except Exception as e:
            print(f"  ❌ Error: {e}")

            # Print more details about the error
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
