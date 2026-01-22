#!/usr/bin/env python3
"""
Trade BTC hourly market as a NON neg_risk market.

The key insight: if neg_risk=False orders get "invalid amount" (signature valid)
but neg_risk=True orders get "invalid signature", then the market is NOT neg_risk.

This script tries trading with neg_risk=False and adjusts amounts properly.
"""

import os
import sys
import json

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
    print("BTC HOURLY TRADER - NON NEG_RISK MODE")
    print("=" * 70)
    print(f"Signer: derived from private key")
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

    # Query API for market info
    print("\n" + "-" * 70)
    print("MARKET INFO FROM API")
    print("-" * 70)

    try:
        # Check neg_risk from API
        api_neg_risk = client.get_neg_risk(market["yes_token"])
        print(f"API neg_risk: {api_neg_risk}")
    except Exception as e:
        print(f"get_neg_risk failed: {e}")
        api_neg_risk = None

    try:
        tick_size = client.get_tick_size(market["yes_token"])
        print(f"API tick_size: {tick_size}")
    except Exception as e:
        print(f"get_tick_size failed: {e}")
        tick_size = "0.01"

    try:
        market_info = client.get_market(market["yes_token"])
        print(f"Market info: {json.dumps(market_info, indent=2)[:500]}...")
    except Exception as e:
        print(f"get_market failed: {e}")

    # Calculate bid prices
    yes_bid = round(market["yes_price"] - 0.02, 2)
    no_bid = round(market["no_price"] - 0.02, 2)

    # Ensure combined < 1.00
    if yes_bid + no_bid > 0.96:
        yes_bid = 0.47
        no_bid = 0.47

    yes_bid = max(0.01, yes_bid)
    no_bid = max(0.01, no_bid)

    size = 5.0

    print(f"\n" + "=" * 70)
    print("ORDER PLAN")
    print("=" * 70)
    print(f"YES BUY: {size} @ ${yes_bid:.2f}")
    print(f"NO BUY:  {size} @ ${no_bid:.2f}")
    print(f"Total: ${yes_bid + no_bid:.2f}")
    print(f"Profit if both fill: ${1 - yes_bid - no_bid:.2f}")

    # Test order creation with EXPLICIT neg_risk settings
    print(f"\n" + "=" * 70)
    print("TESTING ORDER CREATION")
    print("=" * 70)

    # Try with neg_risk=False (based on our analysis)
    for test_neg_risk in [False, True]:
        print(f"\n--- Testing neg_risk={test_neg_risk} ---")
        try:
            order_args = OrderArgs(
                token_id=market["yes_token"],
                price=yes_bid,
                size=size,
                side=BUY,
            )
            options = PartialCreateOrderOptions(
                neg_risk=test_neg_risk,
                tick_size=tick_size,
            )

            # Create the signed order (don't post yet)
            signed_order = client.create_order(order_args, options)
            print(f"✓ Order created with neg_risk={test_neg_risk}")
            print(f"  Order type: {type(signed_order)}")

            # Now try to POST
            print(f"  Posting order...")
            response = client.post_order(signed_order, OrderType.GTC)
            print(f"  Response: {response}")

            order_id = response.get("orderID") or response.get("id")
            if order_id:
                print(f"  ✅ SUCCESS! Order ID: {order_id}")
                # Cancel immediately since this is a test
                try:
                    client.cancel(order_id)
                    print(f"  Cancelled test order")
                except:
                    pass
            else:
                error = response.get("error") or response.get("message") or response
                print(f"  ❌ Failed: {error}")

        except Exception as e:
            print(f"  ❌ Error: {e}")


if __name__ == "__main__":
    main()
