#!/usr/bin/env python3
"""Test basic connectivity to Polymarket APIs."""

import os
import sys
import json
import traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

def test_http_connection():
    """Test basic HTTP connection to Polymarket."""
    import httpx

    print("=" * 60)
    print("TESTING HTTP CONNECTIVITY")
    print("=" * 60)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    # Test 1: Gamma API
    print("\n1. Testing Gamma API...")
    try:
        with httpx.Client(headers=headers, timeout=30) as client:
            resp = client.get("https://gamma-api.polymarket.com/events?limit=1")
            print(f"   Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"   ✅ Gamma API reachable")
            else:
                print(f"   ❌ Gamma API returned {resp.status_code}")
                print(f"   Response: {resp.text[:200]}")
    except Exception as e:
        print(f"   ❌ Gamma API failed: {e}")

    # Test 2: CLOB API
    print("\n2. Testing CLOB API...")
    try:
        with httpx.Client(headers=headers, timeout=30) as client:
            resp = client.get("https://clob.polymarket.com/tick-size?token_id=test")
            print(f"   Status: {resp.status_code}")
            if resp.status_code in [200, 400, 404]:
                print(f"   ✅ CLOB API reachable")
            else:
                print(f"   ⚠️ CLOB API returned {resp.status_code}")
    except Exception as e:
        print(f"   ❌ CLOB API failed: {e}")

    # Test 3: py-clob-client directly
    print("\n3. Testing py-clob-client...")
    try:
        from py_clob_client.client import ClobClient

        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
        sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

        print(f"   Signature Type: {sig_type}")
        print(f"   Safe Address: {safe_address}")

        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=sig_type,
            funder=safe_address,
        )
        print(f"   ✅ Client created")

        # Try to create API credentials with detailed error
        print("\n4. Creating API credentials...")
        try:
            creds = client.create_api_key()
            print(f"   ✅ API Key: {creds.api_key[:30]}...")
            client.set_api_creds(creds)
        except Exception as e:
            print(f"   ⚠️ create_api_key failed: {e}")
            print(f"   Trying derive_api_key...")
            try:
                creds = client.derive_api_key()
                print(f"   ✅ Derived API Key: {creds.api_key[:30]}...")
                client.set_api_creds(creds)
            except Exception as e2:
                print(f"   ❌ derive_api_key failed: {e2}")
                traceback.print_exc()
                return

        # Test getting orders
        print("\n5. Testing authenticated endpoint...")
        try:
            orders = client.get_orders()
            print(f"   ✅ get_orders() works - {len(orders) if orders else 0} orders")
        except Exception as e:
            print(f"   ❌ get_orders() failed: {e}")

        # Find a market
        print("\n6. Finding active BTC market...")
        with httpx.Client(headers=headers, timeout=30) as http:
            resp = http.get(
                "https://gamma-api.polymarket.com/events",
                params={"active": "true", "closed": "false", "limit": 100}
            )
            events = resp.json()

            for event in events:
                title = event.get("title", "").lower()
                if "bitcoin" in title and ("up" in title or "down" in title):
                    for m in event.get("markets", []):
                        if not m.get("closed"):
                            tokens = m.get("clobTokenIds", [])
                            if isinstance(tokens, str):
                                tokens = json.loads(tokens)
                            if len(tokens) >= 2:
                                print(f"   Found: {m.get('question', '')[:50]}...")
                                print(f"   YES token: {tokens[0][:40]}...")

                                # Try placing an order
                                print("\n7. Attempting order placement...")
                                from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
                                from py_clob_client.order_builder.constants import BUY

                                order_args = OrderArgs(
                                    token_id=tokens[0],
                                    price=0.01,
                                    size=1.0,
                                    side=BUY,
                                )

                                # Try with neg_risk=True (required for BTC markets)
                                try:
                                    options = PartialCreateOrderOptions(neg_risk=True)
                                    signed = client.create_order(order_args, options)
                                    print(f"   ✅ Order signed")

                                    resp = client.post_order(signed, OrderType.GTC)
                                    print(f"   ✅ ORDER PLACED! {resp}")

                                    # Cancel immediately
                                    order_id = resp.get("orderID") or resp.get("id")
                                    if order_id:
                                        client.cancel(order_id)
                                        print(f"   ✅ Cancelled")
                                    return
                                except Exception as e:
                                    print(f"   ❌ Order failed: {e}")
                                    traceback.print_exc()
                                return

        print("   ❌ No active BTC market found")

    except Exception as e:
        print(f"   ❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_http_connection()
