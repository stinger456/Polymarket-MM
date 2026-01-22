#!/usr/bin/env python3
"""
Market Maker - Uses CLOB API directly for valid token IDs
Filters for ACTIVE markets with existing orderbooks
"""
import os
import time
from dotenv import load_dotenv

load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
ORDER_SIZE = 5

def main():
    print("\n=== MARKET MAKER ===\n")

    # Connect
    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected!\n")

    # Get markets from CLOB directly
    print("Fetching markets from CLOB...")
    resp = client.get_simplified_markets()
    all_markets = resp.get("data", []) if isinstance(resp, dict) else []
    print(f"Found {len(all_markets)} total markets")

    # Filter for ACTIVE markets only
    markets = [m for m in all_markets if m.get("active") == True and m.get("closed") == False]
    print(f"Active markets: {len(markets)}\n")

    if not markets:
        print("No active markets!")
        return

    count = 0
    skipped = 0
    for m in markets[:30]:  # Try more markets since some may fail
        question = m.get("question", "")[:50]
        tokens = m.get("tokens", [])

        if len(tokens) < 2:
            continue

        # Get token info
        t1 = tokens[0]
        t2 = tokens[1]

        token1_id = t1.get("token_id")
        token2_id = t2.get("token_id")
        price1 = float(t1.get("price", 0.5))
        price2 = float(t2.get("price", 0.5))
        outcome1 = t1.get("outcome", "Yes")
        outcome2 = t2.get("outcome", "No")

        # Skip extreme prices
        if price1 < 0.1 or price1 > 0.9:
            continue

        # Bid 5 cents below
        bid1 = max(0.01, round(price1 - 0.05, 2))
        bid2 = max(0.01, round(price2 - 0.05, 2))

        # First check if orderbook exists
        try:
            book = client.get_order_book(token1_id)
            if not book or (not book.get("bids") and not book.get("asks")):
                skipped += 1
                continue
        except Exception as e:
            if "does not exist" in str(e):
                skipped += 1
                continue
            # Other error, try anyway

        print(f"{question}...")
        print(f"  {outcome1}: ${price1:.2f} -> bid ${bid1:.2f}")
        print(f"  {outcome2}: ${price2:.2f} -> bid ${bid2:.2f}")

        # Place orders
        oid1 = None
        oid2 = None
        try:
            order1 = OrderArgs(token_id=token1_id, price=bid1, size=ORDER_SIZE, side=BUY)
            signed1 = client.create_order(order1)
            resp1 = client.post_order(signed1, OrderType.GTC)
            oid1 = resp1.get("orderID")
            if oid1:
                print(f"  ORDER 1: {oid1[:20]}...")
        except Exception as e:
            err = str(e)
            if "does not exist" in err:
                print(f"  Skipping (no orderbook)")
                continue
            print(f"  Error 1: {err[:60]}")

        try:
            order2 = OrderArgs(token_id=token2_id, price=bid2, size=ORDER_SIZE, side=BUY)
            signed2 = client.create_order(order2)
            resp2 = client.post_order(signed2, OrderType.GTC)
            oid2 = resp2.get("orderID")
            if oid2:
                print(f"  ORDER 2: {oid2[:20]}...")
        except Exception as e:
            err = str(e)
            if "does not exist" not in err:
                print(f"  Error 2: {err[:60]}")

        if oid1 or oid2:
            count += 1
        print()
        time.sleep(0.3)

    print(f"=== Orders placed on {count} markets (skipped {skipped} inactive) ===")
    print("Check: https://polymarket.com/portfolio")

if __name__ == "__main__":
    main()
