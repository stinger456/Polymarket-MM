#!/usr/bin/env python3
"""
REAL 15-MINUTE BTC MARKET MAKER

This is an ACTIVE market maker that:
1. Posts bids on BOTH YES and NO sides
2. Earns the bid-ask spread when filled
3. Earns maker rebates (20% of taker fees)
4. Manages inventory to stay delta-neutral
5. Rebalances when one side gets too heavy

This is how real MMs make money - not waiting for arbitrage.
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
from py_clob_client.order_builder.constants import BUY, SELL

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# ============== CONFIG ==============
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
SPREAD_FROM_MID = 0.02  # Post orders 2¢ from mid price
MAX_INVENTORY_IMBALANCE = 10  # Max shares imbalance before rebalancing
QUOTE_REFRESH_SECONDS = 5  # How often to refresh quotes
REBALANCE_THRESHOLD = 0.6  # Rebalance if inventory skew > 60%
# ====================================


class RealMarketMaker:
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

        # Track our positions and orders
        self.yes_position = 0.0
        self.no_position = 0.0
        self.yes_avg_price = 0.0
        self.no_avg_price = 0.0

        self.active_orders = {}  # order_id -> order_info

        # Stats
        self.total_pnl = 0.0
        self.realized_pnl = 0.0
        self.maker_volume = 0.0
        self.total_fills = 0
        self.start_time = time.time()

        # Current market being traded
        self.current_market = None

        print(f"✓ Connected: {safe_address[:20]}...")

    def get_15min_markets(self) -> List[Dict]:
        """Get active 15-minute BTC markets using correct slug pattern."""
        markets = []
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        # 15-minute markets use Unix timestamp in slug: btc-updown-15m-{timestamp}
        # Try current and next few 15-minute intervals
        for offset in range(8):  # Check next 2 hours
            # Round to 15-minute intervals
            dt = now + timedelta(minutes=offset * 15)
            dt = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)

            # Convert to Unix timestamp
            timestamp = int(dt.timestamp())

            slug = f"btc-updown-15m-{timestamp}"

            try:
                with httpx.Client(timeout=10) as http:
                    resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                    events = resp.json()

                    if events and len(events) > 0:
                        event = events[0]
                        for mkt in event.get("markets", []):
                            if mkt.get("closed"):
                                continue
                            tokens = mkt.get("clobTokenIds", [])
                            if isinstance(tokens, str):
                                tokens = json.loads(tokens)
                            if len(tokens) >= 2:
                                end_dt = dt + timedelta(minutes=15)
                                mins_left = (end_dt - now).total_seconds() / 60

                                if mins_left > 2:  # Skip if about to expire
                                    markets.append({
                                        "yes_token": tokens[0],
                                        "no_token": tokens[1],
                                        "question": mkt.get("question", "")[:40],
                                        "time": dt.strftime("%H:%M"),
                                        "mins_left": mins_left,
                                        "slug": slug,
                                    })
            except Exception as e:
                pass

        return markets

    def get_ob(self, token_id: str) -> Dict:
        """Get orderbook."""
        try:
            ob = self.client.get_order_book(token_id)
            bids = sorted([(float(b.price), float(b.size)) for b in (ob.bids or [])], reverse=True)
            asks = sorted([(float(a.price), float(a.size)) for a in (ob.asks or [])])
            return {"bids": bids[:10], "asks": asks[:10]}
        except:
            return {"bids": [], "asks": []}

    def get_mid_price(self, ob: Dict) -> Optional[float]:
        """Calculate mid price from orderbook."""
        if ob["bids"] and ob["asks"]:
            return (ob["bids"][0][0] + ob["asks"][0][0]) / 2
        return None

    def cancel_all_orders(self):
        """Cancel all active orders."""
        for order_id in list(self.active_orders.keys()):
            try:
                self.client.cancel(order_id)
            except:
                pass
        self.active_orders = {}

    def place_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        """Place a maker order."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id")
            if order_id:
                self.active_orders[order_id] = {
                    "token_id": token_id,
                    "price": price,
                    "size": size,
                    "side": side,
                    "placed_at": time.time()
                }
            return order_id
        except Exception as e:
            print(f"\n   ❌ Order failed: {e}")
            return None

    def check_fills(self):
        """Check for filled orders and update positions."""
        filled_orders = []

        for order_id, order_info in list(self.active_orders.items()):
            try:
                order = self.client.get_order(order_id)
                filled = float(order.get("size_matched", 0))

                if filled > 0:
                    price = order_info["price"]
                    side = order_info["side"]
                    token_id = order_info["token_id"]

                    # Determine if this is YES or NO
                    is_yes = token_id == self.current_market["yes_token"]

                    if side == BUY:
                        if is_yes:
                            self.yes_position += filled
                            self.yes_avg_price = price  # Simplified
                        else:
                            self.no_position += filled
                            self.no_avg_price = price
                        self.maker_volume += filled * price
                    else:  # SELL
                        if is_yes:
                            # Realize P&L
                            pnl = (price - self.yes_avg_price) * filled
                            self.realized_pnl += pnl
                            self.yes_position -= filled
                        else:
                            pnl = (price - self.no_avg_price) * filled
                            self.realized_pnl += pnl
                            self.no_position -= filled
                        self.maker_volume += filled * price

                    self.total_fills += 1
                    filled_orders.append((order_id, filled, side, "YES" if is_yes else "NO", price))

                    # Remove if fully filled
                    if filled >= order_info["size"] * 0.95:
                        del self.active_orders[order_id]

            except:
                pass

        return filled_orders

    def calculate_inventory_skew(self) -> float:
        """Calculate how imbalanced our inventory is. 0 = balanced, 1 = all YES, -1 = all NO."""
        total = self.yes_position + self.no_position
        if total == 0:
            return 0
        return (self.yes_position - self.no_position) / total

    def post_quotes(self, market: Dict):
        """Post bid orders on both YES and NO sides."""
        yes_ob = self.get_ob(market["yes_token"])
        no_ob = self.get_ob(market["no_token"])

        if not yes_ob["bids"] or not yes_ob["asks"] or not no_ob["bids"] or not no_ob["asks"]:
            print(f"\n   ⚠️ Incomplete orderbook - YES bids:{len(yes_ob['bids'])} asks:{len(yes_ob['asks'])} | NO bids:{len(no_ob['bids'])} asks:{len(no_ob['asks'])}")
            return None

        # Calculate mid prices
        yes_mid = self.get_mid_price(yes_ob)
        no_mid = self.get_mid_price(no_ob)

        if not yes_mid or not no_mid:
            return

        # Calculate our bid prices (slightly below mid to be competitive)
        # Adjust based on inventory - if we have too much YES, bid lower for YES
        skew = self.calculate_inventory_skew()

        yes_adjustment = -skew * 0.01  # If skew positive (too much YES), lower YES bid
        no_adjustment = skew * 0.01    # If skew positive (too much YES), raise NO bid

        yes_bid_price = round(yes_mid - SPREAD_FROM_MID + yes_adjustment, 2)
        no_bid_price = round(no_mid - SPREAD_FROM_MID + no_adjustment, 2)

        # Make sure prices are valid
        yes_bid_price = max(0.01, min(0.99, yes_bid_price))
        no_bid_price = max(0.01, min(0.99, no_bid_price))

        # Also make sure YES + NO bids < 1.00 (so we profit when both fill)
        if yes_bid_price + no_bid_price >= 0.99:
            # Reduce both proportionally
            total = yes_bid_price + no_bid_price
            yes_bid_price = round(yes_bid_price * 0.98 / total, 2)
            no_bid_price = round(no_bid_price * 0.98 / total, 2)

        # Cancel old orders and place new ones
        self.cancel_all_orders()

        # Place YES bid
        yes_order = self.place_order(market["yes_token"], yes_bid_price, ORDER_SIZE, BUY)

        # Place NO bid
        no_order = self.place_order(market["no_token"], no_bid_price, ORDER_SIZE, BUY)

        return {
            "yes_bid": yes_bid_price,
            "no_bid": no_bid_price,
            "total": yes_bid_price + no_bid_price,
            "profit_if_both_fill": 1.0 - yes_bid_price - no_bid_price,
            "yes_order": yes_order,
            "no_order": no_order,
        }

    def check_and_hedge(self, market: Dict):
        """If we have imbalanced inventory, try to hedge."""
        skew = self.calculate_inventory_skew()

        if abs(skew) < REBALANCE_THRESHOLD:
            return  # No rebalance needed

        if skew > REBALANCE_THRESHOLD:
            # Too much YES, need to buy NO or sell YES
            excess = self.yes_position - self.no_position
            if excess > 2:
                print(f"\n   ⚖️ REBALANCING: Too much YES ({self.yes_position:.0f}), hedging with NO")
                no_ob = self.get_ob(market["no_token"])
                if no_ob["asks"]:
                    hedge_price = no_ob["asks"][0][0] + 0.01
                    hedge_size = min(excess / 2, ORDER_SIZE)
                    # Buy NO to hedge
                    try:
                        args = OrderArgs(token_id=market["no_token"], price=hedge_price,
                                        size=hedge_size, side=BUY)
                        signed = self.client.create_order(args)
                        self.client.post_order(signed, OrderType.FOK)
                    except:
                        pass

        elif skew < -REBALANCE_THRESHOLD:
            # Too much NO, need to buy YES or sell NO
            excess = self.no_position - self.yes_position
            if excess > 2:
                print(f"\n   ⚖️ REBALANCING: Too much NO ({self.no_position:.0f}), hedging with YES")
                yes_ob = self.get_ob(market["yes_token"])
                if yes_ob["asks"]:
                    hedge_price = yes_ob["asks"][0][0] + 0.01
                    hedge_size = min(excess / 2, ORDER_SIZE)
                    # Buy YES to hedge
                    try:
                        args = OrderArgs(token_id=market["yes_token"], price=hedge_price,
                                        size=hedge_size, side=BUY)
                        signed = self.client.create_order(args)
                        self.client.post_order(signed, OrderType.FOK)
                    except:
                        pass

    def calculate_unrealized_pnl(self, market: Dict) -> float:
        """Calculate unrealized P&L based on current positions."""
        if self.yes_position == 0 and self.no_position == 0:
            return 0

        yes_ob = self.get_ob(market["yes_token"])
        no_ob = self.get_ob(market["no_token"])

        unrealized = 0

        if self.yes_position > 0 and yes_ob["bids"]:
            current_yes_price = yes_ob["bids"][0][0]
            unrealized += (current_yes_price - self.yes_avg_price) * self.yes_position

        if self.no_position > 0 and no_ob["bids"]:
            current_no_price = no_ob["bids"][0][0]
            unrealized += (current_no_price - self.no_avg_price) * self.no_position

        return unrealized

    def estimate_rebates(self) -> float:
        """Estimate maker rebates earned."""
        # Rough estimate: ~1% average fee rate, 20% rebate
        return self.maker_volume * 0.01 * 0.20

    def show_status(self, quotes: Dict = None):
        """Show current status."""
        runtime = (time.time() - self.start_time) / 60
        skew = self.calculate_inventory_skew()
        rebates = self.estimate_rebates()

        pos_str = f"YES:{self.yes_position:.0f} NO:{self.no_position:.0f}"
        skew_str = f"{'→YES' if skew > 0 else '→NO' if skew < 0 else 'BAL'}"

        if quotes:
            quote_str = f"Bid Y${quotes['yes_bid']:.2f} N${quotes['no_bid']:.2f}={quotes['total']:.2f}"
        else:
            quote_str = "No quotes"

        total_pnl = self.realized_pnl + rebates

        return f"{quote_str} | {pos_str} {skew_str} | Fills:{self.total_fills} | P&L:${total_pnl:+.2f}"

    def show_dashboard(self, market: Dict):
        """Full dashboard."""
        runtime = (time.time() - self.start_time) / 60
        unrealized = self.calculate_unrealized_pnl(market)
        rebates = self.estimate_rebates()
        total_pnl = self.realized_pnl + unrealized + rebates

        print(f"\n\n{'='*60}")
        print(f"📊 MARKET MAKER DASHBOARD")
        print(f"{'='*60}")
        print(f"   Runtime:       {runtime:.1f} minutes")
        print(f"   Fills:         {self.total_fills}")
        print(f"   Volume:        ${self.maker_volume:.2f}")
        print(f"   {'─'*40}")
        print(f"   Positions:     YES: {self.yes_position:.0f} @ ${self.yes_avg_price:.2f}")
        print(f"                  NO:  {self.no_position:.0f} @ ${self.no_avg_price:.2f}")
        print(f"   Skew:          {self.calculate_inventory_skew()*100:+.0f}%")
        print(f"   {'─'*40}")
        print(f"   Realized P&L:  ${self.realized_pnl:+.2f}")
        print(f"   Unrealized:    ${unrealized:+.2f}")
        print(f"   Est. Rebates:  ${rebates:+.2f}")
        print(f"   {'─'*40}")
        color = "🟢" if total_pnl >= 0 else "🔴"
        print(f"   {color} TOTAL P&L:   ${total_pnl:+.2f}")
        print(f"{'='*60}\n")

    def run(self):
        """Main loop."""
        print(f"\n{'='*60}")
        print("🏦 REAL 15-MINUTE MARKET MAKER")
        print("Posts orders on BOTH sides, earns spread + rebates")
        print(f"Order size: ${ORDER_SIZE} | Spread: {SPREAD_FROM_MID*100:.0f}¢ from mid")
        print("Press Ctrl+C to stop (will cancel all orders)")
        print(f"{'='*60}")

        scan = 0
        last_quote_time = 0
        last_dashboard = time.time()
        verbose_interval = 30  # Show verbose info every 30 seconds
        last_verbose = 0

        try:
            while True:
                scan += 1
                ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

                # Dashboard every 5 minutes
                if time.time() - last_dashboard > 300 and self.current_market:
                    self.show_dashboard(self.current_market)
                    last_dashboard = time.time()

                # Get markets
                markets = self.get_15min_markets()

                if not markets:
                    print(f"\r[{ts}] #{scan} | Searching for 15-min markets...   ", end="", flush=True)
                    time.sleep(5)
                    continue

                # Use first market (soonest expiry with time left)
                market = markets[0]
                self.current_market = market

                # Verbose output every 30 seconds
                if time.time() - last_verbose > verbose_interval:
                    print(f"\n\n{'─'*60}")
                    print(f"📊 MARKET: {market['question']}")
                    print(f"   Time: {market['time']} ET | {market['mins_left']:.1f} mins left")
                    print(f"   Slug: {market['slug']}")

                    # Show orderbook
                    yes_ob = self.get_ob(market["yes_token"])
                    no_ob = self.get_ob(market["no_token"])

                    print(f"\n   📈 YES Orderbook:")
                    if yes_ob["bids"]:
                        print(f"      Best Bid: ${yes_ob['bids'][0][0]:.2f} x {yes_ob['bids'][0][1]:.0f}")
                    else:
                        print(f"      Best Bid: EMPTY")
                    if yes_ob["asks"]:
                        print(f"      Best Ask: ${yes_ob['asks'][0][0]:.2f} x {yes_ob['asks'][0][1]:.0f}")
                    else:
                        print(f"      Best Ask: EMPTY")

                    print(f"\n   📉 NO Orderbook:")
                    if no_ob["bids"]:
                        print(f"      Best Bid: ${no_ob['bids'][0][0]:.2f} x {no_ob['bids'][0][1]:.0f}")
                    else:
                        print(f"      Best Bid: EMPTY")
                    if no_ob["asks"]:
                        print(f"      Best Ask: ${no_ob['asks'][0][0]:.2f} x {no_ob['asks'][0][1]:.0f}")
                    else:
                        print(f"      Best Ask: EMPTY")

                    # Show implied probability
                    if yes_ob["asks"] and no_ob["asks"]:
                        taker_cost = yes_ob["asks"][0][0] + no_ob["asks"][0][0]
                        print(f"\n   💰 Taker total: ${taker_cost:.2f} (profit if <$1: ${1-taker_cost:.2f})")

                    if yes_ob["bids"] and no_ob["bids"]:
                        maker_revenue = yes_ob["bids"][0][0] + no_ob["bids"][0][0]
                        print(f"   💰 If we post at best bid: ${maker_revenue:.2f}")

                    print(f"{'─'*60}\n")
                    last_verbose = time.time()

                # Check for fills
                fills = self.check_fills()
                for fill in fills:
                    order_id, size, side, token_type, price = fill
                    print(f"\n   🎯 FILL: {side} {size:.0f} {token_type} @ ${price:.2f}")

                # Check if we need to rebalance
                self.check_and_hedge(market)

                # Post/refresh quotes every QUOTE_REFRESH_SECONDS
                quotes = None
                if time.time() - last_quote_time > QUOTE_REFRESH_SECONDS:
                    quotes = self.post_quotes(market)
                    last_quote_time = time.time()

                    # Show what we posted
                    if quotes and quotes.get("yes_order") and quotes.get("no_order"):
                        pass  # Orders posted successfully
                    elif quotes:
                        print(f"\n   ⚠️ Order posting issue - YES:{quotes.get('yes_order')} NO:{quotes.get('no_order')}")

                # Show status
                status = self.show_status(quotes)
                print(f"\r[{ts}] #{scan} | {market['time']} | {status}   ", end="", flush=True)

                time.sleep(1)

        except KeyboardInterrupt:
            print(f"\n\n{'='*60}")
            print("🛑 STOPPING - Cancelling all orders...")
            print(f"{'='*60}")
            self.cancel_all_orders()
            if self.current_market:
                self.show_dashboard(self.current_market)
            print("All orders cancelled. Check Polymarket for any open positions.")


if __name__ == "__main__":
    RealMarketMaker().run()
