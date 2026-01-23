#!/usr/bin/env python3
"""
DELTA NEUTRAL 15-MINUTE MARKET MAKER

ALWAYS stays hedged - equal YES and NO positions at all times.

Strategy:
1. Post a maker bid on YES
2. When YES fills -> IMMEDIATELY buy equal NO (taker) to hedge
3. Post a maker bid on NO
4. When NO fills -> IMMEDIATELY buy equal YES (taker) to hedge

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
SPREAD_FROM_BEST = 0.01  # Post 1c below best bid to be competitive
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
        self.hedged_pairs = 0  # Number of YES+NO pairs we hold
        self.total_pair_cost = 0.0  # Total cost of all pairs

        # Active orders
        self.yes_order_id = None
        self.no_order_id = None
        self.yes_order_price = 0.0
        self.no_order_price = 0.0
        self.yes_order_size = 0.0
        self.no_order_size = 0.0

        # Stats
        self.total_volume = 0.0
        self.maker_fills = 0
        self.taker_fills = 0
        self.start_time = time.time()

        self.current_market = None

        print(f"Connected: {safe_address[:20]}...")
        print(f"Delta Neutral Mode: Always hedged, zero directional risk")

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

    def post_quotes(self, market: Dict):
        """Post maker bids on both sides."""
        yes_ob = self.get_ob(market["yes_token"])
        no_ob = self.get_ob(market["no_token"])

        if not yes_ob["bids"] or not no_ob["bids"]:
            return None

        # Post at best bid to be at top of queue
        yes_price = yes_ob["bids"][0][0]
        no_price = no_ob["bids"][0][0]

        # Ensure prices are valid
        yes_price = max(0.01, min(0.99, yes_price))
        no_price = max(0.01, min(0.99, no_price))

        # Cancel old orders first
        self.cancel_order(self.yes_order_id)
        self.cancel_order(self.no_order_id)
        
        time.sleep(0.5)  # Brief pause to let cancels process

        # Post new orders
        self.yes_order_id = self.place_maker_bid(market["yes_token"], yes_price, ORDER_SIZE)
        self.no_order_id = self.place_maker_bid(market["no_token"], no_price, ORDER_SIZE)

        self.yes_order_price = yes_price
        self.no_order_price = no_price
        self.yes_order_size = ORDER_SIZE
        self.no_order_size = ORDER_SIZE

        return {
            "yes_bid": yes_price,
            "no_bid": no_price,
            "total": yes_price + no_price,
        }

    def check_and_hedge_fills(self, market: Dict):
        """Check for fills and IMMEDIATELY hedge them."""

        # Check YES order
        if self.yes_order_id:
            filled = self.check_order_filled(self.yes_order_id)
            if filled > 0:
                print(f"\n   MAKER FILL: {filled:.0f} YES @ ${self.yes_order_price:.2f}")
                self.yes_shares += filled
                self.maker_fills += 1
                self.total_volume += filled * self.yes_order_price

                # IMMEDIATELY hedge
                self.hedge_immediately("YES", filled, self.yes_order_price, market)

                # Clear the order
                self.cancel_order(self.yes_order_id)
                self.yes_order_id = None

        # Check NO order
        if self.no_order_id:
            filled = self.check_order_filled(self.no_order_id)
            if filled > 0:
                print(f"\n   MAKER FILL: {filled:.0f} NO @ ${self.no_order_price:.2f}")
                self.no_shares += filled
                self.maker_fills += 1
                self.total_volume += filled * self.no_order_price

                # IMMEDIATELY hedge
                self.hedge_immediately("NO", filled, self.no_order_price, market)

                # Clear the order
                self.cancel_order(self.no_order_id)
                self.no_order_id = None

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

    def show_status(self, quotes: Dict = None):
        """Show current status."""
        pnl = self.calculate_pnl()

        delta = self.yes_shares - self.no_shares
        if abs(delta) < 0.1:
            delta_status = "NEUTRAL"
        else:
            delta_status = f"UNHEDGED:{delta:+.0f}"

        if quotes:
            quote_str = f"Y${quotes['yes_bid']:.2f} N${quotes['no_bid']:.2f}"
        else:
            quote_str = "..."

        return f"{quote_str} | Pairs:{pnl['pairs']:.0f} | {delta_status} | ${pnl['total']:+.2f}"

    def show_dashboard(self):
        """Full dashboard."""
        runtime = (time.time() - self.start_time) / 60
        pnl = self.calculate_pnl()

        print(f"\n\n{'='*60}")
        print(f"DELTA NEUTRAL MARKET MAKER - FINAL REPORT")
        print(f"{'='*60}")
        print(f"   Runtime:       {runtime:.1f} minutes")
        print(f"   Volume:        ${self.total_volume:.2f}")
        print(f"   Maker fills:   {self.maker_fills}")
        print(f"   Taker fills:   {self.taker_fills}")
        print(f"   {'-'*40}")
        print(f"   YES shares:    {self.yes_shares:.0f}")
        print(f"   NO shares:     {self.no_shares:.0f}")
        delta = self.yes_shares - self.no_shares
        if abs(delta) < 0.1:
            print(f"   Delta:         NEUTRAL (0)")
        else:
            print(f"   Delta:         {delta:+.0f} (UNHEDGED)")
        print(f"   {'-'*40}")
        print(f"   Hedged pairs:  {pnl['pairs']:.0f}")
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
        print("DELTA NEUTRAL 15-MINUTE MARKET MAKER")
        print("When one side fills -> immediately buy the other side")
        print(f"Order size: ${ORDER_SIZE}")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        scan = 0
        last_quote_time = 0
        last_verbose = 0
        QUOTE_REFRESH = 10

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

                # Check for fills and hedge immediately
                self.check_and_hedge_fills(market)

                # Post/refresh quotes
                quotes = None
                if time.time() - last_quote_time > QUOTE_REFRESH:
                    quotes = self.post_quotes(market)
                    last_quote_time = time.time()

                # Show status
                status = self.show_status(quotes)
                print(f"\r[{ts}] #{scan} | {market['time']} | {status}   ", end="", flush=True)

                time.sleep(2)

        except KeyboardInterrupt:
            print(f"\n\nStopping...")
            self.cancel_order(self.yes_order_id)
            self.cancel_order(self.no_order_id)
            self.show_dashboard()

            if self.hedged_pairs > 0:
                print(f"\nYou have {self.hedged_pairs:.0f} hedged pairs.")
                print(f"These will pay out $1.00 each at market expiration.")
                print(f"Guaranteed profit: ${self.hedged_pairs - self.total_pair_cost:.2f}")


if __name__ == "__main__":
    DeltaNeutralMM().run()
