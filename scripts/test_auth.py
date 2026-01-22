#!/usr/bin/env python3
"""Test Polymarket authentication and order placement."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

def main():
    private_key = os.getenv("POLY_PRIVATE_KEY")
    safe_address = os.getenv("POLY_SAFE_ADDRESS")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    print(f"Private Key: {private_key[:10]}...{private_key[-4:]}")
    print(f"Safe Address: {safe_address}")
    print(f"Signature Type: {sig_type}")
    print()

    # Create client
    print("Creating client...")
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=sig_type,
        funder=safe_address,
    )

    # Derive API credentials
    print("Deriving API credentials...")
    try:
        creds = client.create_or_derive_api_creds()
        print(f"API Key: {creds.api_key[:20]}...")
        print(f"API Secret: {creds.api_secret[:20]}...")
        client.set_api_creds(creds)
        print("API credentials set successfully!")
    except Exception as e:
        print(f"ERROR deriving credentials: {e}")
        return

    # Check balance
    print("\nChecking balance...")
    try:
        # This is a simple test to see if auth works
        orders = client.get_orders()
        print(f"Open orders: {len(orders) if orders else 0}")
    except Exception as e:
        print(f"ERROR getting orders: {e}")

    # Try to place a tiny test order on a real market
    print("\nTrying to place a small test order...")

    # Use a real BTC market token ID (you can get this from the bot output)
    # This is just a test - we'll cancel immediately
    test_token = "71136155131899742255150873207222411754152584079034757820610453353511789727"

    try:
        order = OrderArgs(
            token_id=test_token,
            price=0.01,  # Very low price, won't fill
            size=1.0,    # Minimum size
            side=BUY,
        )

        signed = client.create_order(order)
        print(f"Order signed successfully!")

        resp = client.post_order(signed, OrderType.GTC)
        print(f"Order placed! Response: {resp}")

        # Cancel it immediately
        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            client.cancel(order_id)
            print(f"Order cancelled: {order_id}")

    except Exception as e:
        print(f"ERROR placing order: {e}")
        print(f"Error type: {type(e).__name__}")

if __name__ == "__main__":
    main()
