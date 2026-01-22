#!/usr/bin/env python3
"""
Test order v2 - Try alternative approaches for proxy wallet trading.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import py_clob_client
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY


def get_token_id():
    """Get a valid token ID from current BTC hourly market."""
    import httpx
    import json
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
    print(f"Fetching market: {slug}")

    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=30) as http:
        resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
        events = resp.json()

    if not events:
        print("No market found for current hour, trying next hour...")
        from datetime import timedelta
        next_hour = now + timedelta(hours=1)
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
        resp = http.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
        events = resp.json()

    if not events:
        return None, None

    event = events[0]
    print(f"Found: {event.get('title')}")

    for market in event.get("markets", []):
        if market.get("closed"):
            continue
        tokens = market.get("clobTokenIds", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if len(tokens) >= 2:
            return tokens[0], tokens[1]  # YES, NO tokens

    return None, None


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    # Ensure 0x prefix
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 60)
    print("POLYMARKET ORDER TEST V2")
    print("=" * 60)
    print(f"py-clob-client version: {py_clob_client.__version__ if hasattr(py_clob_client, '__version__') else 'unknown'}")
    print(f"Private key: {private_key[:10]}...")
    print(f"Safe address: {safe_address}")

    # Derive EOA address from private key to check configuration
    try:
        from eth_account import Account
        account = Account.from_key(private_key)
        eoa_address = account.address
        print(f"EOA address (from key): {eoa_address}")

        if safe_address.lower() == eoa_address.lower():
            print("\n⚠️  WARNING: POLY_SAFE_ADDRESS equals your EOA address!")
            print("   This is WRONG. Your Safe address should be different.")
            print("   Get your Safe address from polymarket.com -> Profile -> Deposit")
            print("   Update .env with the correct POLY_SAFE_ADDRESS")
            print("\n   Expected Safe: 0x731ea493985381ef0e79ec5c7c53d472a7e40e54")
            return
    except ImportError:
        print("eth_account not installed, skipping EOA check")

    yes_token, no_token = get_token_id()
    if not yes_token:
        print("❌ Could not get token ID!")
        return

    print(f"\nYES token: {yes_token[:40]}...")
    print(f"NO token: {no_token[:40]}...")

    # =========================================
    # APPROACH: Try trading a NON neg_risk market first
    # to verify the signature mechanism works
    # =========================================
    print("\n" + "=" * 60)
    print("TESTING: Find a regular (non-neg_risk) market")
    print("=" * 60)

    import httpx
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    # Find a market that's NOT neg_risk
    with httpx.Client(headers=headers, timeout=30) as http:
        # Get markets from CLOB
        resp = http.get("https://clob.polymarket.com/markets")
        if resp.status_code == 200:
            data = resp.json()
            # Handle different response formats
            if isinstance(data, dict):
                markets = data.get("data", []) or data.get("markets", []) or []
            elif isinstance(data, list):
                markets = data
            else:
                markets = []

            # Find first non-neg_risk market
            regular_token = None
            for m in markets[:50]:
                if isinstance(m, dict) and not m.get("neg_risk", False):
                    tokens = m.get("tokens", [])
                    if tokens:
                        regular_token = tokens[0].get("token_id")
                        print(f"Found regular market: {m.get('question', '')[:50]}...")
                        break

            if regular_token:
                print(f"Regular token: {regular_token[:40]}...")

                # Test with sig_type=2 on regular market
                print("\nTesting sig_type=2 on regular (non-neg_risk) market...")
                try:
                    client = ClobClient(
                        "https://clob.polymarket.com",
                        key=private_key,
                        chain_id=137,
                        signature_type=2,
                        funder=safe_address,
                    )
                    creds = client.derive_api_key()
                    client.set_api_creds(creds)

                    order_args = OrderArgs(
                        token_id=regular_token,
                        price=0.01,  # Very low price
                        size=1.0,
                        side=BUY,
                    )
                    signed = client.create_order(order_args)
                    resp = client.post_order(signed, OrderType.GTC)
                    print(f"Response: {resp}")

                    order_id = resp.get("orderID") or resp.get("id")
                    if order_id:
                        print(f"✅ SUCCESS on regular market!")
                        client.cancel(order_id)
                    else:
                        print(f"❌ Failed: {resp}")

                except Exception as e:
                    print(f"❌ Exception: {e}")
            else:
                print("No regular market found")
        else:
            print(f"Could not fetch markets: {resp.status_code}")

    # =========================================
    # Check if we need to use different API setup
    # =========================================
    print("\n" + "=" * 60)
    print("CHECKING: Account and allowance status")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=2,
            funder=safe_address,
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)
        print(f"API Key: {creds.api_key[:20]}...")
        print(f"API Secret: {creds.api_secret[:20]}...")

        # Check if we can get orders (tests authentication)
        orders = client.get_orders()
        print(f"✅ Auth works - can get orders: {len(orders) if orders else 0}")

        # Try to get balance info
        try:
            # Get the address being used
            print(f"\nClient address being used for signing...")

            # Try to inspect the client's internal state
            if hasattr(client, 'signer'):
                print(f"Signer: {client.signer}")
            if hasattr(client, 'funder'):
                print(f"Funder: {client.funder}")

        except Exception as e:
            print(f"Could not inspect client: {e}")

    except Exception as e:
        print(f"Client setup failed: {e}")

    # =========================================
    # APPROACH: Try with explicit tick_size
    # =========================================
    print("\n" + "=" * 60)
    print("TESTING: With explicit tick_size parameter")
    print("=" * 60)

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=2,
            funder=safe_address,
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)

        # Get market info to find tick_size
        with httpx.Client(headers=headers, timeout=30) as http:
            resp = http.get(f"https://clob.polymarket.com/markets/{yes_token}")
            if resp.status_code == 200:
                market_info = resp.json()
                tick_size = market_info.get("minimum_tick_size", "0.01")
                neg_risk = market_info.get("neg_risk", False)
                print(f"Market tick_size: {tick_size}")
                print(f"Market neg_risk: {neg_risk}")
                print(f"Market condition_id: {market_info.get('condition_id', 'N/A')}")

        # Try order with explicit parameters
        order_args = OrderArgs(
            token_id=yes_token,
            price=0.40,
            size=1.0,
            side=BUY,
        )

        # For neg_risk markets, use PartialCreateOrderOptions
        options = PartialCreateOrderOptions(neg_risk=True)

        print("\nCreating order with neg_risk=True...")
        signed = client.create_order(order_args, options)

        print(f"Signed order: {type(signed)}")
        if hasattr(signed, '__dict__'):
            for k, v in signed.__dict__.items():
                if k == 'signature':
                    print(f"  {k}: {str(v)[:50]}...")
                else:
                    print(f"  {k}: {v}")

        print("\nPosting order...")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Response: {resp}")

    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("""
The 'invalid signature' error on neg_risk markets with signature_type=2
suggests there may be:

1. A version mismatch in py-clob-client
2. A registration issue with the proxy wallet
3. An issue with how the library handles neg_risk signing

Try these steps:
1. Update py-clob-client: pip install --upgrade py-clob-client
2. Make another manual trade on polymarket.com (try a BTC hourly market)
3. Check if there's a newer version of the library

Current workaround options:
- Trade non-neg_risk markets (if sig_type=2 works on those)
- Fund your EOA wallet directly and use sig_type=0 (not recommended)
""")


if __name__ == "__main__":
    main()
