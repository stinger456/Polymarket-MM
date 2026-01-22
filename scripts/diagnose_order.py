#!/usr/bin/env python3
"""
Comprehensive diagnostic for Polymarket order placement.
This will identify exactly why orders are failing.
"""

import os
import sys
import json
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

def main():
    print("=" * 70)
    print("POLYMARKET ORDER DIAGNOSTIC")
    print("=" * 70)

    # 1. Check environment variables
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    print("\n1. CONFIGURATION:")
    print(f"   Private Key: {private_key[:8]}...{private_key[-4:]} ({len(private_key)} chars)")
    print(f"   Safe Address: {safe_address}")
    print(f"   Signature Type: {sig_type}")

    # Check private key format
    if private_key.startswith("0x"):
        print("   ⚠️  Private key has 0x prefix - removing it")
        private_key = private_key[2:]

    if len(private_key) != 64:
        print(f"   ❌ Private key should be 64 hex chars, got {len(private_key)}")
        return
    else:
        print("   ✅ Private key length OK")

    # Check safe address format
    if not safe_address.startswith("0x"):
        print("   ❌ Safe address should start with 0x")
        return
    else:
        print("   ✅ Safe address format OK")

    # 2. Create client
    print("\n2. CREATING CLIENT:")
    from py_clob_client.client import ClobClient

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=sig_type,
            funder=safe_address,
        )
        print("   ✅ Client created")
    except Exception as e:
        print(f"   ❌ Client creation failed: {e}")
        return

    # 3. Create API credentials
    print("\n3. API CREDENTIALS:")
    try:
        # Try to create fresh credentials
        creds = client.create_api_key()
        print(f"   ✅ Created fresh API key: {creds.api_key[:20]}...")
        client.set_api_creds(creds)
    except Exception as e:
        print(f"   ⚠️  create_api_key failed: {e}")
        try:
            creds = client.derive_api_key()
            print(f"   ✅ Derived API key: {creds.api_key[:20]}...")
            client.set_api_creds(creds)
        except Exception as e2:
            print(f"   ❌ derive_api_key also failed: {e2}")
            return

    # 4. Test basic API access
    print("\n4. TESTING API ACCESS:")
    try:
        orders = client.get_orders()
        print(f"   ✅ get_orders() works - {len(orders) if orders else 0} open orders")
    except Exception as e:
        print(f"   ❌ get_orders() failed: {e}")

    # 5. Get a real market token
    print("\n5. FINDING ACTIVE MARKET:")
    import httpx

    async def get_market():
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as http:
            resp = await http.get(
                "https://gamma-api.polymarket.com/events",
                params={"slug": "bitcoin-up-or-down-january-22-9am-et"}
            )
            if resp.status_code != 200:
                # Try 10am
                resp = await http.get(
                    "https://gamma-api.polymarket.com/events",
                    params={"slug": "bitcoin-up-or-down-january-22-10am-et"}
                )
            events = resp.json()
            if events:
                for m in events[0].get("markets", []):
                    if not m.get("closed"):
                        tokens = m.get("clobTokenIds", [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)
                        if len(tokens) >= 2:
                            return {
                                "question": m.get("question"),
                                "yes_token": tokens[0],
                                "no_token": tokens[1],
                            }
        return None

    market = asyncio.run(get_market())
    if not market:
        print("   ❌ No active market found")
        return

    print(f"   ✅ Found market: {market['question'][:50]}...")
    print(f"   YES token: {market['yes_token'][:40]}...")
    print(f"   NO token: {market['no_token'][:40]}...")

    # 6. Try to create and sign an order
    print("\n6. CREATING ORDER:")
    from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
    from py_clob_client.order_builder.constants import BUY

    order_args = OrderArgs(
        token_id=market['yes_token'],
        price=0.01,  # Very low price - won't fill
        size=1.0,
        side=BUY,
    )

    print(f"   Order: BUY {order_args.size} @ ${order_args.price}")
    print(f"   Token: {order_args.token_id[:40]}...")

    # Try WITHOUT neg_risk first
    print("\n7. TESTING ORDER SIGNING (without neg_risk):")
    try:
        signed = client.create_order(order_args)
        print(f"   ✅ Order signed without neg_risk")
        print(f"   Attempting to post...")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"   ✅ SUCCESS! Order placed: {resp}")
        # Cancel immediately
        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            client.cancel(order_id)
            print(f"   ✅ Order cancelled")
        return
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # Try WITH neg_risk
    print("\n8. TESTING ORDER SIGNING (with neg_risk=True):")
    try:
        options = PartialCreateOrderOptions(neg_risk=True)
        signed = client.create_order(order_args, options)
        print(f"   ✅ Order signed with neg_risk=True")
        print(f"   Attempting to post...")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"   ✅ SUCCESS! Order placed: {resp}")
        # Cancel immediately
        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            client.cancel(order_id)
            print(f"   ✅ Order cancelled")
        return
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # 9. Try different signature types
    print("\n9. TRYING DIFFERENT SIGNATURE TYPES:")
    for test_sig_type in [0, 1, 2]:
        if test_sig_type == sig_type:
            continue
        print(f"\n   Testing signature_type={test_sig_type}:")
        try:
            test_client = ClobClient(
                "https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=test_sig_type,
                funder=safe_address if test_sig_type == 2 else None,
            )
            test_creds = test_client.create_or_derive_api_creds()
            test_client.set_api_creds(test_creds)

            signed = test_client.create_order(order_args)
            resp = test_client.post_order(signed, OrderType.GTC)
            print(f"   ✅ SUCCESS with signature_type={test_sig_type}!")
            order_id = resp.get("orderID") or resp.get("id")
            if order_id:
                test_client.cancel(order_id)
            return
        except Exception as e:
            print(f"   ❌ Failed: {str(e)[:60]}...")

    print("\n" + "=" * 70)
    print("DIAGNOSIS COMPLETE - ALL SIGNATURE TYPES FAILED")
    print("=" * 70)
    print("""
Possible issues:
1. POLY_SAFE_ADDRESS doesn't match your Polymarket proxy wallet
   → Go to polymarket.com → Profile → Settings → Check 'Proxy Wallet Address'

2. POLY_PRIVATE_KEY is from a different wallet than your Polymarket account
   → The private key must be from the wallet you used to sign up for Polymarket

3. Your Polymarket account may need to approve trading
   → Try placing a manual trade on polymarket.com first
""")

if __name__ == "__main__":
    main()
