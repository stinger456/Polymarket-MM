#!/usr/bin/env python3
"""
Delta Neutral Market Maker

Strategy:
1. Post bids on BOTH YES and NO sides
2. Prices set so YES_bid + NO_bid < $1.00 (guaranteed profit if both fill)
3. When one side fills, AGGRESSIVELY fill the other to stay hedged
4. Track positions to ensure we're always delta neutral

Key insight: We're MAKERS, not TAKERS. We post and wait.
When we get hit, we immediately hedge the other side.
"""

import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
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
from py_clob_client.order_builder.constants import BUY, SELL

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# Config
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
TARGET_EDGE_CENTS = 2  # Target 2 cent edge ($0.02)
MAX_POSITION_IMBALANCE = ORDER_SIZE  # Max unhedged position


@dataclass
class Position:
    yes_shares: float = 0
    no_shares: float = 0
    yes_cost: float = 0
    no_cost: float = 0

    @property
    def delta(self) -> float:
        """Positive = long YES, Negative = long NO"""
        return self.yes_shares - self.no_shares

    @property
    def is_hedged(self) -> bool:
        return abs(self.delta) < 0.1

    @property
    def total_cost(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def locked_profit(self) -> float:
        """Profit locked in from hedged positions"""
        hedged = min(self.yes_shares, self.no_shares)
        return hedged - self.total_cost if hedged > 0 else 0


class DeltaNeutralMM:
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

        # State
        self.position = Position()
        self.yes_order_id = None
        self.no_order_id = None
        self.total_pnl = 0.0
        self.trades_completed = 0

        print(f"✓ Connected: {safe_address[:20]}...")

    def get_market(self) -> Optional[Dict]:
        """Get the best active BTC hourly market."""
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
                                if mins_left > 10:  # Only trade markets with >10 mins left
                                    return {
                                        "yes_token": tokens[0],
                                        "no_token": tokens[1],
                                        "hour": h.upper(),
                                        "mins_left": mins_left,
                                    }
            except:
                pass
        return None

    def get_orderbook(self, token_id: str) -> Dict:
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:5], "asks": asks[:5]}
        except:
            return {"bids": [], "asks": []}

    def calculate_quotes(self, market: Dict) -> Tuple[Optional[float], Optional[float], Dict]:
        """Calculate delta-neutral bid prices."""
        yes_ob = self.get_orderbook(market["yes_token"])
        no_ob = self.get_orderbook(market["no_token"])

        if not yes_ob["bids"] or not yes_ob["asks"] or not no_ob["bids"] or not no_ob["asks"]:
            return None, None, {"error": "No orderbook"}

        yes_bid, _ = yes_ob["bids"][0]
        yes_ask, _ = yes_ob["asks"][0]
        no_bid, _ = no_ob["bids"][0]
        no_ask, _ = no_ob["asks"][0]

        yes_mid = (yes_bid + yes_ask) / 2
        no_mid = (no_bid + no_ask) / 2

        # Our strategy: bid slightly below mid on both sides
        # Ensure total < $1.00 for profit
        target_total = 1.0 - (TARGET_EDGE_CENTS / 100)  # e.g., $0.98 for 2 cent edge

        # Distribute based on current mids
        total_mid = yes_mid + no_mid
        if total_mid > 0:
            yes_ratio = yes_mid / total_mid
            no_ratio = no_mid / total_mid
        else:
            yes_ratio = no_ratio = 0.5

        our_yes_bid = round(target_total * yes_ratio, 2)
        our_no_bid = round(target_total * no_ratio, 2)

        # Don't bid above current best bid (we want to be competitive but not overpay)
        our_yes_bid = min(our_yes_bid, yes_bid + 0.01)
        our_no_bid = min(our_no_bid, no_bid + 0.01)

        # Ensure minimum prices
        our_yes_bid = max(0.01, our_yes_bid)
        our_no_bid = max(0.01, our_no_bid)

        info = {
            "yes_bid": yes_bid, "yes_ask": yes_ask, "yes_mid": yes_mid,
            "no_bid": no_bid, "no_ask": no_ask, "no_mid": no_mid,
            "our_yes_bid": our_yes_bid, "our_no_bid": our_no_bid,
            "total": our_yes_bid + our_no_bid,
            "edge": 1.0 - (our_yes_bid + our_no_bid),
        }

        return our_yes_bid, our_no_bid, info

    def place_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"      Order error: {e}")
            return None

    def cancel_order(self, order_id: str):
        if order_id:
            try:
                self.client.cancel(order_id)
            except:
                pass

    def check_order_fill(self, order_id: str) -> Tuple[float, str]:
        """Returns (filled_size, status)"""
        if not order_id:
            return 0, "none"
        try:
            order = self.client.get_order(order_id)
            filled = float(order.get("size_matched", 0))
            status = order.get("status", "unknown")
            return filled, status
        except:
            return 0, "error"

    def hedge_imbalance(self, market: Dict):
        """If we have an imbalanced position, aggressively hedge it."""
        if self.position.is_hedged:
            return

        delta = self.position.delta

        if delta > 0:
            # Long YES, need to buy NO
            no_ob = self.get_orderbook(market["no_token"])
            if no_ob["asks"]:
                # Buy at ask for immediate fill
                ask_price = no_ob["asks"][0][0]
                size_needed = delta
                print(f"   ⚡ HEDGING: Buying {size_needed:.1f} NO @ ${ask_price:.2f} (crossing spread)")
                order_id = self.place_order(market["no_token"], ask_price, size_needed, BUY)
                if order_id:
                    self.position.no_shares += size_needed
                    self.position.no_cost += ask_price * size_needed

        elif delta < 0:
            # Long NO, need to buy YES
            yes_ob = self.get_orderbook(market["yes_token"])
            if yes_ob["asks"]:
                ask_price = yes_ob["asks"][0][0]
                size_needed = abs(delta)
                print(f"   ⚡ HEDGING: Buying {size_needed:.1f} YES @ ${ask_price:.2f} (crossing spread)")
                order_id = self.place_order(market["yes_token"], ask_price, size_needed, BUY)
                if order_id:
                    self.position.yes_shares += size_needed
                    self.position.yes_cost += ask_price * size_needed

    def display_status(self, market: Dict, info: Dict):
        ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")
        delta_str = f"{self.position.delta:+.1f}" if abs(self.position.delta) > 0.1 else "0 (hedged)"

        print(f"\r[{ts}] {market['hour']} | "
              f"YES: ${info.get('our_yes_bid', 0):.2f} | "
              f"NO: ${info.get('our_no_bid', 0):.2f} | "
              f"Edge: {info.get('edge', 0)*100:.1f}¢ | "
              f"Delta: {delta_str} | "
              f"P&L: ${self.total_pnl:+.2f}   ", end="", flush=True)

    def run(self):
        print(f"\n{'='*60}")
        print("DELTA NEUTRAL MARKET MAKER")
        print(f"Order size: ${ORDER_SIZE} | Target edge: {TARGET_EDGE_CENTS}¢")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}\n")

        current_market = None
        last_yes_price = None
        last_no_price = None

        while True:
            try:
                # Get market
                market = self.get_market()
                if not market:
                    print("\rNo active market found. Waiting...", end="", flush=True)
                    time.sleep(5)
                    continue

                # If market changed, cancel old orders and reset
                if current_market is None or market["hour"] != current_market["hour"]:
                    print(f"\n\n📊 New market: {market['hour']} ET ({market['mins_left']:.0f} mins left)")
                    self.cancel_order(self.yes_order_id)
                    self.cancel_order(self.no_order_id)
                    self.yes_order_id = None
                    self.no_order_id = None
                    current_market = market

                # Calculate quotes
                yes_price, no_price, info = self.calculate_quotes(market)
                if yes_price is None:
                    time.sleep(1)
                    continue

                # Check if our orders got filled
                yes_filled, yes_status = self.check_order_fill(self.yes_order_id)
                no_filled, no_status = self.check_order_fill(self.no_order_id)

                # Track fills
                if yes_filled > 0 and self.yes_order_id:
                    print(f"\n   ✅ YES FILLED: {yes_filled:.1f} @ ${last_yes_price:.2f}")
                    self.position.yes_shares += yes_filled
                    self.position.yes_cost += yes_filled * last_yes_price
                    self.yes_order_id = None  # Will place new order

                if no_filled > 0 and self.no_order_id:
                    print(f"\n   ✅ NO FILLED: {no_filled:.1f} @ ${last_no_price:.2f}")
                    self.position.no_shares += no_filled
                    self.position.no_cost += no_filled * last_no_price
                    self.no_order_id = None

                # If we got a fill, immediately hedge
                if not self.position.is_hedged:
                    self.hedge_imbalance(market)

                    # If now hedged, calculate P&L
                    if self.position.is_hedged and self.position.yes_shares > 0:
                        hedged_size = min(self.position.yes_shares, self.position.no_shares)
                        payout = hedged_size  # $1.00 per hedged pair
                        cost = self.position.total_cost
                        pnl = payout - cost
                        self.total_pnl += pnl
                        self.trades_completed += 1
                        print(f"\n   💰 HEDGED POSITION COMPLETE!")
                        print(f"      Shares: {hedged_size:.1f}")
                        print(f"      Cost: ${cost:.2f}")
                        print(f"      Payout: ${payout:.2f}")
                        print(f"      P&L: ${pnl:+.2f}")
                        print(f"      Total P&L: ${self.total_pnl:+.2f}")

                        # Reset position
                        self.position = Position()

                # Place/update orders if needed
                price_changed = (yes_price != last_yes_price or no_price != last_no_price)

                # Place YES order if needed
                if self.yes_order_id is None or (price_changed and yes_status != "matched"):
                    self.cancel_order(self.yes_order_id)
                    self.yes_order_id = self.place_order(market["yes_token"], yes_price, ORDER_SIZE, BUY)
                    last_yes_price = yes_price

                # Place NO order if needed
                if self.no_order_id is None or (price_changed and no_status != "matched"):
                    self.cancel_order(self.no_order_id)
                    self.no_order_id = self.place_order(market["no_token"], no_price, ORDER_SIZE, BUY)
                    last_no_price = no_price

                # Display status
                self.display_status(market, info)

                time.sleep(2)

            except KeyboardInterrupt:
                print(f"\n\n{'='*60}")
                print("🛑 STOPPING")
                print(f"{'='*60}")
                print(f"   Cancelling orders...")
                self.cancel_order(self.yes_order_id)
                self.cancel_order(self.no_order_id)
                print(f"   Trades completed: {self.trades_completed}")
                print(f"   Total P&L: ${self.total_pnl:+.2f}")
                if not self.position.is_hedged:
                    print(f"   ⚠️  WARNING: Unhedged position! Delta: {self.position.delta:+.1f}")
                break
            except Exception as e:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(3)


if __name__ == "__main__":
    DeltaNeutralMM().run()
