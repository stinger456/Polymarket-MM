#!/usr/bin/env python3
"""
Continuous Atomic Market Maker

Continuously scans for profitable opportunities and executes automatically.
- Finds active BTC hourly markets (current + next hour)
- Analyzes orderbook every few seconds
- Executes when profitable spread is found
- Ensures both sides fill or exits
- Loops forever looking for opportunities
"""

import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# Configuration
SCAN_INTERVAL_SECONDS = 3  # How often to check orderbook
FILL_TIMEOUT_SECONDS = 10  # How long to wait for fills
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_PROFIT_MARGIN = 0.01  # Minimum $0.01 profit per share
MIN_PROFIT_PCT = 1.0  # Minimum 1% profit


class ContinuousMarketMaker:
    def __init__(self):
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        self.safe_address = safe_address
        self.client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=2,
            funder=safe_address,
        )

        try:
            creds = self.client.derive_api_key()
        except:
            creds = self.client.create_api_key()
        self.client.set_api_creds(creds)

        self.active_positions = {}  # Track what we're holding
        print(f"✓ Connected! Safe: {safe_address[:20]}...")

    def get_all_btc_hourly_markets(self) -> List[Dict]:
        """Get current and upcoming BTC hourly markets."""
        markets = []
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        # Check current hour and next 2 hours
        for offset in range(3):
            dt = now + timedelta(hours=offset)
            month = dt.strftime("%B").lower()
            day = dt.day
            hour = dt.hour

            if hour == 0:
                h = "12am"
            elif hour < 12:
                h = f"{hour}am"
            elif hour == 12:
                h = "12pm"
            else:
                h = f"{hour-12}pm"

            slug = f"bitcoin-up-or-down-{month}-{day}-{h}-et"

            try:
                headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
                with httpx.Client(headers=headers, timeout=10) as http:
                    resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                    events = resp.json()

                    if events:
                        event = events[0]
                        for market in event.get("markets", []):
                            if market.get("closed"):
                                continue

                            tokens = market.get("clobTokenIds", [])
                            if isinstance(tokens, str):
                                tokens = json.loads(tokens)

                            if len(tokens) >= 2:
                                # Calculate time until expiry
                                end_time = dt.replace(minute=0, second=0, microsecond=0)
                                mins_left = (end_time - now).total_seconds() / 60

                                markets.append({
                                    "yes_token": tokens[0],
                                    "no_token": tokens[1],
                                    "question": market.get("question", ""),
                                    "slug": slug,
                                    "hour": h,
                                    "mins_until_expiry": mins_left,
                                })
            except Exception as e:
                pass  # Skip failed fetches

        return markets

    def get_orderbook(self, token_id: str) -> Dict:
        """Fetch orderbook for a token."""
        try:
            ob = self.client.get_order_book(token_id)
            return {
                "bids": [(float(b.price), float(b.size)) for b in (ob.bids or [])],
                "asks": [(float(a.price), float(a.size)) for a in (ob.asks or [])],
            }
        except:
            return {"bids": [], "asks": []}

    def analyze_opportunity(self, market: Dict) -> Optional[Dict]:
        """
        Analyze a market for profitable opportunity.
        Returns trade details if profitable, None otherwise.
        """
        yes_ob = self.get_orderbook(market["yes_token"])
        no_ob = self.get_orderbook(market["no_token"])

        if not yes_ob["asks"] or not no_ob["asks"]:
            return None

        yes_ask = yes_ob["asks"][0][0]
        yes_ask_size = yes_ob["asks"][0][1]
        no_ask = no_ob["asks"][0][0]
        no_ask_size = no_ob["asks"][0][1]

        yes_bid = yes_ob["bids"][0][0] if yes_ob["bids"] else 0
        no_bid = no_ob["bids"][0][0] if no_ob["bids"] else 0

        total_cost = yes_ask + no_ask
        profit = 1.0 - total_cost
        profit_pct = (profit / total_cost) * 100 if total_cost > 0 else 0

        # Check if profitable
        if profit < MIN_PROFIT_MARGIN or profit_pct < MIN_PROFIT_PCT:
            return None

        # Check if enough liquidity
        min_size = min(yes_ask_size, no_ask_size)
        if min_size < ORDER_SIZE:
            return None

        return {
            "market": market,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "yes_bid": yes_bid,
            "no_bid": no_bid,
            "yes_ask_size": yes_ask_size,
            "no_ask_size": no_ask_size,
            "total_cost": total_cost,
            "profit": profit,
            "profit_pct": profit_pct,
        }

    def place_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        """Place a single order."""
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            )
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            return response.get("orderID") or response.get("id")
        except Exception as e:
            print(f"    Order error: {e}")
            return None

    def check_order_filled(self, order_id: str, target_size: float) -> Tuple[bool, float]:
        """Check if order is filled."""
        try:
            order = self.client.get_order(order_id)
            filled = float(order.get("size_matched", 0))
            return filled >= target_size * 0.99, filled
        except:
            return False, 0

    def cancel_order(self, order_id: str):
        """Cancel an order."""
        try:
            self.client.cancel(order_id)
        except:
            pass

    def sell_position(self, token_id: str, size: float) -> Optional[str]:
        """Sell position at market bid."""
        ob = self.get_orderbook(token_id)
        if not ob["bids"]:
            return None
        sell_price = ob["bids"][0][0]
        return self.place_order(token_id, sell_price, size, SELL)

    def execute_trade(self, opp: Dict) -> bool:
        """Execute a trade opportunity."""
        market = opp["market"]
        size = ORDER_SIZE

        print(f"\n{'='*60}")
        print(f"🚀 EXECUTING TRADE: {market['question']}")
        print(f"{'='*60}")
        print(f"  YES: {size} @ ${opp['yes_ask']:.3f}")
        print(f"  NO:  {size} @ ${opp['no_ask']:.3f}")
        print(f"  Total: ${opp['total_cost']*size:.2f} | Profit: ${opp['profit']*size:.2f} ({opp['profit_pct']:.1f}%)")

        # Place both orders simultaneously
        start = time.time()
        with ThreadPoolExecutor(max_workers=2) as ex:
            yes_future = ex.submit(self.place_order, market["yes_token"], opp["yes_ask"], size, BUY)
            no_future = ex.submit(self.place_order, market["no_token"], opp["no_ask"], size, BUY)
            yes_id = yes_future.result()
            no_id = no_future.result()

        print(f"  Orders placed in {(time.time()-start)*1000:.0f}ms")

        if not yes_id or not no_id:
            print("  ❌ Failed to place orders!")
            if yes_id: self.cancel_order(yes_id)
            if no_id: self.cancel_order(no_id)
            return False

        # Monitor fills
        timeout_at = time.time() + FILL_TIMEOUT_SECONDS
        while time.time() < timeout_at:
            yes_filled, yes_size = self.check_order_filled(yes_id, size)
            no_filled, no_size = self.check_order_filled(no_id, size)

            if yes_filled and no_filled:
                print(f"  ✅ BOTH FILLED! Profit locked: ${opp['profit']*size:.2f}")
                return True

            time.sleep(0.3)

        # Timeout - emergency exit
        print(f"  ⚠️ TIMEOUT - Emergency exit!")

        yes_filled, yes_size = self.check_order_filled(yes_id, size)
        no_filled, no_size = self.check_order_filled(no_id, size)

        if yes_filled and not no_filled:
            print(f"  Cancelling NO, selling YES...")
            self.cancel_order(no_id)
            self.sell_position(market["yes_token"], yes_size)
        elif no_filled and not yes_filled:
            print(f"  Cancelling YES, selling NO...")
            self.cancel_order(yes_id)
            self.sell_position(market["no_token"], no_size)
        else:
            print(f"  Cancelling both...")
            self.cancel_order(yes_id)
            self.cancel_order(no_id)
            if yes_size > 0:
                self.sell_position(market["yes_token"], yes_size)
            if no_size > 0:
                self.sell_position(market["no_token"], no_size)

        return False

    def run(self):
        """Main loop - continuously scan and execute."""
        print("\n" + "="*60)
        print("CONTINUOUS MARKET MAKER STARTED")
        print("Scanning for profitable opportunities...")
        print("Press Ctrl+C to stop")
        print("="*60)

        trades_executed = 0
        scans = 0

        while True:
            try:
                scans += 1
                now = datetime.now(ZoneInfo("America/New_York"))
                timestamp = now.strftime("%H:%M:%S")

                # Get all active markets
                markets = self.get_all_btc_hourly_markets()

                if not markets:
                    print(f"\r[{timestamp}] No active markets found. Waiting...", end="", flush=True)
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue

                # Analyze each market
                best_opp = None
                for market in markets:
                    # Skip markets expiring in < 5 minutes (too risky)
                    if market["mins_until_expiry"] < 5:
                        continue

                    opp = self.analyze_opportunity(market)
                    if opp:
                        if best_opp is None or opp["profit_pct"] > best_opp["profit_pct"]:
                            best_opp = opp

                if best_opp:
                    # Found opportunity - execute!
                    print(f"\n[{timestamp}] 💰 OPPORTUNITY FOUND!")
                    success = self.execute_trade(best_opp)
                    if success:
                        trades_executed += 1
                        print(f"\n✅ Trade #{trades_executed} complete!")
                    # Wait a bit before next scan
                    time.sleep(5)
                else:
                    # Show status
                    market_info = []
                    for m in markets[:2]:  # Show first 2 markets
                        yes_ob = self.get_orderbook(m["yes_token"])
                        no_ob = self.get_orderbook(m["no_token"])
                        if yes_ob["asks"] and no_ob["asks"]:
                            total = yes_ob["asks"][0][0] + no_ob["asks"][0][0]
                            profit = (1 - total) * 100
                            market_info.append(f"{m['hour']}:{profit:+.1f}%")

                    status = " | ".join(market_info) if market_info else "No data"
                    print(f"\r[{timestamp}] Scan #{scans} | {status} | Trades: {trades_executed}", end="", flush=True)

                time.sleep(SCAN_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                print(f"\n\nStopping... Executed {trades_executed} trades.")
                break
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(5)


def main():
    print("="*60)
    print("CONTINUOUS ATOMIC MARKET MAKER")
    print("="*60)
    print(f"Order size: ${ORDER_SIZE}")
    print(f"Min profit: {MIN_PROFIT_PCT}% (${MIN_PROFIT_MARGIN})")
    print(f"Scan interval: {SCAN_INTERVAL_SECONDS}s")

    mm = ContinuousMarketMaker()
    mm.run()


if __name__ == "__main__":
    main()
