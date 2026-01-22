#!/usr/bin/env python3
"""
Minimal order test - diagnose signature issues.
Tries multiple approaches to find what works.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY


def test_approach(name: str, client: ClobClient, token_id: str, use_neg_risk: bool):
    """Test placing an order with given settings."""
    print(f"\n{'='*50}")
    print(f"Testing: {name}")
    print(f"{'='*50}")

    try:
        # Small test order
        order_args = OrderArgs(
            token_id=token_id,
            price=0.40,  # Low price, unlikely to fill
            size=1.0,    # Minimum size
            side=BUY,
        )

        if use_neg_risk:
            options = PartialCreateOrderOptions(neg_risk=True)
            signed = client.create_order(order_args, options)
        else:
            signed = client.create_order(order_args)

        print(f"  Signed order created")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"  Response: {resp}")

        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            print(f"  ✅ SUCCESS! Order ID: {order_id}")
            # Cancel it immediately
            try:
                client.cancel(order_id)
                print(f"  Cancelled test order")
            except:
                pass
            return True
        else:
            print(f"  ❌ Failed: {resp}")
            return False

    except Exception as e:
        print(f"  ❌ Exception: {e}")
        return False


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    # Add 0x prefix if missing
    if not private_key.startswith("0x"):
        private_key_with_prefix = f"0x{private_key}"
    else:
        private_key_with_prefix = private_key

    print("=" * 60)
    print("POLYMARKET ORDER TEST")
    print("=" * 60)
    print(f"Private key: {private_key[:8]}...")
    print(f"Safe address: {safe_address}")

    # Get a valid token ID from the current BTC hourly market
    import httpx
    from datetime import datetime
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
    print(f"\nFetching market: {slug}")

    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=30) as http:
        resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
        events = resp.json()

    if not events:
        print("❌ No market found!")
        return

    event = events[0]
    print(f"Found: {event.get('title')}")

    # Get token ID
    import json
    for market in event.get("markets", []):
        if market.get("closed"):
            continue
        tokens = market.get("clobTokenIds", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if tokens:
            token_id = tokens[0]  # YES token
            print(f"Using YES token: {token_id[:40]}...")
            break
    else:
        print("❌ No valid token found!")
        return

    # =========================================
    # TEST 1: Signature Type 0 (EOA direct)
    # =========================================
    print("\n" + "=" * 60)
    print("APPROACH 1: Signature Type 0 (EOA direct signing)")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key_with_prefix,
            chain_id=137,
            signature_type=0,  # Direct EOA
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)
        print("Client created with sig_type=0")

        # Test with neg_risk=False
        test_approach("sig_type=0, neg_risk=False", client, token_id, use_neg_risk=False)

        # Test with neg_risk=True
        test_approach("sig_type=0, neg_risk=True", client, token_id, use_neg_risk=True)

    except Exception as e:
        print(f"Approach 1 setup failed: {e}")

    # =========================================
    # TEST 2: Signature Type 1 (Poly GNOSIS SAFE)
    # =========================================
    print("\n" + "=" * 60)
    print("APPROACH 2: Signature Type 1 (Poly GNOSIS SAFE)")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key_with_prefix,
            chain_id=137,
            signature_type=1,
            funder=safe_address,
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)
        print("Client created with sig_type=1")

        test_approach("sig_type=1, neg_risk=False", client, token_id, use_neg_risk=False)
        test_approach("sig_type=1, neg_risk=True", client, token_id, use_neg_risk=True)

    except Exception as e:
        print(f"Approach 2 setup failed: {e}")

    # =========================================
    # TEST 3: Signature Type 2 (Poly Proxy) - Current approach
    # =========================================
    print("\n" + "=" * 60)
    print("APPROACH 3: Signature Type 2 (Poly Proxy)")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key_with_prefix,
            chain_id=137,
            signature_type=2,
            funder=safe_address,
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)
        print("Client created with sig_type=2")

        test_approach("sig_type=2, neg_risk=False", client, token_id, use_neg_risk=False)
        test_approach("sig_type=2, neg_risk=True", client, token_id, use_neg_risk=True)

    except Exception as e:
        print(f"Approach 3 setup failed: {e}")

    # =========================================
    # TEST 4: Try create_or_derive_api_creds
    # =========================================
    print("\n" + "=" * 60)
    print("APPROACH 4: Using create_or_derive_api_creds()")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key_with_prefix,
            chain_id=137,
            signature_type=2,
            funder=safe_address,
        )
        # Use the combined method
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print("Client created with create_or_derive_api_creds()")

        test_approach("create_or_derive + neg_risk=True", client, token_id, use_neg_risk=True)

    except Exception as e:
        print(f"Approach 4 setup failed: {e}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    print("""
If ALL approaches failed with 'invalid signature':
1. Have you made at least ONE trade on polymarket.com web UI?
   → This initializes your proxy wallet and approvals

2. Try going to polymarket.com, connect your MetaMask, and place
   a small $1 order manually. Then run this script again.

3. Check that your MetaMask is connected to the same wallet
   that owns the Polymarket Safe address.
""")


if __name__ == "__main__":
    main()
