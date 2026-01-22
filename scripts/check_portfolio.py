#!/usr/bin/env python3
"""
Check Polymarket portfolio - positions, orders, and balances.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from py_clob_client.client import ClobClient

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 70)
    print("POLYMARKET PORTFOLIO CHECK")
    print("=" * 70)
    print(f"Safe Address: {safe_address}")

    # Connect
    client = ClobClient(
        CLOB_HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=2,
        funder=safe_address,
    )

    try:
        creds = client.derive_api_key()
    except:
        creds = client.create_api_key()
    client.set_api_creds(creds)
    print("Connected!\n")

    # Check orders
    print("-" * 70)
    print("OPEN ORDERS")
    print("-" * 70)
    try:
        orders = client.get_orders()
        if orders:
            live_orders = [o for o in orders if o.get("status") == "live"]
            if live_orders:
                for o in live_orders:
                    side = "BUY" if o.get("side") == "BUY" else "SELL"
                    print(f"  {side} {o.get('original_size')} @ ${float(o.get('price', 0)):.2f}")
                    print(f"    Token: {o.get('asset_id', '')[:40]}...")
                    print(f"    Order ID: {o.get('id', '')[:40]}...")
                    print(f"    Filled: {o.get('size_matched', 0)}")
                    print()
            else:
                print("  No open orders")
        else:
            print("  No orders found")
    except Exception as e:
        print(f"  Error fetching orders: {e}")

    # Check trades/fills
    print("-" * 70)
    print("RECENT TRADES")
    print("-" * 70)
    try:
        trades = client.get_trades()
        if trades:
            for t in trades[:10]:  # Last 10 trades
                side = t.get("side", "?")
                size = t.get("size", 0)
                price = float(t.get("price", 0))
                status = t.get("status", "?")
                print(f"  {side} {size} @ ${price:.2f} - {status}")
                print(f"    Match time: {t.get('match_time', 'N/A')}")
                print()
        else:
            print("  No recent trades")
    except Exception as e:
        print(f"  Error fetching trades: {e}")

    # Check balances (if available)
    print("-" * 70)
    print("BALANCE INFO")
    print("-" * 70)
    try:
        # The balance check depends on what the API exposes
        # Try to get collateral balance
        balance = client.get_balance_allowance()
        if balance:
            print(f"  Balance info: {json.dumps(balance, indent=2)}")
    except Exception as e:
        print(f"  Balance check: {e}")
        print("  (Check polymarket.com/portfolio for full balance)")

    print("\n" + "=" * 70)
    print("TIP: Visit https://polymarket.com/portfolio for full details")
    print("=" * 70)


if __name__ == "__main__":
    main()
