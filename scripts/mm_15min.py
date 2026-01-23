#!/usr/bin/env python3
"""
15-MINUTE BTC Market Maker - Earns Maker Rebates

Strategy:
1. Find 15-minute BTC markets (not hourly)
2. Post MAKER orders on both YES and NO sides
3. When one fills, hedge the other side
4. Earn maker rebates (20% of taker fees redistributed to makers)

Key: Being a MAKER earns rebates. Being a TAKER pays fees.
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

# Config
ORDER_SIZE = float(os.getenv("BASE_ORDER_SIZE", "5"))
MIN_PROFIT_CENTS = 0.5  # Minimum profit per share (rebates add to this)
DASHBOARD_INTERVAL = 2 * 60 * 60  # 2 hours


class FifteenMinMM:
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
        self.maker_volume = 0.0  # Track volume for rebate estimation
        self.start_time = time.time()
        self.last_dashboard = time.time()

        print(f"✓ Connected: {safe_address[:20]}...")

    def get_15min_markets(self) -> List[Dict]:
        """Get active 15-minute BTC markets."""
        markets = []

        try:
            # Search for 15-minute BTC markets
            with httpx.Client(timeout=10) as http:
                # Try searching by tag or keyword
                resp = http.get(f"{GAMMA_API}/markets", params={
                    "active": "true",
                    "closed": "false",
                    "limit": 100
                })
                all_markets = resp.json()

                for mkt in all_markets:
                    question = mkt.get("question", "").lower()
                    # Look for 15-minute BTC markets
                    if "bitcoin" in question and ("15" in question or "fifteen" in question or "15-min" in question or "15min" in question):
                        tokens = mkt.get("clobTokenIds", [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)
                        if len(tokens) >= 2:
                            # Get end time from question or market data
                            end_str = mkt.get("endDate", "")
                            markets.append({
                                "yes_token": tokens[0],
                                "no_token": tokens[1],
                                "question": mkt.get("question", ""),
                                "condition_id": mkt.get("conditionId", ""),
                                "slug": mkt.get("slug", ""),
                            })
        except Exception as e:
            print(f"\nError fetching markets: {e}")

        # Also try specific slug patterns for 15-min markets
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)

        # Try different 15-minute interval timestamps
        for offset in range(8):  # Check next 2 hours (8 x 15min intervals)
            dt = now + timedelta(minutes=offset * 15)
            # Round to nearest 15 minutes
            minute = (dt.minute // 15) * 15
            dt = dt.replace(minute=minute, second=0, microsecond=0)

            # Convert to Unix timestamp
            timestamp = int(dt.timestamp())

            # Try slug format: btc-updown-15m-{timestamp}
            slug = f"btc-updown-15m-{timestamp}"

            try:
                with httpx.Client(timeout=10) as http:
                    resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                    events = resp.json()
                    if events:
                        for mkt in events[0].get("markets", []):
                            if mkt.get("closed"):
                                continue
                            tokens = mkt.get("clobTokenIds", [])
                            if isinstance(tokens, str):
                                tokens = json.loads(tokens)
                            if len(tokens) >= 2:
                                # Check if already added
                                if not any(m["yes_token"] == tokens[0] for m in markets):
                                    mins_left = (dt - now).total_seconds() / 60
                                    if mins_left > 2:  # Skip if about to expire
                                        markets.append({
                                            "yes_token": tokens[0],
                                            "no_token": tokens[1],
                                            "question": mkt.get("question", ""),
                                            "time": dt.strftime("%H:%M"),
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

    def place_maker_order(self, token_id: str, price: float, size: float, side: str) -> Optional[str]:
        """Place a maker (GTC) order."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            print(f"   Order error: {e}")
            return None

    def place_fok(self, token_id: str, price: float, size: float, side: str = BUY) -> bool:
        """Place Fill-or-Kill order for instant execution."""
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
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
        """Cancel an order."""
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

    def emergency_sell(self, token_id: str, size: float) -> Tuple[bool, float]:
        """Immediately sell a position."""
        try:
            ob = self.get_ob(token_id)
            if ob["bids"]:
                sell_price = ob["bids"][0][0]
                if self.place_fok(token_id, sell_price, size, SELL):
                    return True, sell_price
            return False, 0
        except:
            return False, 0

    def calculate_rebate_estimate(self, price: float, shares: float) -> float:
        """
        Estimate maker rebate based on Polymarket's fee curve.
        Rebate = 20% of taker fee that would be charged.
        Fee formula: shares * price * 0.25 * (price * (1 - price))^2
        """
        fee_equivalent = shares * price * 0.25 * (price * (1 - price)) ** 2
        rebate = fee_equivalent * 0.20  # 20% rebate rate
        return rebate

    def find_opportunity(self, market: Dict) -> Optional[Dict]:
        """Find a profitable maker opportunity."""
        yes_ob = self.get_ob(market["yes_token"])
        no_ob = self.get_ob(market["no_token"])

        if not yes_ob["bids"] or not yes_ob["asks"] or not no_ob["bids"] or not no_ob["asks"]:
            return None

        yes_bid, yes_bid_size = yes_ob["bids"][0]
        yes_ask, yes_ask_size = yes_ob["asks"][0]
        no_bid, no_bid_size = no_ob["bids"][0]
        no_ask, no_ask_size = no_ob["asks"][0]

        # Calculate taker-taker cost (what we'd pay to take both sides)
        taker_cost = yes_ask + no_ask
        taker_profit = 1.0 - taker_cost

        # Calculate maker opportunity
        # Post bid at best_bid + 0.01 (improve the bid to be top of queue)
        our_yes_bid = round(min(yes_bid + 0.01, yes_ask - 0.01), 2)
        our_no_bid = round(min(no_bid + 0.01, no_ask - 0.01), 2)

        maker_cost = our_yes_bid + our_no_bid
        maker_profit = 1.0 - maker_cost

        # Estimate rebate we'd earn as maker
        yes_rebate = self.calculate_rebate_estimate(our_yes_bid, ORDER_SIZE)
        no_rebate = self.calculate_rebate_estimate(our_no_bid, ORDER_SIZE)
        total_rebate = yes_rebate + no_rebate

        # Total profit = spread profit + rebates
        total_profit_per_share = maker_profit + (total_rebate / ORDER_SIZE)

        if taker_profit >= MIN_PROFIT_CENTS / 100:
            return {
                "market": market,
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "taker_cost": taker_cost,
                "taker_profit": taker_profit,
                "taker_profit_cents": taker_profit * 100,
                "our_yes_bid": our_yes_bid,
                "our_no_bid": our_no_bid,
                "maker_cost": maker_cost,
                "maker_profit": maker_profit,
                "estimated_rebate": total_rebate,
                "total_profit_cents": total_profit_per_share * 100,
            }

        return None

    def execute_taker_trade(self, opp: Dict) -> bool:
        """Execute a taker-taker trade (guaranteed fills)."""
        market = opp["market"]
        size = ORDER_SIZE

        print(f"\n{'='*60}")
        print(f"⚡ 15-MIN TRADE - {market.get('time', '??')} ET")
        print(f"{'='*60}")
        print(f"   BUY YES @ ${opp['yes_ask']:.2f}")
        print(f"   BUY NO  @ ${opp['no_ask']:.2f}")
        print(f"   Total: ${opp['taker_cost']:.3f} | Profit: ${opp['taker_profit']*size:.2f}")

        # Execute both sides
        yes_price = round(opp['yes_ask'] + 0.01, 2)
        yes_filled = self.place_fok(market["yes_token"], yes_price, size)

        if not yes_filled:
            print(f"   ❌ YES order failed")
            return False

        print(f"   ✓ YES filled @ ${yes_price:.2f}")

        no_price = round(opp['no_ask'] + 0.01, 2)
        no_filled = self.place_fok(market["no_token"], no_price, size)

        if not no_filled:
            print(f"   ❌ NO order failed - EMERGENCY SELL")
            sold, sell_price = self.emergency_sell(market["yes_token"], size)
            if sold:
                loss = (yes_price - sell_price) * size
                self.total_pnl -= loss
                self.total_trades += 1
                print(f"   🔴 SOLD YES @ ${sell_price:.2f} | Loss: ${loss:.2f}")
            return False

        print(f"   ✓ NO filled @ ${no_price:.2f}")

        # Calculate P&L
        actual_cost = yes_price + no_price
        actual_profit = (1.0 - actual_cost) * size

        self.total_pnl += actual_profit
        self.total_trades += 1
        self.maker_volume += actual_cost * size
        if actual_profit > 0:
            self.wins += 1

        print(f"\n   ✅ HEDGED! P&L: ${actual_profit:+.2f}")
        self.show_stats()
        return True

    def show_stats(self):
        """Show current stats."""
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        runtime = (time.time() - self.start_time) / 3600
        color = "🟢" if self.total_pnl >= 0 else "🔴"
        print(f"\n   {'─'*40}")
        print(f"   {color} P&L: ${self.total_pnl:+.2f} | Trades: {self.total_trades} | Win: {win_rate:.0f}%")
        print(f"   Volume: ${self.maker_volume:.2f} | Runtime: {runtime:.1f}h")

    def show_dashboard(self):
        """Full dashboard."""
        runtime = (time.time() - self.start_time) / 3600
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

        # Estimate rebates earned (very rough)
        estimated_rebate = self.maker_volume * 0.01 * 0.20  # ~1% avg fee, 20% rebate

        print(f"\n\n{'='*60}")
        print(f"📊 15-MIN MARKET MAKER DASHBOARD")
        print(f"{'='*60}")
        print(f"   Runtime:        {runtime:.1f} hours")
        print(f"   Trades:         {self.total_trades}")
        print(f"   Win Rate:       {win_rate:.0f}%")
        print(f"   Volume:         ${self.maker_volume:.2f}")
        print(f"   Est. Rebates:   ${estimated_rebate:.2f}")
        print(f"   {'─'*40}")
        color = "🟢" if self.total_pnl >= 0 else "🔴"
        print(f"   {color} TOTAL P&L: ${self.total_pnl:+.2f}")
        print(f"   {color} + Rebates: ~${self.total_pnl + estimated_rebate:+.2f}")
        print(f"{'='*60}\n")

    def run(self):
        """Main loop."""
        print(f"\n{'='*60}")
        print("15-MINUTE BTC MARKET MAKER")
        print("Targeting 15-min markets for MAKER REBATES")
        print(f"Size: ${ORDER_SIZE} | Min profit: {MIN_PROFIT_CENTS}¢")
        print("Rebates: 20% of taker fees → makers")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}")

        scan = 0

        while True:
            try:
                scan += 1
                ts = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S ET")

                # Dashboard every 2 hours
                if time.time() - self.last_dashboard > DASHBOARD_INTERVAL:
                    self.show_dashboard()
                    self.last_dashboard = time.time()

                markets = self.get_15min_markets()

                if not markets:
                    print(f"\r[{ts}] #{scan} | No 15-min markets found | P&L: ${self.total_pnl:+.2f}   ", end="", flush=True)
                    time.sleep(5)
                    continue

                # Check opportunities
                opportunities = []
                status_parts = []

                for m in markets[:4]:
                    opp = self.find_opportunity(m)
                    time_str = m.get("time", "??")

                    if opp:
                        opportunities.append(opp)
                        status_parts.append(f"{time_str}:{opp['taker_profit_cents']:+.1f}¢✓")
                    else:
                        yes_ob = self.get_ob(m["yes_token"])
                        no_ob = self.get_ob(m["no_token"])
                        if yes_ob["asks"] and no_ob["asks"]:
                            taker = (1 - yes_ob["asks"][0][0] - no_ob["asks"][0][0]) * 100
                            status_parts.append(f"{time_str}:{taker:+.1f}¢")

                status = " | ".join(status_parts) if status_parts else "scanning..."
                print(f"\r[{ts}] #{scan} | {status} | P&L: ${self.total_pnl:+.2f}   ", end="", flush=True)

                # Execute best opportunity
                if opportunities:
                    opportunities.sort(key=lambda x: x["taker_profit"], reverse=True)
                    best = opportunities[0]
                    self.execute_taker_trade(best)
                    time.sleep(3)

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
                time.sleep(5)


if __name__ == "__main__":
    FifteenMinMM().run()
