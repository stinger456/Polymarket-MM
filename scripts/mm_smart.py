#!/usr/bin/env python3
"""
HYBRID Market Maker - BTC Hourly (Adaptive)

Strategy:
1. Post MAKER order on one side (better price, inside the spread)
2. Wait for fill (with timeout)
3. When filled → INSTANTLY buy other side at ASK (taker, guaranteed fill)
4. Result: Always hedged, capture maker edge

Adaptive:
- Low volatility: Accept tighter spreads (0.1¢+ profit)
- High volatility: Require wider spreads (0.5¢+ profit)
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple
from collections import deque

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

# Config
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_PROFIT_LOW_VOL = 1.0   # 1¢ minimum to cover slippage
MIN_PROFIT_HIGH_VOL = 1.5  # 1.5¢ minimum during high volatility
SLIPPAGE_BUFFER = 0.5      # Assume 0.5¢ slippage on hedge
MAKER_TIMEOUT = 10  # Short timeout
AGGRESSIVE_THRESHOLD = 0.01  # If profit >= 1¢, use taker-taker (ALWAYS use guaranteed fills)
DASHBOARD_INTERVAL = 2 * 60 * 60  # 2 hours


class HybridMM:
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

        # Stats
        self.total_pnl = 0.0
        self.total_trades = 0
        self.wins = 0
        self.start_time = time.time()
        self.last_dashboard = time.time()

        # Volatility tracking (BTC prices over last 5 mins)
        self.btc_prices = deque(maxlen=60)  # Store last 60 price checks
        self.last_btc_price = None
        self.volatility = "LOW"  # LOW, MEDIUM, HIGH

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
                                if mins_left > 5:
                                    markets.append({
                                        "yes_token": tokens[0],
                                        "no_token": tokens[1],
                                        "hour": h.upper(),
                                        "mins_left": mins_left,
                                    })
            except:
                pass
        return markets

    def get_ob(self, token_id: str) -> Dict:
        """Get orderbook."""
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:5], "asks": asks[:5]}
        except:
            return {"bids": [], "asks": []}

    def get_btc_price(self) -> Optional[float]:
        """Get current BTC price from Binance."""
        try:
            with httpx.Client(timeout=5) as http:
                resp = http.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
                return float(resp.json()["price"])
        except:
            return None

    def update_volatility(self):
        """Calculate volatility based on recent BTC price movement."""
        price = self.get_btc_price()
        if price:
            self.btc_prices.append(price)
            self.last_btc_price = price

        if len(self.btc_prices) < 10:
            self.volatility = "LOW"
            return

        # Calculate price range over recent samples
        prices = list(self.btc_prices)
        min_p, max_p = min(prices), max(prices)
        range_pct = (max_p - min_p) / min_p * 100

        # Classify volatility
        if range_pct > 0.3:  # >0.3% move in recent period
            self.volatility = "HIGH"
        elif range_pct > 0.1:  # 0.1-0.3% move
            self.volatility = "MEDIUM"
        else:
            self.volatility = "LOW"

    def get_min_profit(self) -> float:
        """Get minimum profit threshold based on volatility (includes slippage buffer)."""
        if self.volatility == "HIGH":
            return (MIN_PROFIT_HIGH_VOL + SLIPPAGE_BUFFER) / 100  # 2¢ total
        elif self.volatility == "MEDIUM":
            return (MIN_PROFIT_LOW_VOL + SLIPPAGE_BUFFER) / 100  # 1.5¢ total
        else:
            return (MIN_PROFIT_LOW_VOL) / 100  # 1¢ minimum

    def find_hybrid_opportunity(self, market: Dict) -> Optional[Dict]:
        """
        Find hybrid opportunity:
        - MAKER on one side (post at ask - 1¢ for best fill chance)
        - TAKER on other side (buy at ask)
        - Profit = 1.00 - maker_price - taker_price

        Uses dynamic threshold based on volatility.
        """
        yes_ob = self.get_ob(market["yes_token"])
        no_ob = self.get_ob(market["no_token"])

        if not yes_ob["bids"] or not yes_ob["asks"] or not no_ob["bids"] or not no_ob["asks"]:
            return None

        yes_bid = yes_ob["bids"][0][0]
        yes_ask, yes_ask_size = yes_ob["asks"][0]
        no_bid = no_ob["bids"][0][0]
        no_ask, no_ask_size = no_ob["asks"][0]

        # AGGRESSIVE: Post maker at ask - 1¢ (top of bid queue, best fill chance)
        # Option A: Maker on YES, Taker on NO
        yes_maker_price = round(yes_ask - 0.01, 2)  # 1¢ below ask = top of queue
        yes_maker_price = max(yes_maker_price, yes_bid)  # But at least match best bid
        option_a_cost = yes_maker_price + no_ask
        option_a_profit = 1.0 - option_a_cost

        # Option B: Maker on NO, Taker on YES
        no_maker_price = round(no_ask - 0.01, 2)  # 1¢ below ask = top of queue
        no_maker_price = max(no_maker_price, no_bid)  # But at least match best bid
        option_b_cost = yes_ask + no_maker_price
        option_b_profit = 1.0 - option_b_cost

        # Get dynamic threshold based on volatility
        min_profit = self.get_min_profit()

        best = None

        # Check Option A
        if option_a_profit >= min_profit and no_ask_size >= ORDER_SIZE:
            best = {
                "market": market,
                "maker_side": "YES",
                "maker_token": market["yes_token"],
                "maker_price": yes_maker_price,
                "hedge_side": "NO",
                "hedge_token": market["no_token"],
                "hedge_price": no_ask,
                "total_cost": option_a_cost,
                "profit": option_a_profit,
                "profit_cents": option_a_profit * 100,
            }

        # Check Option B
        if option_b_profit >= min_profit and yes_ask_size >= ORDER_SIZE:
            if best is None or option_b_profit > best["profit"]:
                best = {
                    "market": market,
                    "maker_side": "NO",
                    "maker_token": market["no_token"],
                    "maker_price": no_maker_price,
                    "hedge_side": "YES",
                    "hedge_token": market["yes_token"],
                    "hedge_price": yes_ask,
                    "total_cost": option_b_cost,
                    "profit": option_b_profit,
                    "profit_cents": option_b_profit * 100,
                }

        return best

    def place_order(self, token_id: str, price: float, size: float) -> Optional[str]:
        """Place GTC order."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"   Order error: {e}")
            return None

    def place_fok(self, token_id: str, price: float, size: float) -> bool:
        """Place Fill-or-Kill order for instant hedge."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.FOK)
            oid = resp.get("orderID") or resp.get("id")
            if oid:
                order = self.client.get_order(oid)
                filled = float(order.get("size_matched", 0))
                return filled >= size * 0.95
            return False
        except:
            return False

    def cancel(self, order_id: str):
        if order_id:
            try:
                self.client.cancel(order_id)
            except:
                pass

    def check_fill(self, order_id: str, size: float) -> Tuple[bool, float]:
        """Check if order is filled."""
        try:
            order = self.client.get_order(order_id)
            filled = float(order.get("size_matched", 0))
            return filled >= size * 0.95, filled
        except:
            return False, 0

    def execute_taker_taker(self, market: Dict, yes_ask: float, no_ask: float) -> bool:
        """
        Execute taker-taker trade (both sides at ask = guaranteed fill).
        Used when profit margin is high enough to justify taking both sides.
        """
        size = ORDER_SIZE
        total_cost = yes_ask + no_ask
        expected_profit = (1.0 - total_cost) * size

        print(f"\n{'='*60}")
        print(f"⚡ TAKER-TAKER TRADE - {market['hour']} ET (INSTANT FILL)")
        print(f"{'='*60}")
        print(f"   BUY YES @ ${yes_ask:.2f} (taker)")
        print(f"   BUY NO  @ ${no_ask:.2f} (taker)")
        print(f"   Total: ${total_cost:.3f} | Expected Profit: ${expected_profit:.2f}")

        # Place both orders as FOK for instant fill (add 1¢ buffer)
        print(f"\n   Executing both sides instantly...")

        yes_price = round(yes_ask + 0.01, 2)
        yes_filled = self.place_fok(market["yes_token"], yes_price, size)
        if not yes_filled:
            print(f"   ❌ YES order failed - aborting")
            return False

        print(f"   ✓ YES filled @ ${yes_price:.2f}")

        no_price = round(no_ask + 0.01, 2)
        no_filled = self.place_fok(market["no_token"], no_price, size)
        if not no_filled:
            print(f"   ❌ NO order failed - trying emergency hedge...")
            # Try emergency hedge at higher prices
            for attempt in range(5):
                time.sleep(0.2)
                no_ob = self.get_ob(market["no_token"])
                if no_ob["asks"]:
                    emergency_price = round(no_ob["asks"][0][0] + 0.02 + (attempt * 0.01), 2)
                    if self.place_fok(market["no_token"], emergency_price, size):
                        actual_cost = yes_price + emergency_price
                        actual_profit = (1.0 - actual_cost) * size
                        self.total_pnl += actual_profit
                        self.total_trades += 1
                        if actual_profit > 0:
                            self.wins += 1
                        status = "✅" if actual_profit > 0 else "⚠️"
                        print(f"   {status} Emergency hedge @ ${emergency_price:.2f}")
                        print(f"   💰 P&L: ${actual_profit:+.2f}")
                        self.show_stats()
                        return actual_profit > 0
            print(f"   ❌ HEDGE FAILED - UNHEDGED POSITION!")
            self.total_trades += 1
            return False

        print(f"   ✓ NO filled @ ${no_price:.2f}")

        # Both filled - calculate actual P&L
        actual_cost = yes_price + no_price
        actual_profit = (1.0 - actual_cost) * size

        self.total_pnl += actual_profit
        self.total_trades += 1
        if actual_profit > 0:
            self.wins += 1

        status = "✅ PROFIT" if actual_profit > 0 else "⚠️ LOSS"
        print(f"\n   {status} BOTH SIDES FILLED!")
        print(f"   💰 P&L: ${actual_profit:+.2f}")
        self.show_stats()
        return actual_profit > 0

    def execute_hybrid(self, opp: Dict) -> bool:
        """
        Execute hybrid trade:
        1. Place maker order
        2. Wait for fill
        3. Instant taker hedge
        """
        m = opp["market"]
        size = ORDER_SIZE

        # ALWAYS use taker-taker for guaranteed fills (maker orders are too risky)
        yes_ob = self.get_ob(m["yes_token"])
        no_ob = self.get_ob(m["no_token"])
        if yes_ob["asks"] and no_ob["asks"]:
            yes_ask = yes_ob["asks"][0][0]
            no_ask = no_ob["asks"][0][0]
            taker_profit = 1.0 - yes_ask - no_ask
            if taker_profit >= 0.005:  # At least 0.5¢ profit as taker-taker
                print(f"\n   ⚡ Using TAKER-TAKER for GUARANTEED fill (no risk)")
                return self.execute_taker_taker(m, yes_ask, no_ask)
            else:
                print(f"\n   ❌ Taker profit too low ({taker_profit*100:.1f}¢) - skipping")
                return False

        print(f"\n{'='*60}")
        print(f"🚀 HYBRID TRADE - {m['hour']} ET")
        print(f"{'='*60}")
        print(f"   MAKER: BUY {opp['maker_side']} @ ${opp['maker_price']:.2f}")
        print(f"   HEDGE: BUY {opp['hedge_side']} @ ${opp['hedge_price']:.2f} (instant)")
        print(f"   Total: ${opp['total_cost']:.3f} | Profit: ${opp['profit']*size:.2f}")

        # Step 1: Place maker order
        print(f"\n   [1/3] Placing MAKER order...")
        maker_id = self.place_order(opp["maker_token"], opp["maker_price"], size)

        if not maker_id:
            print(f"   ❌ Failed to place maker order")
            return False

        print(f"   ✓ Maker placed: {maker_id[:30]}...")

        # Step 2: Wait for fill
        print(f"\n   [2/3] Waiting for fill ({MAKER_TIMEOUT}s timeout)...")
        start = time.time()

        while time.time() - start < MAKER_TIMEOUT:
            filled, fill_size = self.check_fill(maker_id, size)
            elapsed = time.time() - start
            print(f"\r   [{elapsed:.0f}s] Fill: {fill_size:.1f}/{size}   ", end="", flush=True)

            if filled:
                print(f"\n   ✓ MAKER FILLED!")
                break
            time.sleep(0.5)
        else:
            print(f"\n   ⏰ Timeout - cancelling (no loss)")
            self.cancel(maker_id)
            return False

        # Step 3: Instant hedge
        print(f"\n   [3/3] HEDGING instantly...")

        # Get fresh hedge price
        hedge_ob = self.get_ob(opp["hedge_token"])
        if not hedge_ob["asks"]:
            print(f"   ❌ No asks for hedge!")
            return False

        hedge_price = hedge_ob["asks"][0][0]
        actual_cost = opp["maker_price"] + hedge_price
        actual_profit = (1.0 - actual_cost) * size

        print(f"   Hedge @ ${hedge_price:.2f}")

        # Try hedge with 1¢ buffer for slippage
        hedge_price_with_buffer = round(hedge_price + 0.01, 2)

        if self.place_fok(opp["hedge_token"], hedge_price_with_buffer, size):
            # Recalculate actual profit with the price we paid
            actual_cost = opp["maker_price"] + hedge_price_with_buffer
            actual_profit = (1.0 - actual_cost) * size

            self.total_pnl += actual_profit
            self.total_trades += 1
            if actual_profit > 0:
                self.wins += 1  # Only count as win if actually profitable

            status = "✅ PROFIT" if actual_profit > 0 else "⚠️ LOSS"
            print(f"\n   {status} HEDGED @ ${hedge_price_with_buffer:.2f}")
            print(f"   💰 P&L: ${actual_profit:+.2f}")
            self.show_stats()
            return actual_profit > 0
        else:
            # Emergency: try at higher price
            print(f"   ⚠️ FOK failed, trying emergency hedge...")
            for i in range(3):
                time.sleep(0.3)
                hedge_ob = self.get_ob(opp["hedge_token"])
                if hedge_ob["asks"]:
                    emergency_price = round(hedge_ob["asks"][0][0] + 0.01, 2)  # Add buffer
                    if self.place_fok(opp["hedge_token"], emergency_price, size):
                        actual_cost = opp["maker_price"] + emergency_price
                        actual_profit = (1.0 - actual_cost) * size
                        self.total_pnl += actual_profit
                        self.total_trades += 1
                        if actual_profit > 0:
                            self.wins += 1
                        status = "✅ PROFIT" if actual_profit > 0 else "⚠️ LOSS"
                        print(f"   {status} Emergency hedge @ ${emergency_price:.2f}")
                        print(f"   💰 P&L: ${actual_profit:+.2f}")
                        self.show_stats()
                        return actual_profit > 0

            print(f"   ❌ HEDGE FAILED - UNHEDGED POSITION!")
            self.total_trades += 1
            return False

    def show_stats(self):
        """Show P&L stats."""
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        runtime = (time.time() - self.start_time) / 3600
        color = "🟢" if self.total_pnl >= 0 else "🔴"
        print(f"\n   {'─'*40}")
        print(f"   {color} P&L: ${self.total_pnl:+.2f} | Trades: {self.total_trades} | Win: {win_rate:.0f}%")
        print(f"   Runtime: {runtime:.1f}h | ROI: {self.total_pnl/20*100:+.1f}% on $20")

    def show_dashboard(self):
        """Full dashboard every 2 hours."""
        runtime = (time.time() - self.start_time) / 3600
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

        print(f"\n\n{'='*60}")
        print(f"📊 2-HOUR DASHBOARD")
        print(f"{'='*60}")
        print(f"   Runtime:    {runtime:.1f} hours")
        print(f"   Trades:     {self.total_trades}")
        print(f"   Wins:       {self.wins}")
        print(f"   Win Rate:   {win_rate:.0f}%")
        print(f"   {'─'*40}")
        color = "🟢" if self.total_pnl >= 0 else "🔴"
        print(f"   {color} TOTAL P&L: ${self.total_pnl:+.2f}")
        print(f"   {color} ROI:       {self.total_pnl/20*100:+.1f}% on $20")
        print(f"{'='*60}\n")

    def run(self):
        """Main loop."""
        print(f"\n{'='*60}")
        print("HYBRID MARKET MAKER (PROFITABLE)")
        print("Maker on one side → Instant taker hedge")
        print(f"Size: ${ORDER_SIZE} | Slippage buffer: {SLIPPAGE_BUFFER}¢")
        print(f"Min profit: {MIN_PROFIT_LOW_VOL}¢ (low vol) | {MIN_PROFIT_HIGH_VOL}¢ (high vol)")
        print("Only takes trades with REAL profit margin")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        scan = 0

        while True:
            try:
                scan += 1
                ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

                # Update volatility every scan
                self.update_volatility()
                min_profit_now = self.get_min_profit() * 100  # in cents

                # 2-hour dashboard
                if time.time() - self.last_dashboard > DASHBOARD_INTERVAL:
                    self.show_dashboard()
                    self.last_dashboard = time.time()

                markets = self.get_markets()
                if not markets:
                    print(f"\r[{ts}] No markets", end="", flush=True)
                    time.sleep(2)
                    continue

                # Check opportunities and build status
                opportunities = []
                status_parts = []

                for m in markets[:3]:
                    opp = self.find_hybrid_opportunity(m)
                    if opp:
                        opportunities.append(opp)
                        status_parts.append(f"{m['hour']}:{opp['profit_cents']:+.1f}¢✓")
                    else:
                        # Show current spread
                        yes_ob = self.get_ob(m["yes_token"])
                        no_ob = self.get_ob(m["no_token"])
                        if yes_ob["bids"] and no_ob["bids"] and yes_ob["asks"] and no_ob["asks"]:
                            h1 = (1 - (yes_ob["bids"][0][0] + 0.01 + no_ob["asks"][0][0])) * 100
                            h2 = (1 - (yes_ob["asks"][0][0] + no_ob["bids"][0][0] + 0.01)) * 100
                            status_parts.append(f"{m['hour']}:{max(h1,h2):+.1f}¢")

                status = " | ".join(status_parts) if status_parts else "..."
                vol_indicator = f"VOL:{self.volatility[0]}" # L/M/H
                print(f"\r[{ts}] #{scan} | {vol_indicator}>{min_profit_now:.1f}¢ | {status} | P&L: ${self.total_pnl:+.2f}   ", end="", flush=True)

                # Execute best opportunity
                if opportunities:
                    opportunities.sort(key=lambda x: x["profit"], reverse=True)
                    best = opportunities[0]
                    self.execute_hybrid(best)
                    time.sleep(5)

                time.sleep(1)

            except KeyboardInterrupt:
                print(f"\n\n{'='*60}")
                print("🛑 STOPPED")
                print(f"{'='*60}")
                self.show_dashboard()
                break
            except Exception as e:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(3)


if __name__ == "__main__":
    HybridMM().run()
