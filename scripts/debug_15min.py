#!/usr/bin/env python3
"""
DEBUG SCRIPT - Find out what's happening with 15-minute market discovery.
This will show exactly what the API returns.
"""

import os
import sys
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

import httpx
from py_clob_client.client import ClobClient

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

def debug_markets():
    print("=" * 70)
    print("15-MINUTE MARKET DEBUG")
    print("=" * 70)

    # Show current time
    et = ZoneInfo("America/New_York")
    utc = ZoneInfo("UTC")
    now_et = datetime.now(et)
    now_utc = datetime.now(utc)

    print(f"\n📅 Current Time:")
    print(f"   ET:  {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # The timestamp from user's URL
    user_timestamp = 1769176800
    user_dt = datetime.fromtimestamp(user_timestamp, tz=utc)
    print(f"\n📌 User's Market Timestamp: {user_timestamp}")
    print(f"   UTC: {user_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   ET:  {user_dt.astimezone(et).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Try multiple approaches to find markets
    print("\n" + "=" * 70)
    print("APPROACH 1: Try exact slug from user's URL")
    print("=" * 70)

    slug = f"btc-updown-15m-{user_timestamp}"
    print(f"   Trying slug: {slug}")

    with httpx.Client(timeout=15) as http:
        try:
            resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
            print(f"   Status: {resp.status_code}")
            data = resp.json()
            print(f"   Response: {json.dumps(data, indent=2)[:2000]}")
        except Exception as e:
            print(f"   Error: {e}")

    print("\n" + "=" * 70)
    print("APPROACH 2: Search for 'btc' in active events")
    print("=" * 70)

    with httpx.Client(timeout=15) as http:
        try:
            resp = http.get(f"{GAMMA_API}/events", params={
                "active": "true",
                "limit": 50,
            })
            print(f"   Status: {resp.status_code}")
            events = resp.json()
            print(f"   Found {len(events)} active events")

            btc_events = [e for e in events if 'btc' in e.get('slug', '').lower() or 'btc' in e.get('title', '').lower()]
            print(f"   BTC-related events: {len(btc_events)}")

            for e in btc_events[:5]:
                print(f"\n   📊 Event: {e.get('slug', 'no-slug')}")
                print(f"      Title: {e.get('title', 'no-title')[:60]}")
                print(f"      Markets: {len(e.get('markets', []))}")
                for m in e.get('markets', [])[:2]:
                    print(f"        - {m.get('question', 'no-question')[:50]}")
                    tokens = m.get('clobTokenIds', [])
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    print(f"          Token IDs: {tokens}")
                    print(f"          Closed: {m.get('closed', 'N/A')}")
        except Exception as e:
            print(f"   Error: {e}")

    print("\n" + "=" * 70)
    print("APPROACH 3: Search for '15m' or 'updown' slugs")
    print("=" * 70)

    with httpx.Client(timeout=15) as http:
        try:
            resp = http.get(f"{GAMMA_API}/events", params={
                "active": "true",
                "limit": 100,
            })
            events = resp.json()

            updown_events = [e for e in events if '15m' in e.get('slug', '').lower() or 'updown' in e.get('slug', '').lower()]
            print(f"   Found {len(updown_events)} updown/15m events")

            for e in updown_events[:10]:
                print(f"\n   📊 Slug: {e.get('slug', 'no-slug')}")
                print(f"      Title: {e.get('title', 'no-title')[:50]}")
                for m in e.get('markets', [])[:2]:
                    print(f"        - {m.get('question', 'no-question')[:50]}")
        except Exception as e:
            print(f"   Error: {e}")

    print("\n" + "=" * 70)
    print("APPROACH 4: Generate timestamps and check each")
    print("=" * 70)

    # Try different timestamp approaches
    print("\n   Testing timestamp generation:")

    # Round to current 15-minute interval (ET)
    current_15 = now_et.replace(minute=(now_et.minute // 15) * 15, second=0, microsecond=0)

    for i in range(-2, 6):
        check_dt = current_15 + timedelta(minutes=i * 15)
        timestamp = int(check_dt.timestamp())
        slug = f"btc-updown-15m-{timestamp}"

        with httpx.Client(timeout=5) as http:
            try:
                resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                events = resp.json()
                found = len(events) > 0
                status = "✓ FOUND" if found else "✗"
                print(f"   {status} {check_dt.strftime('%H:%M ET')} -> {slug}")

                if found:
                    event = events[0]
                    for m in event.get('markets', []):
                        tokens = m.get('clobTokenIds', [])
                        if isinstance(tokens, str):
                            tokens = json.loads(tokens)
                        print(f"        Market: {m.get('question', '')[:40]}")
                        print(f"        YES Token: {tokens[0] if tokens else 'N/A'}")
                        print(f"        NO Token: {tokens[1] if len(tokens) > 1 else 'N/A'}")
                        print(f"        Closed: {m.get('closed')}")
            except Exception as e:
                print(f"   ✗ {check_dt.strftime('%H:%M ET')} -> {slug} (Error: {e})")

    # Also try UTC-based timestamps
    print("\n   Testing UTC-based timestamps:")
    current_15_utc = now_utc.replace(minute=(now_utc.minute // 15) * 15, second=0, microsecond=0)

    for i in range(-2, 6):
        check_dt = current_15_utc + timedelta(minutes=i * 15)
        timestamp = int(check_dt.timestamp())
        slug = f"btc-updown-15m-{timestamp}"

        with httpx.Client(timeout=5) as http:
            try:
                resp = http.get(f"{GAMMA_API}/events", params={"slug": slug})
                events = resp.json()
                found = len(events) > 0
                status = "✓ FOUND" if found else "✗"
                print(f"   {status} {check_dt.strftime('%H:%M UTC')} -> {slug}")
            except:
                pass

    print("\n" + "=" * 70)
    print("APPROACH 5: Check orderbooks for found tokens")
    print("=" * 70)

    # Initialize CLOB client
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    client = ClobClient(
        CLOB_HOST, key=private_key, chain_id=CHAIN_ID,
        signature_type=2, funder=safe_address,
    )

    # Find a market with tokens and check the orderbook
    with httpx.Client(timeout=15) as http:
        try:
            resp = http.get(f"{GAMMA_API}/events", params={"active": "true", "limit": 50})
            events = resp.json()

            btc_events = [e for e in events if '15m' in e.get('slug', '').lower() or 'updown' in e.get('slug', '').lower()]

            for e in btc_events[:3]:
                for m in e.get('markets', []):
                    if m.get('closed'):
                        continue
                    tokens = m.get('clobTokenIds', [])
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if len(tokens) >= 2:
                        print(f"\n   Checking market: {m.get('question', '')[:40]}")

                        # Check YES orderbook
                        try:
                            yes_ob = client.get_order_book(tokens[0])
                            yes_bids = sorted([(float(b.price), float(b.size)) for b in (yes_ob.bids or [])], reverse=True)
                            yes_asks = sorted([(float(a.price), float(a.size)) for a in (yes_ob.asks or [])])

                            print(f"   YES Token: {tokens[0][:20]}...")
                            if yes_bids:
                                print(f"      Best Bid: ${yes_bids[0][0]:.2f} x {yes_bids[0][1]:.0f}")
                            else:
                                print(f"      Best Bid: None")
                            if yes_asks:
                                print(f"      Best Ask: ${yes_asks[0][0]:.2f} x {yes_asks[0][1]:.0f}")
                            else:
                                print(f"      Best Ask: None")
                        except Exception as e:
                            print(f"      YES orderbook error: {e}")

                        # Check NO orderbook
                        try:
                            no_ob = client.get_order_book(tokens[1])
                            no_bids = sorted([(float(b.price), float(b.size)) for b in (no_ob.bids or [])], reverse=True)
                            no_asks = sorted([(float(a.price), float(a.size)) for a in (no_ob.asks or [])])

                            print(f"   NO Token: {tokens[1][:20]}...")
                            if no_bids:
                                print(f"      Best Bid: ${no_bids[0][0]:.2f} x {no_bids[0][1]:.0f}")
                            else:
                                print(f"      Best Bid: None")
                            if no_asks:
                                print(f"      Best Ask: ${no_asks[0][0]:.2f} x {no_asks[0][1]:.0f}")
                            else:
                                print(f"      Best Ask: None")
                        except Exception as e:
                            print(f"      NO orderbook error: {e}")

                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"   Error: {e}")

    print("\n" + "=" * 70)
    print("DEBUG COMPLETE")
    print("=" * 70)
    print("\nPlease share the output above so we can fix the bot!\n")

if __name__ == "__main__":
    debug_markets()
