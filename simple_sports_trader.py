#!/usr/bin/env python3
"""
Market Maker - JUST PLACE ORDERS on markets with active orderbooks
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
    print("\n=== MARKET MAKER - PLACING ORDERS NOW ===\n")

    client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY,
                        chain_id=137, signature_type=SIG_TYPE, funder=FUNDER)
    client.set_api_creds(client.create_or_derive_api_creds())
    print("Connected!\n")

    # Get markets
    print("Getting markets...")
    resp = client.get_markets()
    markets = resp if isinstance(resp, list) else []
    print(f"Found {len(markets)} markets\n")

    count = 0
    tried = 0

    for m in markets:
        if count >= 10:  # Stop after 10 successful orders
            break

        tokens = m.get("tokens", [])
        if len(tokens) < 2:
            continue

        # Get token info
        t1 = tokens[0]
        t2 = tokens[1]
        token1 = t1.get("token_id")
        token2 = t2.get("token_id")

        if not token1 or not token2:
            continue

        # Get prices from orderbook
        try:
            book = client.get_order_book(token1)
            if not book:
                continue
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids and not asks:
                continue

            # Get mid price
            if bids and asks:
                best_bid = float(bids[0].get("price", 0.5))
                best_ask = float(asks[0].get("price", 0.5))
                price1 = (best_bid + best_ask) / 2
            elif bids:
                price1 = float(bids[0].get("price", 0.5))
            else:
                price1 = float(asks[0].get("price", 0.5))

            price2 = 1.0 - price1
        except:
            continue

        tried += 1
        question = m.get("question", "")[:45]

        # Skip extreme prices
        if price1 < 0.15 or price1 > 0.85:
            continue

        bid1 = max(0.01, round(price1 - 0.03, 2))
        bid2 = max(0.01, round(price2 - 0.03, 2))

        print(f"{question}...")
        print(f"  Yes: ${price1:.2f} -> bid ${bid1:.2f}")
        print(f"  No:  ${price2:.2f} -> bid ${bid2:.2f}")

        oid1 = None
        oid2 = None

        try:
            order1 = OrderArgs(token_id=token1, price=bid1, size=ORDER_SIZE, side=BUY)
            signed1 = client.create_order(order1)
            resp1 = client.post_order(signed1, OrderType.GTC)
            oid1 = resp1.get("orderID")
            if oid1:
                print(f"  ORDER YES: {oid1[:16]}... PLACED!")
        except Exception as e:
            err = str(e)
            if "does not exist" not in err:
                print(f"  Error: {err[:40]}")

        try:
            order2 = OrderArgs(token_id=token2, price=bid2, size=ORDER_SIZE, side=BUY)
            signed2 = client.create_order(order2)
            resp2 = client.post_order(signed2, OrderType.GTC)
            oid2 = resp2.get("orderID")
            if oid2:
                print(f"  ORDER NO:  {oid2[:16]}... PLACED!")
        except Exception as e:
            err = str(e)
            if "does not exist" not in err:
                print(f"  Error: {err[:40]}")

        if oid1 or oid2:
            count += 1
            print(f"  SUCCESS! ({count} markets done)")
        print()
        time.sleep(0.2)

    print(f"\n=== DONE: {count} markets, {tried} tried ===")
    print("Check your orders: https://polymarket.com/portfolio")

if __name__ == "__main__":
    main()
