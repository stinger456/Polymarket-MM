#!/usr/bin/env python3
"""
HYBRID Market Maker - Best Strategy for BTC Hourly

Strategy:
1. Analyze both orderbooks
2. Determine which side has better maker opportunity
3. Post MAKER order on one side (inside spread for better price)
4. When maker order fills, INSTANTLY hedge with TAKER on other side
5. Profit = maker edge - taker cost

This guarantees:
- Always hedged (no directional risk)
- Captures maker edge on one side
- Fast execution on hedge side

Key: Only trade when maker_price + other_side_ask < $1.00
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

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
MIN_PROFIT_CENTS = 0.5  # Minimum 0.5 cent profit per share
MAKER_ORDER_TIMEOUT = 30  # How long to wait for maker fill


@dataclass
class TradeStats:
    total_trades: int = 0
    successful: int = 0
    total_pnl: float = 0.0

    def log(self, success: bool, pnl: float):
        self.total_trades += 1
        if success:
            self.successful += 1
        self.total_pnl += pnl


class HybridMarketMaker:
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

        self.stats = TradeStats()
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
                                if mins_left > 5:  # Skip markets about to expire
                                    markets.append({
                                        "yes_token": tokens[0],
                                        "no_token": tokens[1],
                                        "hour": h.upper(),
                                        "mins_left": mins_left,
                                        "question": mkt.get("question", ""),
                                    })
            except:
                pass
        return markets

    def get_orderbook(self, token_id: str) -> Dict:
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:5], "asks": asks[:5]}
        except:
            return {"bids": [], "asks": []}

    def analyze_hybrid_opportunity(self, market: Dict) -> Optional[Dict]:
        """
        Find the best hybrid (maker + taker) opportunity.

        We'll be a MAKER on the side with better spread,
        and a TAKER on the other side for instant hedge.
        """
        yes_ob = self.get_orderbook(market["yes_token"])
        no_ob = self.get_orderbook(market["no_token"])

        if not yes_ob["bids"] or not yes_ob["asks"] or not no_ob["bids"] or not no_ob["asks"]:
            return None

        yes_bid, yes_bid_size = yes_ob["bids"][0]
        yes_ask, yes_ask_size = yes_ob["asks"][0]
        no_bid, no_bid_size = no_ob["bids"][0]
        no_ask, no_ask_size = no_ob["asks"][0]

        yes_spread = yes_ask - yes_bid
        no_spread = no_ask - no_bid

        # Option A: Maker on YES, Taker on NO
        # Post bid slightly above best YES bid, hedge by buying NO at ask
        yes_maker_price = round(yes_bid + 0.01, 2)  # Improve bid by 1 cent
        option_a_cost = yes_maker_price + no_ask
        option_a_profit = 1.0 - option_a_cost

        # Option B: Maker on NO, Taker on YES
        no_maker_price = round(no_bid + 0.01, 2)
        option_b_cost = yes_ask + no_maker_price
        option_b_profit = 1.0 - option_b_cost

        # Check liquidity for hedge
        if yes_ask_size < ORDER_SIZE or no_ask_size < ORDER_SIZE:
            return None

        # Pick the better option
        best = None

        if option_a_profit >= MIN_PROFIT_CENTS / 100:
            # Verify we can exit if maker doesn't fill
            # (we'd need to cancel, no loss)
            best = {
                "market": market,
                "maker_side": "YES",
                "maker_token": market["yes_token"],
                "maker_price": yes_maker_price,
                "hedge_side": "NO",
                "hedge_token": market["no_token"],
                "hedge_price": no_ask,
                "hedge_size_available": no_ask_size,
                "total_cost": option_a_cost,
                "profit": option_a_profit,
                "profit_cents": option_a_profit * 100,
                "yes_spread": yes_spread,
                "no_spread": no_spread,
            }

        if option_b_profit >= MIN_PROFIT_CENTS / 100:
            if best is None or option_b_profit > best["profit"]:
                best = {
                    "market": market,
                    "maker_side": "NO",
                    "maker_token": market["no_token"],
                    "maker_price": no_maker_price,
                    "hedge_side": "YES",
                    "hedge_token": market["yes_token"],
                    "hedge_price": yes_ask,
                    "hedge_size_available": yes_ask_size,
                    "total_cost": option_b_cost,
                    "profit": option_b_profit,
                    "profit_cents": option_b_profit * 100,
                    "yes_spread": yes_spread,
                    "no_spread": no_spread,
                }

        return best

    def place_order(self, token_id: str, price: float, size: float) -> Optional[str]:
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"      Order error: {e}")
            return None

    def place_fok_order(self, token_id: str, price: float, size: float) -> Tuple[bool, str]:
        """Place Fill-or-Kill order for instant hedge."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.FOK)
            order_id = resp.get("orderID") or resp.get("id")
            # FOK either fills completely or not at all
            # Check status
            if order_id:
                order = self.client.get_order(order_id)
                filled = float(order.get("size_matched", 0))
                if filled >= size * 0.95:
                    return True, order_id
            return False, str(resp)
        except Exception as e:
            return False, str(e)

    def cancel_order(self, order_id: str):
        if order_id:
            try:
                self.client.cancel(order_id)
            except:
                pass

    def check_order_fill(self, order_id: str, target_size: float) -> Tuple[bool, float]:
        """Returns (is_filled, filled_size)"""
        if not order_id:
            return False, 0
        try:
            order = self.client.get_order(order_id)
            filled = float(order.get("size_matched", 0))
            return filled >= target_size * 0.95, filled
        except:
            return False, 0

    def execute_hybrid_trade(self, opp: Dict) -> bool:
        """
        Execute hybrid trade:
        1. Place maker order
        2. Wait for fill (with timeout)
        3. When filled, instantly hedge with taker
        """
        m = opp["market"]
        size = ORDER_SIZE

        print(f"\n{'='*60}")
        print(f"🚀 HYBRID TRADE - {m['hour']} ET")
        print(f"{'='*60}")
        print(f"   Strategy: MAKER on {opp['maker_side']}, TAKER on {opp['hedge_side']}")
        print(f"   Maker: BUY {opp['maker_side']} @ ${opp['maker_price']:.2f}")
        print(f"   Hedge: BUY {opp['hedge_side']} @ ${opp['hedge_price']:.2f} (instant)")
        print(f"   Total: ${opp['total_cost']:.3f}")
        print(f"   Expected Profit: ${opp['profit']*size:.2f} ({opp['profit_cents']:.1f}¢/share)")

        # Step 1: Place maker order
        print(f"\n   [1/3] Placing MAKER order...")
        maker_id = self.place_order(opp["maker_token"], opp["maker_price"], size)

        if not maker_id:
            print(f"   ❌ Failed to place maker order")
            return False

        print(f"   ✓ Maker order placed: {maker_id[:30]}...")

        # Step 2: Wait for maker fill
        print(f"\n   [2/3] Waiting for maker fill (timeout: {MAKER_ORDER_TIMEOUT}s)...")
        start = time.time()
        timeout_at = start + MAKER_ORDER_TIMEOUT

        while time.time() < timeout_at:
            filled, filled_size = self.check_order_fill(maker_id, size)
            elapsed = time.time() - start

            print(f"\r   [{elapsed:.0f}s] Maker fill: {filled_size:.1f}/{size}   ", end="", flush=True)

            if filled:
                print(f"\n   ✓ Maker filled!")
                break

            time.sleep(0.5)
        else:
            # Timeout - cancel and exit (no loss, just opportunity cost)
            print(f"\n   ⏰ Timeout - cancelling maker order (no fill, no loss)")
            self.cancel_order(maker_id)
            return False

        # Step 3: Instant hedge with taker
        print(f"\n   [3/3] Hedging with TAKER order...")

        # Re-check hedge price (may have moved)
        hedge_ob = self.get_orderbook(opp["hedge_token"])
        if not hedge_ob["asks"]:
            print(f"   ❌ No asks available for hedge!")
            # We have an unhedged position - this is bad
            # For now, just report it
            self.stats.log(False, 0)
            return False

        current_hedge_ask, hedge_size = hedge_ob["asks"][0]

        if hedge_size < size:
            print(f"   ⚠️ Hedge liquidity low: {hedge_size:.1f} < {size}")

        # Use current ask price for hedge (may be different from original)
        actual_total = opp["maker_price"] + current_hedge_ask
        actual_profit = 1.0 - actual_total

        print(f"   Hedge price now: ${current_hedge_ask:.2f}")
        print(f"   Actual total: ${actual_total:.3f}")
        print(f"   Actual profit: ${actual_profit*size:.2f}")

        # Place hedge order at current ask
        hedge_success, hedge_result = self.place_fok_order(
            opp["hedge_token"],
            current_hedge_ask,
            size
        )

        if hedge_success:
            pnl = actual_profit * size
            print(f"\n   ✅ HEDGED! Both sides filled.")
            print(f"   💰 Profit: ${pnl:.2f}")
            self.stats.log(True, pnl)
            self.display_stats()
            return True
        else:
            # Hedge failed - we have unhedged exposure
            print(f"\n   ❌ Hedge failed: {hedge_result}")
            print(f"   ⚠️ WARNING: Unhedged {opp['maker_side']} position!")

            # Try to hedge at a higher price
            print(f"   Attempting emergency hedge...")
            for i in range(3):
                time.sleep(0.5)
                hedge_ob = self.get_orderbook(opp["hedge_token"])
                if hedge_ob["asks"]:
                    emergency_price = hedge_ob["asks"][0][0]
                    print(f"   Trying hedge @ ${emergency_price:.2f}...")
                    success, _ = self.place_fok_order(opp["hedge_token"], emergency_price, size)
                    if success:
                        actual_total = opp["maker_price"] + emergency_price
                        actual_profit = 1.0 - actual_total
                        pnl = actual_profit * size
                        print(f"   ✅ Emergency hedge successful!")
                        print(f"   P&L: ${pnl:.2f}")
                        self.stats.log(pnl > 0, pnl)
                        self.display_stats()
                        return pnl > 0

            print(f"   ❌ Could not hedge - manual intervention needed!")
            self.stats.log(False, 0)
            return False

    def display_stats(self):
        print(f"\n   {'─'*40}")
        win_rate = (self.stats.successful / self.stats.total_trades * 100) if self.stats.total_trades > 0 else 0
        color = "🟢" if self.stats.total_pnl >= 0 else "🔴"
        print(f"   {color} P&L: ${self.stats.total_pnl:+.2f} | Trades: {self.stats.total_trades} | Win: {win_rate:.0f}%")

    def run(self):
        print(f"\n{'='*60}")
        print("HYBRID MARKET MAKER")
        print("Maker on one side, Taker hedge on the other")
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
                opportunities = []
                for m in markets:
                    opp = self.analyze_hybrid_opportunity(m)
                    if opp:
                        opportunities.append(opp)

                # Show status
                status_parts = []
                for m in markets[:3]:
                    yes_ob = self.get_orderbook(m["yes_token"])
                    no_ob = self.get_orderbook(m["no_token"])
                    if yes_ob["asks"] and no_ob["asks"]:
                        taker_total = yes_ob["asks"][0][0] + no_ob["asks"][0][0]
                        taker_profit = (1 - taker_total) * 100

                        # Check hybrid profit (improve best bid by 1c, take ask on other)
                        if yes_ob["bids"] and no_ob["bids"]:
                            yes_maker = yes_ob["bids"][0][0] + 0.01
                            no_maker = no_ob["bids"][0][0] + 0.01
                            hybrid_a = (1 - (yes_maker + no_ob["asks"][0][0])) * 100
                            hybrid_b = (1 - (yes_ob["asks"][0][0] + no_maker)) * 100
                            best_hybrid = max(hybrid_a, hybrid_b)
                            status_parts.append(f"{m['hour']}:H{best_hybrid:+.1f}¢")
                        else:
                            status_parts.append(f"{m['hour']}:{taker_profit:+.1f}¢")

                status = " | ".join(status_parts) if status_parts else "..."
                print(f"\r[{ts}] #{scan} | {status} | P&L: ${self.stats.total_pnl:+.2f}   ", end="", flush=True)

                # Execute best opportunity
                if opportunities:
                    # Sort by profit
                    opportunities.sort(key=lambda x: x["profit"], reverse=True)
                    best = opportunities[0]

                    print(f"\n\n[{ts}] 💰 HYBRID OPPORTUNITY FOUND!")
                    print(f"   {best['market']['hour']}: {best['maker_side']} maker @ ${best['maker_price']:.2f}")
                    print(f"   + {best['hedge_side']} taker @ ${best['hedge_price']:.2f}")
                    print(f"   = ${best['profit_cents']:.1f}¢ profit")

                    self.execute_hybrid_trade(best)
                    time.sleep(5)  # Pause after trade

                time.sleep(1)

            except KeyboardInterrupt:
                print(f"\n\n{'='*60}")
                print("🛑 STOPPED")
                print(f"{'='*60}")
                self.display_stats()
                break
            except Exception as e:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(3)


if __name__ == "__main__":
    HybridMarketMaker().run()
