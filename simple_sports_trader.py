#!/usr/bin/env python3
"""
Market Maker - Brute force find working markets
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

    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected!\n")

    # Get ALL markets
    print("Fetching ALL markets...")
    resp = client.get_simplified_markets()
    all_markets = resp.get("data", []) if isinstance(resp, dict) else []
    print(f"Total markets: {len(all_markets)}\n")

    # Try to find markets with ACTUAL orderbooks by checking
    print("Scanning for markets with active orderbooks...")
    working_markets = []

    for i, m in enumerate(all_markets):
        tokens = m.get("tokens", [])
        if len(tokens) < 2:
            continue

        token_id = tokens[0].get("token_id")
        if not token_id:
            continue

        # Try to get orderbook - this tells us if market is actually active
        try:
            book = client.get_order_book(token_id)
            # Check if it has ANY orders (bids or asks)
            if book and (book.get("bids") or book.get("asks")):
                working_markets.append(m)
                if len(working_markets) >= 20:  # Found enough
                    break
        except:
            pass  # No orderbook, skip

        if i % 100 == 0:
            print(f"  Scanned {i}/{len(all_markets)}, found {len(working_markets)} active...")

    print(f"\nFound {len(working_markets)} markets with active orderbooks!\n")

    if not working_markets:
        print("No markets with orderbooks found!")
        return

    # Now place orders on working markets
    count = 0
    for m in working_markets:
        question = m.get("question", "")[:50]
        tokens = m.get("tokens", [])

        t1, t2 = tokens[0], tokens[1]
        token1_id = t1.get("token_id")
        token2_id = t2.get("token_id")
        price1 = float(t1.get("price", 0.5))
        price2 = float(t2.get("price", 0.5))
        outcome1 = t1.get("outcome", "Yes")
        outcome2 = t2.get("outcome", "No")

        # Skip extreme prices
        if price1 < 0.1 or price1 > 0.9:
            continue

        bid1 = max(0.01, round(price1 - 0.05, 2))
        bid2 = max(0.01, round(price2 - 0.05, 2))

        print(f"{question}...")
        print(f"  {outcome1}: ${price1:.2f} -> bid ${bid1:.2f}")
        print(f"  {outcome2}: ${price2:.2f} -> bid ${bid2:.2f}")

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
            print(f"  Error 1: {str(e)[:50]}")

        try:
            order2 = OrderArgs(token_id=token2_id, price=bid2, size=ORDER_SIZE, side=BUY)
            signed2 = client.create_order(order2)
            resp2 = client.post_order(signed2, OrderType.GTC)
            oid2 = resp2.get("orderID")
            if oid2:
                print(f"  ORDER 2: {oid2[:20]}...")
        except Exception as e:
            print(f"  Error 2: {str(e)[:50]}")

        if oid1 or oid2:
            count += 1
        print()
        time.sleep(0.3)

    print(f"=== Orders placed on {count} markets ===")
    print("Check: https://polymarket.com/portfolio")

if __name__ == "__main__":
    main()
