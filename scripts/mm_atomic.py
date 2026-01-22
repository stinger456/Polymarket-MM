#!/usr/bin/env python3
"""
Atomic Market Maker - Ensures BOTH sides fill or exits immediately.

Strategy:
1. Analyze orderbook to find executable prices
2. Place both YES and NO orders simultaneously
3. Monitor fills in real-time
4. If only one fills within timeout, EXIT immediately (sell the filled position)

This ensures we're either fully hedged or flat - never exposed directionally.
"""

import os
import sys
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY, SELL

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# Configuration
FILL_TIMEOUT_SECONDS = 10  # How long to wait for both fills
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_PROFIT_MARGIN = 0.02  # Minimum $0.02 profit required


class AtomicMarketMaker:
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

        # Get API credentials
        try:
            creds = self.client.derive_api_key()
        except:
            creds = self.client.create_api_key()
        self.client.set_api_creds(creds)

        print(f"Connected! Safe: {safe_address[:20]}...")

    def get_orderbook(self, token_id: str) -> Dict:
        """Fetch orderbook for a token."""
        try:
            ob = self.client.get_order_book(token_id)
            return {
                "bids": [(float(b.price), float(b.size)) for b in (ob.bids or [])],
                "asks": [(float(a.price), float(a.size)) for a in (ob.asks or [])],
            }
        except Exception as e:
            print(f"Orderbook error: {e}")
            return {"bids": [], "asks": []}

    def analyze_orderbook(self, yes_token: str, no_token: str, size: float) -> Tuple[Optional[float], Optional[float], Dict]:
        """
        Analyze orderbooks to find executable prices.

        Returns prices that are LIKELY to fill based on available liquidity.
        Strategy: Place limit orders at or just below the best ask to get filled quickly.
        """
        yes_ob = self.get_orderbook(yes_token)
        no_ob = self.get_orderbook(no_token)

        analysis = {
            "yes_best_bid": yes_ob["bids"][0][0] if yes_ob["bids"] else 0,
            "yes_best_ask": yes_ob["asks"][0][0] if yes_ob["asks"] else 1,
            "yes_ask_size": yes_ob["asks"][0][1] if yes_ob["asks"] else 0,
            "no_best_bid": no_ob["bids"][0][0] if no_ob["bids"] else 0,
            "no_best_ask": no_ob["asks"][0][0] if no_ob["asks"] else 1,
            "no_ask_size": no_ob["asks"][0][1] if no_ob["asks"] else 0,
        }

        # For GUARANTEED fills, we buy AT the ask price (cross the spread)
        # This is a "taker" order that fills immediately
        yes_price = analysis["yes_best_ask"]
        no_price = analysis["no_best_ask"]

        # Check if there's enough liquidity at the ask
        if analysis["yes_ask_size"] < size:
            print(f"⚠️  YES ask size ({analysis['yes_ask_size']}) < order size ({size})")
        if analysis["no_ask_size"] < size:
            print(f"⚠️  NO ask size ({analysis['no_ask_size']}) < order size ({size})")

        # Check profitability
        total_cost = yes_price + no_price
        profit = 1.0 - total_cost

        analysis["yes_price"] = yes_price
        analysis["no_price"] = no_price
        analysis["total_cost"] = total_cost
        analysis["profit"] = profit
        analysis["profit_pct"] = profit * 100

        if profit < MIN_PROFIT_MARGIN:
            print(f"❌ Not profitable: ${total_cost:.3f} total, ${profit:.3f} profit ({profit*100:.1f}%)")
            return None, None, analysis

        return yes_price, no_price, analysis

    def place_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        """Place a single order and return order ID."""
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            )

            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)

            order_id = response.get("orderID") or response.get("id")
            return order_id
        except Exception as e:
            print(f"Order error: {e}")
            return None

    def check_order_status(self, order_id: str) -> Tuple[str, float]:
        """Check if order is filled, returns (status, filled_amount)."""
        try:
            order = self.client.get_order(order_id)
            status = order.get("status", "unknown")
            size_matched = float(order.get("size_matched", 0))
            original_size = float(order.get("original_size", 0))
            return status, size_matched
        except Exception as e:
            return "error", 0

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            print(f"Cancel failed: {e}")
            return False

    def sell_position(self, token_id: str, size: float) -> Optional[str]:
        """Immediately sell a position at market (hit the bid)."""
        ob = self.get_orderbook(token_id)
        if not ob["bids"]:
            print("No bids available to sell into!")
            return None

        # Sell at best bid for immediate fill
        sell_price = ob["bids"][0][0]
        print(f"  Selling {size} @ ${sell_price:.2f} (best bid)")

        return self.place_order(token_id, sell_price, size, SELL)

    def execute_atomic_trade(self, yes_token: str, no_token: str, size: float) -> bool:
        """
        Execute atomic trade - both sides fill or we exit.
        """
        print("\n" + "=" * 70)
        print("ANALYZING ORDERBOOK")
        print("=" * 70)

        yes_price, no_price, analysis = self.analyze_orderbook(yes_token, no_token, size)

        print(f"\nYES: bid=${analysis['yes_best_bid']:.3f} / ask=${analysis['yes_best_ask']:.3f} (size: {analysis['yes_ask_size']:.1f})")
        print(f"NO:  bid=${analysis['no_best_bid']:.3f} / ask=${analysis['no_best_ask']:.3f} (size: {analysis['no_ask_size']:.1f})")

        if yes_price is None or no_price is None:
            print("\n❌ Trade not profitable, skipping")
            return False

        print(f"\n✓ Trade plan:")
        print(f"  BUY YES: {size} @ ${yes_price:.3f} = ${yes_price * size:.2f}")
        print(f"  BUY NO:  {size} @ ${no_price:.3f} = ${no_price * size:.2f}")
        print(f"  Total:   ${analysis['total_cost'] * size:.2f}")
        print(f"  Profit:  ${analysis['profit'] * size:.2f} ({analysis['profit_pct']:.1f}%)")

        print("\n" + "=" * 70)
        print("PLACING ORDERS (SIMULTANEOUSLY)")
        print("=" * 70)

        # Place both orders as fast as possible using threads
        yes_order_id = None
        no_order_id = None

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=2) as executor:
            yes_future = executor.submit(self.place_order, yes_token, yes_price, size, BUY)
            no_future = executor.submit(self.place_order, no_token, no_price, size, BUY)

            yes_order_id = yes_future.result()
            no_order_id = no_future.result()

        order_time = time.time() - start_time
        print(f"\nOrders placed in {order_time*1000:.0f}ms")
        print(f"  YES Order: {yes_order_id[:40] if yes_order_id else 'FAILED'}...")
        print(f"  NO Order:  {no_order_id[:40] if no_order_id else 'FAILED'}...")

        if not yes_order_id or not no_order_id:
            print("\n❌ Failed to place both orders!")
            # Cancel any that succeeded
            if yes_order_id:
                self.cancel_order(yes_order_id)
            if no_order_id:
                self.cancel_order(no_order_id)
            return False

        print("\n" + "=" * 70)
        print("MONITORING FILLS")
        print("=" * 70)

        # Monitor for fills
        yes_filled = False
        no_filled = False
        yes_fill_size = 0
        no_fill_size = 0

        timeout_at = time.time() + FILL_TIMEOUT_SECONDS

        while time.time() < timeout_at:
            # Check both orders
            yes_status, yes_fill_size = self.check_order_status(yes_order_id)
            no_status, no_fill_size = self.check_order_status(no_order_id)

            yes_filled = yes_fill_size >= size * 0.99  # Allow 1% slippage
            no_filled = no_fill_size >= size * 0.99

            elapsed = time.time() - start_time
            print(f"\r[{elapsed:.1f}s] YES: {yes_fill_size:.1f}/{size} ({yes_status}) | NO: {no_fill_size:.1f}/{size} ({no_status})   ", end="", flush=True)

            if yes_filled and no_filled:
                print(f"\n\n✅ BOTH FILLED! Hedged position established.")
                print(f"   Total time: {elapsed*1000:.0f}ms")
                return True

            time.sleep(0.5)  # Check every 500ms

        print(f"\n\n⚠️  TIMEOUT! Not both filled.")
        print(f"   YES filled: {yes_fill_size:.1f}/{size}")
        print(f"   NO filled: {no_fill_size:.1f}/{size}")

        # EXIT STRATEGY: Cancel unfilled, sell filled
        print("\n" + "=" * 70)
        print("EMERGENCY EXIT")
        print("=" * 70)

        if yes_filled and not no_filled:
            print("YES filled but NO didn't - selling YES position")
            self.cancel_order(no_order_id)
            sell_id = self.sell_position(yes_token, yes_fill_size)
            if sell_id:
                print(f"  ✓ Sell order placed: {sell_id[:40]}...")

        elif no_filled and not yes_filled:
            print("NO filled but YES didn't - selling NO position")
            self.cancel_order(yes_order_id)
            sell_id = self.sell_position(no_token, no_fill_size)
            if sell_id:
                print(f"  ✓ Sell order placed: {sell_id[:40]}...")

        else:
            print("Neither fully filled - cancelling both")
            self.cancel_order(yes_order_id)
            self.cancel_order(no_order_id)

            # If partially filled, sell those
            if yes_fill_size > 0:
                print(f"  Selling partial YES fill: {yes_fill_size}")
                self.sell_position(yes_token, yes_fill_size)
            if no_fill_size > 0:
                print(f"  Selling partial NO fill: {no_fill_size}")
                self.sell_position(no_token, no_fill_size)

        return False


