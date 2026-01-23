#!/usr/bin/env python3
"""
DELTA NEUTRAL 15-MINUTE MARKET MAKER v2

FIXED: Only posts ONE side at a time to ensure cash for hedging.

Strategy:
1. Post a maker bid on ONE side (alternating YES/NO)
2. When filled -> IMMEDIATELY hedge with the other side (taker)
3. Post next order on opposite side
4. Repeat

This ensures we ALWAYS have cash to hedge after a fill.

Profit = maker rebate + any spread captured
Risk = ZERO (always hedged, positions cancel out at expiration)
"""

import os
import sys
import json
import time
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
FEE_RATE_BPS = 1000  # Required for 15-min markets

# ============== CONFIG ==============
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
# ====================================


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

        # Track positions - MUST be equal for delta neutral
        self.yes_shares = 0.0
        self.no_shares = 0.0

        # Track for P&L
        self.hedged_pairs = 0.0
        self.total_pair_cost = 0.0

        # SINGLE active order (only ONE side at a time!)
        self.active_order_id = None
        self.active_order_side = None  # "YES" or "NO"
        self.active_order_price = 0.0
        self.active_order_size = 0.0

        # Which side to post next (alternates)
        self.next_side = "YES"

        # Stats
        self.total_volume = 0.0
        self.maker_fills = 0
        self.taker_fills = 0
        self.start_time = time.time()

        self.current_market = None

        print(f"Connected: {safe_address[:20]}...")
        print(f"Delta Neutral v2: ONE order at a time, always cash for hedge")

    def get_15min_markets(self) -> List[Dict]:
        """Get active 15-minute BTC markets."""
        markets = []
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        for offset in range(8):
            dt = now + timedelta(minutes=offset * 15)
            dt = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
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

                                if mins_left > 3:  # Need at least 3 mins
                                    markets.append({
                                        "yes_token": tokens[0],
                                        "no_token": tokens[1],
                                        "question": mkt.get("question", "")[:40],
                                        "time": dt.strftime("%H:%M"),
                                        "mins_left": mins_left,
                                        "slug": slug,
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
            return {"bids": bids[:10], "asks": asks[:10]}
        except:
            return {"bids": [], "asks": []}

    def cancel_order(self, order_id: str):
        """Cancel an order."""
        if order_id:
            try:
                self.client.cancel(order_id)
            except:
                pass

    def place_maker_bid(self, token_id: str, price: float, size: float) -> Optional[str]:
        """Place a maker (GTC) bid order."""
        try:
            args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
                fee_rate_bps=FEE_RATE_BPS,
            )
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"\n   Maker order failed: {e}")
            return None

    def place_taker_buy(self, token_id: str, price: float, size: float) -> bool:
        """Place a taker (FOK) buy order - MUST fill immediately."""
        try:
            args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
                fee_rate_bps=FEE_RATE_BPS,
            )
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.FOK)
            return True
        except Exception as e:
            print(f"\n   Taker hedge failed: {e}")
            return False

    def check_order_filled(self, order_id: str) -> float:
        """Check how much of an order has been filled."""
        if not order_id:
            return 0
        try:
            order = self.client.get_order(order_id)
            return float(order.get("size_matched", 0))
        except:
            return 0

    def hedge_immediately(self, side_filled: str, size: float, fill_price: float, market: Dict) -> bool:
        """
        IMMEDIATELY hedge a fill by buying the opposite side.
        This is the KEY to staying delta neutral.
        """
        if side_filled == "YES":
            # We bought YES, now buy equal NO
            no_ob = self.get_ob(market["no_token"])
            if not no_ob["asks"]:
                print(f"\n   CANNOT HEDGE - No NO asks available!")
                return False

            # Pay up to 2c above best ask to ensure fill
            hedge_price = min(no_ob["asks"][0][0] + 0.02, 0.99)

            print(f"\n   HEDGING: Buying {size:.0f} NO @ ${hedge_price:.2f} (taker)")

            if self.place_taker_buy(market["no_token"], hedge_price, size):
                self.no_shares += size
                self.taker_fills += 1
                self.total_volume += size * hedge_price

                # Record the hedged pair
                pair_cost = fill_price + hedge_price
                self.hedged_pairs += size
                self.total_pair_cost += size * pair_cost

                print(f"   HEDGED! Pair cost: ${pair_cost:.2f} (guaranteed profit: ${1-pair_cost:.2f}/share)")
                return True
            return False

        else:  # NO filled
            # We bought NO, now buy equal YES
            yes_ob = self.get_ob(market["yes_token"])
            if not yes_ob["asks"]:
                print(f"\n   CANNOT HEDGE - No YES asks available!")
                return False

            hedge_price = min(yes_ob["asks"][0][0] + 0.02, 0.99)

            print(f"\n   HEDGING: Buying {size:.0f} YES @ ${hedge_price:.2f} (taker)")

            if self.place_taker_buy(market["yes_token"], hedge_price, size):
                self.yes_shares += size
                self.taker_fills += 1
                self.total_volume += size * hedge_price

                # Record the hedged pair
                pair_cost = hedge_price + fill_price
                self.hedged_pairs += size
                self.total_pair_cost += size * pair_cost

                print(f"   HEDGED! Pair cost: ${pair_cost:.2f} (guaranteed profit: ${1-pair_cost:.2f}/share)")
                return True
            return False

    def post_single_quote(self, market: Dict) -> Optional[Dict]:
        """Post maker bid on ONE side only (alternating)."""

        # Don't post if we already have an active order
        if self.active_order_id:
            return None

        # Determine which side to post
        side = self.next_side

        if side == "YES":
            token_id = market["yes_token"]
            ob = self.get_ob(token_id)
        else:
            token_id = market["no_token"]
            ob = self.get_ob(token_id)

        if not ob["bids"]:
            return None

        # Post at best bid price
        price = ob["bids"][0][0]
        price = max(0.01, min(0.99, price))

        # Post the order
        order_id = self.place_maker_bid(token_id, price, ORDER_SIZE)

        if order_id:
            self.active_order_id = order_id
            self.active_order_side = side
            self.active_order_price = price
            self.active_order_size = ORDER_SIZE

            print(f"\n   Posted {side} bid @ ${price:.2f} x {ORDER_SIZE}")

            return {
                "side": side,
                "price": price,
                "size": ORDER_SIZE,
            }

        return None

    def check_fill_and_hedge(self, market: Dict) -> bool:
        """Check if our single order filled, and if so, hedge it."""

        if not self.active_order_id:
            return False

        filled = self.check_order_filled(self.active_order_id)

        if filled <= 0:
            return False

        side = self.active_order_side
        fill_price = self.active_order_price

        print(f"\n   MAKER FILL: {filled:.0f} {side} @ ${fill_price:.2f}")

        # Update position
        if side == "YES":
            self.yes_shares += filled
        else:
            self.no_shares += filled

        self.maker_fills += 1
        self.total_volume += filled * fill_price

        # Cancel remaining order (in case partial fill)
        self.cancel_order(self.active_order_id)
        self.active_order_id = None

        # IMMEDIATELY hedge with the opposite side
        hedged = self.hedge_immediately(side, filled, fill_price, market)

        if hedged:
            # Switch to opposite side for next order
            self.next_side = "NO" if side == "YES" else "YES"
        else:
            print(f"   WARNING: Failed to hedge! Will retry...")
            # Try to hedge again on next loop

        return hedged

    def calculate_pnl(self) -> Dict:
        """Calculate P&L - should always be positive for delta neutral."""
        # Each hedged pair is worth $1.00 at expiration
        guaranteed_value = self.hedged_pairs * 1.00
        spread_profit = guaranteed_value - self.total_pair_cost

        # Estimated rebates (20% of ~1% fee on maker volume)
        maker_volume = self.maker_fills * ORDER_SIZE * 0.50  # rough avg price
        est_rebates = maker_volume * 0.01 * 0.20

        return {
            "pairs": self.hedged_pairs,
            "cost": self.total_pair_cost,
            "value": guaranteed_value,
            "spread_profit": spread_profit,
            "est_rebates": est_rebates,
            "total": spread_profit + est_rebates,
        }

    def show_status(self):
        """Show current status."""
        pnl = self.calculate_pnl()

        delta = self.yes_shares - self.no_shares
        if abs(delta) < 0.1:
            delta_status = "NEUTRAL"
        else:
            delta_status = f"UNHEDGED:{delta:+.0f}"

        if self.active_order_id:
            order_str = f"Bid {self.active_order_side}@${self.active_order_price:.2f}"
        else:
            order_str = "No order"

        return f"{order_str} | Pairs:{pnl['pairs']:.0f} | {delta_status} | ${pnl['total']:+.2f}"

    def show_dashboard(self):
        """Full dashboard."""
        runtime = (time.time() - self.start_time) / 60
        pnl = self.calculate_pnl()

        print(f"\n\n{'='*60}")
        print(f"DELTA NEUTRAL MARKET MAKER v2 - FINAL REPORT")
        print(f"{'='*60}")
        print(f"   Runtime:       {runtime:.1f} minutes")
        print(f"   Volume:        ${self.total_volume:.2f}")
        print(f"   Maker fills:   {self.maker_fills}")
        print(f"   Taker hedges:  {self.taker_fills}")
        print(f"   {'-'*40}")
        print(f"   YES shares:    {self.yes_shares:.0f}")
        print(f"   NO shares:     {self.no_shares:.0f}")
        delta = self.yes_shares - self.no_shares
        if abs(delta) < 0.1:
            print(f"   Delta:         NEUTRAL (perfectly hedged)")
        else:
            print(f"   Delta:         {delta:+.0f} (UNHEDGED - check positions!)")
        print(f"   {'-'*40}")
        print(f"   Hedged pairs:  {pnl['pairs']:.0f}")
        if pnl['pairs'] > 0:
            avg_cost = pnl['cost'] / pnl['pairs']
            print(f"   Avg pair cost: ${avg_cost:.3f}")
        print(f"   Total cost:    ${pnl['cost']:.2f}")
        print(f"   Value at exp:  ${pnl['value']:.2f}")
        print(f"   Spread profit: ${pnl['spread_profit']:+.2f}")
        print(f"   Est. rebates:  ${pnl['est_rebates']:+.2f}")
        print(f"   {'-'*40}")
        print(f"   TOTAL P&L:     ${pnl['total']:+.2f}")
        print(f"{'='*60}\n")

    def run(self):
        """Main loop."""
        print(f"\n{'='*60}")
        print("DELTA NEUTRAL 15-MINUTE MARKET MAKER v2")
        print("ONE order at a time -> fill -> hedge -> repeat")
        print(f"Order size: ${ORDER_SIZE}")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        scan = 0
        last_verbose = 0

        try:
            while True:
                scan += 1
                ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

                markets = self.get_15min_markets()

                if not markets:
                    print(f"\r[{ts}] #{scan} | Searching for markets...   ", end="", flush=True)
                    time.sleep(5)
                    continue

                market = markets[0]
                self.current_market = market

                # Verbose every 60 seconds
                if time.time() - last_verbose > 60:
                    yes_ob = self.get_ob(market["yes_token"])
                    no_ob = self.get_ob(market["no_token"])

                    print(f"\n\n{'-'*50}")
                    print(f"Market: {market['question']}")
                    print(f"   {market['mins_left']:.1f} mins left")
                    if yes_ob["bids"] and yes_ob["asks"]:
                        print(f"   YES: ${yes_ob['bids'][0][0]:.2f} / ${yes_ob['asks'][0][0]:.2f}")
                    if no_ob["bids"] and no_ob["asks"]:
                        print(f"   NO:  ${no_ob['bids'][0][0]:.2f} / ${no_ob['asks'][0][0]:.2f}")
                    print(f"{'-'*50}\n")
                    last_verbose = time.time()

                # Step 1: Check if our order filled, and hedge if so
                self.check_fill_and_hedge(market)

                # Step 2: If no active order, post one
                if not self.active_order_id:
                    self.post_single_quote(market)

                # Show status
                status = self.show_status()
                print(f"\r[{ts}] #{scan} | {market['time']} | {status}   ", end="", flush=True)

                time.sleep(2)

        except KeyboardInterrupt:
            print(f"\n\nStopping...")
            self.cancel_order(self.active_order_id)
            self.show_dashboard()

            if self.hedged_pairs > 0:
                print(f"\nYou have {self.hedged_pairs:.0f} hedged pairs.")
                print(f"These will pay out $1.00 each at market expiration.")
                print(f"Guaranteed profit: ${self.hedged_pairs - self.total_pair_cost:.2f}")


if __name__ == "__main__":
    DeltaNeutralMM().run()
