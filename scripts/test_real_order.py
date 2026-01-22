#!/usr/bin/env python3
"""Test placing a real order on the current active BTC market."""

import os
import sys
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

async def get_current_btc_market():
    """Get the current active BTC hourly market."""
    import httpx

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Get current BTC events
        resp = await client.get(
            "https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "limit": 100}
        )
        events = resp.json()

        for event in events:
            title = event.get("title", "").lower()
            if "bitcoin" in title and ("up" in title or "down" in title):
                markets = event.get("markets", [])
                print(f"Found event: {event.get('title')}")
                print(f"Markets: {len(markets)}")

                for m in markets:
                    if not m.get("closed"):
                        import json
                        clob_tokens = m.get("clobTokenIds", [])
                        if isinstance(clob_tokens, str):
                            clob_tokens = json.loads(clob_tokens)

                        if len(clob_tokens) >= 2:
                            return {
                                "question": m.get("question"),
                                "yes_token": clob_tokens[0],
                                "no_token": clob_tokens[1],
                                "end_date": m.get("endDate"),
                            }
    return None

def main():
    private_key = os.getenv("POLY_PRIVATE_KEY")
    safe_address = os.getenv("POLY_SAFE_ADDRESS")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    print("=" * 60)
    print("POLYMARKET ORDER TEST")
    print("=" * 60)
    print(f"Private Key: {private_key[:10]}...{private_key[-4:]}")
    print(f"Safe Address: {safe_address}")
    print(f"Signature Type: {sig_type}")
    print()

    # Get current market
    print("Finding current BTC market...")
    market = asyncio.run(get_current_btc_market())

    if not market:
        print("ERROR: No active BTC market found!")
        return

    print(f"Market: {market['question']}")
    print(f"YES token: {market['yes_token'][:40]}...")
    print(f"NO token: {market['no_token'][:40]}...")
    print(f"End date: {market['end_date']}")
    print()

    # Create client
    print("Creating CLOB client...")
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
        client.set_api_creds(creds)
        print("API credentials set!")
    except Exception as e:
        print(f"ERROR deriving credentials: {e}")
        return

    # Try to place a small BUY order on YES token at very low price
    print()
    print("=" * 60)
    print("ATTEMPTING TO PLACE ORDER")
    print("=" * 60)
    print(f"Token: YES ({market['yes_token'][:30]}...)")
    print(f"Side: BUY")
    print(f"Price: 0.01 (very low - won't fill)")
    print(f"Size: 1.0")
    print()

    try:
        order = OrderArgs(
            token_id=market['yes_token'],
            price=0.01,  # Very low price
            size=1.0,    # Minimum size
            side=BUY,
        )

        print("Creating signed order...")
        signed = client.create_order(order)
        print(f"Order signed! Salt: {signed.salt[:20] if hasattr(signed, 'salt') else 'N/A'}...")

        print("Posting order...")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"SUCCESS! Response: {resp}")

        # Cancel it immediately
        order_id = resp.get("orderID") or resp.get("id")
        if order_id:
            print(f"Cancelling order {order_id}...")
            client.cancel(order_id)
            print("Order cancelled!")

    except Exception as e:
        print(f"ERROR: {e}")
        print(f"Error type: {type(e).__name__}")

        # Try to get more details
        if hasattr(e, 'response'):
            print(f"Response: {e.response}")
        if hasattr(e, 'args'):
            print(f"Args: {e.args}")

if __name__ == "__main__":
    main()