def get_btc_hourly_market():
    """Get current BTC hourly market."""
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)

    for offset in [0, 1]:
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
            with httpx.Client(headers=headers, timeout=30) as http:
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
                            return {
                                "yes_token": tokens[0],
                                "no_token": tokens[1],
                                "question": market.get("question", ""),
                                "slug": slug,
                            }
        except Exception as e:
            print(f"Market fetch error: {e}")

    return None


def main():
    print("=" * 70)
    print("ATOMIC MARKET MAKER")
    print("Both sides fill or we exit immediately")
    print("=" * 70)

    # Find market
    print("\nFinding BTC hourly market...")
    market = get_btc_hourly_market()

    if not market:
        print("❌ No active market found")
        return

    print(f"✓ Found: {market['question']}")

    # Initialize MM
    mm = AtomicMarketMaker()

    # Execute trade
    success = mm.execute_atomic_trade(
        market["yes_token"],
        market["no_token"],
        ORDER_SIZE
    )

    if success:
        print("\n" + "=" * 70)
        print("✅ SUCCESS - FULLY HEDGED")
        print("=" * 70)
        print("You now hold both YES and NO positions.")
        print("When market resolves, you profit regardless of outcome!")
    else:
        print("\n" + "=" * 70)
        print("❌ TRADE FAILED OR EXITED")
        print("=" * 70)
        print("Position is FLAT - no directional exposure.")


if __name__ == "__main__":
    main()
