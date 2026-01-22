#!/usr/bin/env python3
"""
Diagnose wallet configuration for Polymarket.
This script helps identify why "invalid signature" errors occur.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

def main():
    print("=" * 60)
    print("POLYMARKET WALLET DIAGNOSTICS")
    print("=" * 60)

    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))

    print(f"\n1. CONFIGURATION:")
    print(f"   Private Key: {private_key[:8]}...{private_key[-8:]} ({len(private_key)} chars)")
    print(f"   Safe Address: {safe_address}")
    print(f"   Signature Type: {sig_type}")

    # Derive EOA address from private key
    print(f"\n2. DERIVED EOA ADDRESS:")
    try:
        from eth_account import Account

        # Handle with or without 0x prefix
        pk = private_key if private_key.startswith('0x') else f'0x{private_key}'
        account = Account.from_key(pk)
        derived_address = account.address

        print(f"   Your MetaMask EOA: {derived_address}")
        print(f"   (This should match your MetaMask wallet address)")

        if safe_address.lower() == derived_address.lower():
            print(f"\n   ⚠️  WARNING: Safe address = EOA address!")
            print(f"   This is unusual. Normally they are different.")
            print(f"   - EOA = Your MetaMask wallet")
            print(f"   - Safe = Polymarket Smart Wallet (different address)")
    except ImportError:
        print("   ❌ eth_account not installed. Run: pip install eth-account")
        print(f"   Cannot derive EOA address without it.")
    except Exception as e:
        print(f"   ❌ Error deriving address: {e}")

    # Test CLOB client connection
    print(f"\n3. TESTING CLOB CONNECTION:")
    try:
        from py_clob_client.client import ClobClient

        # Try with signature_type=2 (proxy wallet)
        print(f"   Creating client with signature_type={sig_type}...")
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=sig_type,
            funder=safe_address if sig_type == 2 else None,
        )
        print(f"   ✅ Client created")

        # Try to get/create API credentials
        print(f"\n4. TESTING API CREDENTIALS:")
        try:
            print(f"   Trying derive_api_key()...")
            creds = client.derive_api_key()
            print(f"   ✅ Derived API key: {creds.api_key[:20]}...")
            client.set_api_creds(creds)
        except Exception as e:
            print(f"   derive_api_key failed: {e}")
            try:
                print(f"   Trying create_api_key()...")
                creds = client.create_api_key()
                print(f"   ✅ Created API key: {creds.api_key[:20]}...")
                client.set_api_creds(creds)
            except Exception as e2:
                print(f"   ❌ create_api_key also failed: {e2}")
                return

        # Test a simple authenticated request
        print(f"\n5. TESTING AUTHENTICATED REQUEST:")
        try:
            orders = client.get_orders()
            print(f"   ✅ get_orders() succeeded!")
            print(f"   Orders found: {len(orders) if orders else 0}")
        except Exception as e:
            print(f"   ❌ get_orders() failed: {e}")

        # Check balance info if possible
        print(f"\n6. CHECKING BALANCE:")
        try:
            # Try to get balance info
            import httpx
            headers = {"Accept": "application/json"}

            # Get USDC balance from CLOB
            resp = httpx.get(
                f"https://clob.polymarket.com/balance",
                params={"address": safe_address},
                headers=headers,
                timeout=30
            )
            if resp.status_code == 200:
                balance_data = resp.json()
                print(f"   ✅ Balance data: {balance_data}")
            else:
                print(f"   Balance check returned {resp.status_code}")
        except Exception as e:
            print(f"   Could not check balance: {e}")

    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*60}")
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)
    print("""
COMMON ISSUES:

1. Wrong Safe Address:
   - Go to polymarket.com -> Profile -> Deposit
   - The "Deposit to" address is your Safe/Proxy address
   - This should be in POLY_SAFE_ADDRESS

2. Wrong Private Key:
   - Export from MetaMask: Account Details -> Export Private Key
   - This is your EOA private key (not the Safe's key)
   - Put in POLY_PRIVATE_KEY (without 0x prefix)

3. Wrong Signature Type:
   - Type 2 = Polymarket proxy wallet (most common)
   - Type 0 = Direct EOA signing (hardware wallet)
   - Type 1 = Magic/Email wallet

4. API Credentials Issue:
   - Try running the script again to refresh credentials
   - Old credentials may have expired

5. Allowance Issue:
   - You may need to approve USDC spending on Polymarket
   - Do this through the Polymarket web UI first
""")

if __name__ == "__main__":
    main()
