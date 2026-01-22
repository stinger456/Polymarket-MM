#!/usr/bin/env python3
"""
Smart Market Maker - Masters the orderbook

Key insight: To get BOTH sides filled, we must bid CLOSE to the ask.
- If YES ask is $0.65, bid $0.64 (1 cent below)
- If NO ask is $0.36, bid $0.35 (1 cent below)
- Total: $0.99 → Profit: $0.01 per share

This places us at the TOP of the bid queue, most likely to fill next.
"""

import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple

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

# Config
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_EDGE_CENTS = 1  # Minimum 1 cent ($0.01) profit per share
SCAN_INTERVAL = 2  # Seconds between scans
FILL_TIMEOUT = 15  # Seconds to wait for fills


class SmartMarketMaker:
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
        print(f"✓ Connected: {safe_address[:20]}...")

    def get_markets(self) -> List[Dict]:
        """Get active BTC hourly markets."""
        markets = []
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        for offset in range(4):  # Current + next 3 hours
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
                        event = events[0]
                        for mkt in event.get("markets", []):
                            if mkt.get("closed"): continue
                            tokens = mkt.get("clobTokenIds", [])
                            if isinstance(tokens, str): tokens = json.loads(tokens)
                            if len(tokens) >= 2:
                                # Time until this hour ends
                                end_time = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                                mins_left = (end_time - now).total_seconds() / 60
                                markets.append({
                                    "yes_token": tokens[0],
                                    "no_token": tokens[1],
                                    "question": mkt.get("question", ""),
                                    "hour": h.upper(),
                                    "mins_left": mins_left,
                                })
            except:
                pass
        return markets

    def get_orderbook_deep(self, token_id: str) -> Dict:
        """Get full orderbook with depth."""
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:5], "asks": asks[:5]}  # Top 5 levels
        except Exception as e:
            return {"bids": [], "asks": [], "error": str(e)}

    def analyze_market(self, market: Dict) -> Dict:
        """Deep orderbook analysis."""
        yes_ob = self.get_orderbook_deep(market["yes_token"])
        no_ob = self.get_orderbook_deep(market["no_token"])

        analysis = {
            "market": market,
            "yes_ob": yes_ob,
            "no_ob": no_ob,
            "tradeable": False,
            "reason": "",
        }

        # Check if orderbooks exist
        if not yes_ob["asks"] or not no_ob["asks"]:
            analysis["reason"] = "No asks available"
            return analysis

        if not yes_ob["bids"] or not no_ob["bids"]:
            analysis["reason"] = "No bids available"
            return analysis

        # Best prices
        yes_best_bid, yes_bid_size = yes_ob["bids"][0]
        yes_best_ask, yes_ask_size = yes_ob["asks"][0]
        no_best_bid, no_bid_size = no_ob["bids"][0]
        no_best_ask, no_ask_size = no_ob["asks"][0]

        analysis["yes_bid"] = yes_best_bid
        analysis["yes_ask"] = yes_best_ask
        analysis["yes_spread"] = yes_best_ask - yes_best_bid
        analysis["no_bid"] = no_best_bid
        analysis["no_ask"] = no_best_ask
        analysis["no_spread"] = no_best_ask - no_best_bid

        # Strategy: Bid 1 cent below the ask to be top of queue
        # This gives us best chance of filling when someone market sells
        our_yes_bid = round(yes_best_ask - 0.01, 2)
        our_no_bid = round(no_best_ask - 0.01, 2)

        # Make sure we're at least at the current best bid
        our_yes_bid = max(our_yes_bid, yes_best_bid)
        our_no_bid = max(our_no_bid, no_best_bid)

        analysis["our_yes_bid"] = our_yes_bid
        analysis["our_no_bid"] = our_no_bid

        total_cost = our_yes_bid + our_no_bid
        profit = 1.0 - total_cost
        profit_cents = profit * 100

        analysis["total_cost"] = total_cost
        analysis["profit"] = profit
        analysis["profit_cents"] = profit_cents

        # Check profitability
        if profit_cents < MIN_EDGE_CENTS:
            analysis["reason"] = f"Not profitable: {profit_cents:.1f}¢ edge (need {MIN_EDGE_CENTS}¢)"
            return analysis

        # Check liquidity - need enough size at the ask for when we want to exit
        if yes_ask_size < ORDER_SIZE or no_ask_size < ORDER_SIZE:
            analysis["reason"] = f"Low liquidity: YES={yes_ask_size:.0f}, NO={no_ask_size:.0f}"
            return analysis

        analysis["tradeable"] = True
        analysis["reason"] = f"✓ {profit_cents:.1f}¢ edge"
        return analysis

    def display_orderbook(self, analysis: Dict):
        """Pretty print orderbook state."""
        m = analysis["market"]
        print(f"\n{'='*60}")
        print(f"📊 {m['question']}")
        print(f"   {m['hour']} ET | {m['mins_left']:.0f} mins left")
        print(f"{'='*60}")

        if "yes_bid" not in analysis:
            print(f"   ❌ {analysis['reason']}")
            return

        print(f"\n   {'YES (Up)':^25} | {'NO (Down)':^25}")
        print(f"   {'-'*25} | {'-'*25}")
        print(f"   Best Ask: ${analysis['yes_ask']:.2f}" + " "*10 + f"| Best Ask: ${analysis['no_ask']:.2f}")
        print(f"   Best Bid: ${analysis['yes_bid']:.2f}" + " "*10 + f"| Best Bid: ${analysis['no_bid']:.2f}")
        print(f"   Spread:   ${analysis['yes_spread']:.2f}" + " "*10 + f"| Spread:   ${analysis['no_spread']:.2f}")
        print()
        print(f"   Our YES bid: ${analysis['our_yes_bid']:.2f}")
        print(f"   Our NO bid:  ${analysis['our_no_bid']:.2f}")
        print(f"   Total:       ${analysis['total_cost']:.2f}")
        print(f"   Profit:      ${analysis['profit']:.2f} ({analysis['profit_cents']:.1f}¢ per share)")
        print()
        print(f"   Status: {analysis['reason']}")

    def place_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        """Place order and return ID."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"      Order error: {e}")
            return None

    def check_fill(self, order_id: str, target: float) -> Tuple[bool, float]:
        """Check fill status."""
        try:
            order = self.client.get_order(order_id)
            filled = float(order.get("size_matched", 0))
            return filled >= target * 0.95, filled
        except:
            return False, 0

    def cancel_order(self, order_id: str):
        try: self.client.cancel(order_id)
        except: pass

    def execute_trade(self, analysis: Dict) -> bool:
        """Execute the trade with monitoring."""
        m = analysis["market"]
        size = ORDER_SIZE

        print(f"\n{'='*60}")
        print(f"🚀 EXECUTING TRADE")
        print(f"{'='*60}")
        print(f"   YES: {size} @ ${analysis['our_yes_bid']:.2f}")
        print(f"   NO:  {size} @ ${analysis['our_no_bid']:.2f}")
        print(f"   Expected profit: ${analysis['profit'] * size:.2f}")

        # Place both orders
        start = time.time()
        with ThreadPoolExecutor(max_workers=2) as ex:
            yes_f = ex.submit(self.place_order, m["yes_token"], analysis["our_yes_bid"], size, BUY)
            no_f = ex.submit(self.place_order, m["no_token"], analysis["our_no_bid"], size, BUY)
            yes_id = yes_f.result()
            no_id = no_f.result()

        print(f"\n   Orders placed in {(time.time()-start)*1000:.0f}ms")
        print(f"   YES: {yes_id[:30] if yes_id else 'FAILED'}...")
        print(f"   NO:  {no_id[:30] if no_id else 'FAILED'}...")

        if not yes_id or not no_id:
            if yes_id: self.cancel_order(yes_id)
            if no_id: self.cancel_order(no_id)
            print("   ❌ Failed to place both orders")
            return False

        # Monitor fills
        print(f"\n   Monitoring fills (timeout: {FILL_TIMEOUT}s)...")
        timeout_at = time.time() + FILL_TIMEOUT
        yes_filled = no_filled = False
        yes_size = no_size = 0

        while time.time() < timeout_at:
            yes_filled, yes_size = self.check_fill(yes_id, size)
            no_filled, no_size = self.check_fill(no_id, size)

            elapsed = time.time() - start
            print(f"\r   [{elapsed:.1f}s] YES: {yes_size:.1f}/{size} | NO: {no_size:.1f}/{size}   ", end="", flush=True)

            if yes_filled and no_filled:
                print(f"\n\n   ✅ BOTH FILLED! Profit: ${analysis['profit'] * size:.2f}")
                return True

            time.sleep(0.5)

        # Timeout - exit
        print(f"\n\n   ⏰ TIMEOUT - Exiting...")

        # Get final fill status
        yes_filled, yes_size = self.check_fill(yes_id, size)
        no_filled, no_size = self.check_fill(no_id, size)

        if yes_filled and not no_filled:
            print(f"   YES filled ({yes_size}), NO didn't - selling YES")
            self.cancel_order(no_id)
            # Sell at bid
            yes_ob = self.get_orderbook_deep(m["yes_token"])
            if yes_ob["bids"]:
                sell_price = yes_ob["bids"][0][0]
                self.place_order(m["yes_token"], sell_price, yes_size, SELL)
                print(f"   Sold YES @ ${sell_price:.2f}")

        elif no_filled and not yes_filled:
            print(f"   NO filled ({no_size}), YES didn't - selling NO")
            self.cancel_order(yes_id)
            no_ob = self.get_orderbook_deep(m["no_token"])
            if no_ob["bids"]:
                sell_price = no_ob["bids"][0][0]
                self.place_order(m["no_token"], sell_price, no_size, SELL)
                print(f"   Sold NO @ ${sell_price:.2f}")

        else:
            print(f"   Neither filled fully - cancelling both")
            self.cancel_order(yes_id)
            self.cancel_order(no_id)

        return False

    def run(self):
        """Main loop."""
        print(f"\n{'='*60}")
        print("SMART MARKET MAKER - RUNNING")
        print(f"Order size: ${ORDER_SIZE} | Min edge: {MIN_EDGE_CENTS}¢")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        trades = 0
        scan = 0

        while True:
            try:
                scan += 1
                et_now = datetime.now(ZoneInfo("America/New_York"))
                ts = et_now.strftime("%H:%M:%S ET")

                markets = self.get_markets()

                if not markets:
                    print(f"\r[{ts}] No markets found. Waiting...", end="", flush=True)
                    time.sleep(SCAN_INTERVAL)
                    continue

                # Find best opportunity
                best = None
                summaries = []

                for m in markets:
                    # Skip if < 3 mins left
                    if m["mins_left"] < 3:
                        continue

                    analysis = self.analyze_market(m)

                    # Collect summary for display
                    if "profit_cents" in analysis:
                        summaries.append(f"{m['hour']}:{analysis['profit_cents']:+.0f}¢")
                    else:
                        summaries.append(f"{m['hour']}:--")

                    if analysis["tradeable"]:
                        if best is None or analysis["profit_cents"] > best["profit_cents"]:
                            best = analysis

                # Display status
                status = " | ".join(summaries) if summaries else "No data"
                print(f"\r[{ts}] Scan #{scan} | {status} | Trades: {trades}   ", end="", flush=True)

                if best:
                    # Show orderbook and execute
                    self.display_orderbook(best)

                    print("\n   💰 PROFITABLE OPPORTUNITY FOUND!")
                    confirm = input("   Execute trade? (y/n): ").strip().lower()

                    if confirm == 'y':
                        success = self.execute_trade(best)
                        if success:
                            trades += 1
                    else:
                        print("   Skipped.")

                time.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                print(f"\n\nStopped. Trades executed: {trades}")
                break
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(5)


def main():
    mm = SmartMarketMaker()
    mm.run()


if __name__ == "__main__":
    main()
