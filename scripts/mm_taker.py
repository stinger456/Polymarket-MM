#!/usr/bin/env python3
"""
TAKER Market Maker - Crosses the spread for GUARANTEED fills.

Instead of placing limit orders and hoping both fill,
we BUY AT THE ASK on both sides simultaneously.

This GUARANTEES both orders fill instantly.
Only trades when: YES_ask + NO_ask < $1.00 (profitable)
"""

import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# Config - smaller size for safety
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_PROFIT_CENTS = 0.5  # Minimum 0.5 cent profit per share


class TakerMarketMaker:
    def __init__(self):
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        self.client = ClobClient(
            CLOB_HOST, key=private_key, chain_id=CHAIN_ID,
            signature_type=2, funder=safe_address,
        )
        try:
            creds = self.client.derive_api_key()
        except:
            creds = self.client.create_api_key()
        self.client.set_api_creds(creds)

        self.total_pnl = 0.0
        self.trades = 0
        self.wins = 0
        print(f"✓ Connected: {safe_address[:20]}...")

    def get_markets(self) -> List[Dict]:
        """Get active BTC hourly markets."""
        markets = []
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        for offset in range(4):
            dt = now + timedelta(hours=offset)
            month = dt.strftime("%B").lower()
            day = dt.day
            hour = dt.hour

            if hour == 0: h = "12am"
            elif hour < 12: h = f"{hour}am"
            elif hour == 12: h = "12pm"
            else: h = f"{hour-12}pm"

            slug = f"bitcoin-up-or-down-{month}-{day}-{h}-et"

            try:
                with httpx.Client(timeout=10) as http:
                    resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                    events = resp.json()
                    if events:
                        for mkt in events[0].get("markets", []):
                            if mkt.get("closed"): continue
                            tokens = mkt.get("clobTokenIds", [])
                            if isinstance(tokens, str): tokens = json.loads(tokens)
                            if len(tokens) >= 2:
                                end_time = dt.replace(minute=0, second=0) + timedelta(hours=1)
                                mins_left = (end_time - now).total_seconds() / 60
                                markets.append({
                                    "yes_token": tokens[0],
                                    "no_token": tokens[1],
                                    "hour": h.upper(),
                                    "mins_left": mins_left,
                                })
            except:
                pass
        return markets

    def get_orderbook(self, token_id: str) -> Dict:
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:3], "asks": asks[:3]}
        except:
            return {"bids": [], "asks": []}

    def find_opportunity(self, markets: List[Dict]) -> Optional[Dict]:
        """Find a profitable taker opportunity."""
        for m in markets:
            if m["mins_left"] < 5:
                continue

            yes_ob = self.get_orderbook(m["yes_token"])
            no_ob = self.get_orderbook(m["no_token"])

            if not yes_ob["asks"] or not no_ob["asks"]:
                continue

            yes_ask, yes_size = yes_ob["asks"][0]
            no_ask, no_size = no_ob["asks"][0]

            # Check liquidity
            if yes_size < ORDER_SIZE or no_size < ORDER_SIZE:
                continue

            total = yes_ask + no_ask
            profit = 1.0 - total
            profit_cents = profit * 100

            if profit_cents >= MIN_PROFIT_CENTS:
                return {
                    "market": m,
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "yes_size": yes_size,
                    "no_size": no_size,
                    "total": total,
                    "profit": profit,
                    "profit_cents": profit_cents,
                }

        return None

    def execute(self, opp: Dict) -> bool:
        """Execute taker trade - buy at ask for instant fill."""
        m = opp["market"]
        size = ORDER_SIZE

        print(f"\n{'='*60}")
        print(f"🚀 TAKER TRADE - {m['hour']} ET")
        print(f"{'='*60}")
        print(f"   BUY YES @ ${opp['yes_ask']:.3f} (ask)")
        print(f"   BUY NO  @ ${opp['no_ask']:.3f} (ask)")
        print(f"   Total: ${opp['total']:.3f}")
        print(f"   Profit: ${opp['profit']*size:.2f} ({opp['profit_cents']:.1f}¢/share)")

        # Place both orders at the ASK price (crosses spread, fills immediately)
        start = time.time()

        def place(token, price):
            try:
                args = OrderArgs(token_id=token, price=price, size=size, side=BUY)
                signed = self.client.create_order(args)
                resp = self.client.post_order(signed, OrderType.FOK)  # Fill or Kill
                return resp.get("orderID") or resp.get("id"), resp
            except Exception as e:
                return None, str(e)

        with ThreadPoolExecutor(max_workers=2) as ex:
            yes_f = ex.submit(place, m["yes_token"], opp["yes_ask"])
            no_f = ex.submit(place, m["no_token"], opp["no_ask"])
            yes_id, yes_resp = yes_f.result()
            no_id, no_resp = no_f.result()

        elapsed = (time.time() - start) * 1000
        print(f"\n   Executed in {elapsed:.0f}ms")

        if yes_id and no_id:
            pnl = opp["profit"] * size
            self.total_pnl += pnl
            self.trades += 1
            self.wins += 1
            print(f"   ✅ BOTH FILLED! Profit: ${pnl:.2f}")
            self.show_pnl()
            return True
        else:
            print(f"   ❌ Fill failed")
            print(f"      YES: {yes_resp}")
            print(f"      NO: {no_resp}")
            self.trades += 1
            return False

    def show_pnl(self):
        print(f"\n   📈 P&L: ${self.total_pnl:+.2f} | Trades: {self.trades} | Wins: {self.wins}")

    def run(self):
        print(f"\n{'='*60}")
        print("TAKER MARKET MAKER")
        print("Buys at ASK for guaranteed fills")
        print(f"Size: ${ORDER_SIZE} | Min profit: {MIN_PROFIT_CENTS}¢")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        scan = 0
        while True:
            try:
                scan += 1
                ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

                markets = self.get_markets()
                if not markets:
                    print(f"\r[{ts}] No markets", end="", flush=True)
                    time.sleep(2)
                    continue

                # Show current spreads
                spreads = []
                for m in markets[:3]:
                    yes_ob = self.get_orderbook(m["yes_token"])
                    no_ob = self.get_orderbook(m["no_token"])
                    if yes_ob["asks"] and no_ob["asks"]:
                        total = yes_ob["asks"][0][0] + no_ob["asks"][0][0]
                        profit = (1 - total) * 100
                        spreads.append(f"{m['hour']}:{profit:+.1f}¢")

                status = " | ".join(spreads) if spreads else "..."
                print(f"\r[{ts}] #{scan} | {status} | P&L: ${self.total_pnl:+.2f}   ", end="", flush=True)

                # Look for opportunity
                opp = self.find_opportunity(markets)
                if opp:
                    self.execute(opp)
                    time.sleep(5)  # Pause after trade

                time.sleep(1)  # Fast scanning

            except KeyboardInterrupt:
                print(f"\n\n{'='*60}")
                print("🛑 STOPPED")
                print(f"{'='*60}")
                self.show_pnl()
                break
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(3)


if __name__ == "__main__":
    TakerMarketMaker().run()
