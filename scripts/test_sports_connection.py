#!/usr/bin/env python3
"""
test_sports_connection.py - Verify your setup works for sports market making.

Run this before starting the bot to verify:
1. Client connection works
2. API credentials are valid
3. Can discover sports markets
4. Can place/cancel test orders
"""
import os
import sys
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
import httpx

load_dotenv()


GAMMA_URL = "https://gamma-api.polymarket.com"


async def test_sports_discovery():
    """Test fetching sports markets from Gamma API."""
    print("\n=== Testing Sports Market Discovery ===")

    # Sports-related search terms
    sports_terms = ["NBA", "NFL", "MLB", "Soccer", "NHL"]

    async with httpx.AsyncClient() as client:
        # First, get all active events
        params = {
            "active": "true",
            "closed": "false",
            "limit": 100,
        }

        try:
            resp = await client.get(f"{GAMMA_URL}/events", params=params)
            events = resp.json()

            if not events:
                print("No active events found")
                return None

            print(f"Found {len(events)} active events")

            # Find sports-related events
            sports_events = []
            for event in events:
                title = event.get("title", "").upper()
                tags = str(event.get("tags", [])).upper()

                for sport in sports_terms:
                    if sport in title or sport in tags:
                        sports_events.append(event)
                        break

            if sports_events:
                print(f"Found {len(sports_events)} sports events:")
                for event in sports_events[:5]:
                    print(f"  - {event.get('title', 'Unknown')[:60]}")
                return sports_events[0]
            else:
                print("No sports events found in current active events")
                print("\nShowing first 5 active events:")
                for event in events[:5]:
                    print(f"  - {event.get('title', 'Unknown')[:60]}")
                return events[0] if events else None

        except Exception as e:
            print(f"Error fetching events: {e}")
            return None


def test_connection():
    """Test basic CLOB client connection."""
    print("\n=== Step 1: Testing Client Connection ===")

    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    funder = os.getenv("POLY_SAFE_ADDRESS", "")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))

    if not private_key:
        print("WARNING: POLY_PRIVATE_KEY not set - running in read-only mode")
        client = ClobClient("https://clob.polymarket.com")
        print("Created read-only client")
        return client, False

    if not funder:
        print("ERROR: POLY_SAFE_ADDRESS not set")
        return None, False

    print(f"Private key: {private_key[:8]}...")
    print(f"Funder address: {funder}")
    print(f"Signature type: {sig_type}")

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=sig_type,
            funder=funder,
        )
        print("Client created successfully")
        return client, True
    except Exception as e:
        print(f"ERROR creating client: {e}")
        return None, False


def test_api_credentials(client, has_auth):
    """Test API credential generation."""
    print("\n=== Step 2: Testing API Credentials ===")

    if not has_auth:
        print("Skipping - no authentication")
        return False

    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"API Key: {creds.api_key[:20]}...")
        print("API credentials set successfully")
        return True
    except Exception as e:
        print(f"ERROR setting API credentials: {e}")
        print("\nTroubleshooting:")
        print("- Check if POLY_SIGNATURE_TYPE matches your login method")
        print("  1 = Email/Magic wallet")
        print("  2 = Browser proxy (Coinbase/MetaMask via Polymarket)")
        print("  0 = EOA (direct MetaMask)")
        return False


def test_fetch_markets(client):
    """Test fetching markets from CLOB."""
    print("\n=== Step 3: Testing Market Fetch ===")

    try:
        markets = client.get_simplified_markets()
        data = markets.get("data", []) if isinstance(markets, dict) else []

        if data:
            print(f"Found {len(data)} markets")
            market = data[0]
            print(f"Sample market: {market.get('question', 'Unknown')[:50]}")

            tokens = market.get("tokens", [])
            if tokens:
                token_id = tokens[0].get("token_id")
                print(f"Sample token ID: {token_id[:30]}...")
                return token_id
        else:
            print("No markets found")
            return None
    except Exception as e:
        print(f"ERROR fetching markets: {e}")
        return None


def test_order_placement(client, token_id, has_auth):
    """Test placing and cancelling a small order."""
    print("\n=== Step 4: Testing Order Placement ===")

    if not has_auth:
        print("Skipping - no authentication")
        return False

    if not token_id:
        print("Skipping - no token ID available")
        return False

    try:
        # Place a very low bid that won't fill
        order = OrderArgs(
            token_id=token_id,
            price=0.01,  # Very low price - won't fill
            size=1.0,
            side=BUY,
        )

        print("Creating order...")
        signed = client.create_order(order)

        print("Posting order...")
        resp = client.post_order(signed, OrderType.GTC)

        order_id = resp.get("orderID")
        if order_id:
            print(f"ORDER PLACED: {order_id}")

            # Cancel the order
            print("Cancelling order...")
            client.cancel(order_id)
            print("Order cancelled successfully")
            return True
        else:
            print(f"Order response: {resp}")
            return False

    except Exception as e:
        print(f"ERROR with order: {e}")
        print("\nTroubleshooting:")
        print("- Check signature_type matches your login method")
        print("- Check funder_address is your Polymarket deposit address")
        print("- Ensure you have USDC balance on Polygon")
        return False


async def main():
    """Run all tests."""
    print("=" * 50)
    print("POLYMARKET SPORTS MARKET MAKER - CONNECTION TEST")
    print("=" * 50)

    # Test 1: Client connection
    client, has_auth = test_connection()
    if not client:
        print("\nFATAL: Could not create client")
        return

    # Test 2: API credentials
    api_ok = test_api_credentials(client, has_auth)

    # Test 3: Fetch markets
    token_id = test_fetch_markets(client)

    # Test 4: Order placement
    order_ok = test_order_placement(client, token_id, has_auth and api_ok)

    # Test 5: Sports discovery
    sports_event = await test_sports_discovery()

    # Summary
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    print(f"Client Connection: {'PASS' if client else 'FAIL'}")
    print(f"API Credentials:   {'PASS' if api_ok else 'SKIP' if not has_auth else 'FAIL'}")
    print(f"Market Fetch:      {'PASS' if token_id else 'FAIL'}")
    print(f"Order Placement:   {'PASS' if order_ok else 'SKIP' if not (has_auth and api_ok) else 'FAIL'}")
    print(f"Sports Discovery:  {'PASS' if sports_event else 'FAIL'}")

    if all([client, token_id]):
        print("\nBasic connectivity is working!")
        if has_auth and api_ok and order_ok:
            print("Full trading capabilities verified!")
        else:
            print("Set credentials in .env for full trading capabilities.")
    else:
        print("\nSome tests failed - check configuration.")


if __name__ == "__main__":
    asyncio.run(main())
