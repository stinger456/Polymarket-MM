#!/usr/bin/env python3
"""
Debug script to understand exactly what's being signed and why neg_risk orders fail.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY
from py_clob_client.config import get_contract_config

# Also import the low-level signing components
from py_order_utils.builders import OrderBuilder as UtilsOrderBuilder
from py_order_utils.signer import Signer as UtilsSigner
from py_order_utils.model import OrderData, EOA, POLY_GNOSIS_SAFE

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

def main():
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    safe_address = os.getenv("POLY_SAFE_ADDRESS", "")

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    print("=" * 70)
    print("SIGNATURE DEBUG SCRIPT")
    print("=" * 70)

    # Create signer
    signer = UtilsSigner(key=private_key)
    signer_address = signer.address()

    print(f"\nPrivate Key: {private_key[:10]}...{private_key[-8:]}")
    print(f"Signer (EOA): {signer_address}")
    print(f"Safe (Funder): {safe_address}")

    # Print contract addresses
    print("\n" + "-" * 70)
    print("EXCHANGE CONTRACTS")
    print("-" * 70)

    regular_config = get_contract_config(CHAIN_ID, neg_risk=False)
    neg_risk_config = get_contract_config(CHAIN_ID, neg_risk=True)

    print(f"Regular exchange:   {regular_config.exchange}")
    print(f"Neg-risk exchange:  {neg_risk_config.exchange}")

    # Use a known BTC hourly token ID (we'll get this from our existing scripts)
    # For now, use a placeholder - in real usage, fetch from the API
    print("\n" + "-" * 70)
    print("ORDER PARAMETERS")
    print("-" * 70)

    # Create a test order
    test_token_id = "72978614791093211870596938456325339254746001692946489809826748882572229037930"
    test_price = 0.48
    test_size = 5.0

    print(f"Token ID: {test_token_id[:40]}...")
    print(f"Price: {test_price}")
    print(f"Size: {test_size}")

    # Calculate maker/taker amounts (simplified - assuming price rounding to 2 decimals)
    maker_amount = int(test_size * test_price * 1e6)  # USDC has 6 decimals
    taker_amount = int(test_size * 1e6)

    print(f"Maker Amount (USDC): {maker_amount}")
    print(f"Taker Amount (shares): {taker_amount}")

    # Test both signature configurations
    for neg_risk in [False, True]:
        print("\n" + "=" * 70)
        print(f"TESTING: neg_risk={neg_risk}")
        print("=" * 70)

        config = get_contract_config(CHAIN_ID, neg_risk)
        print(f"Exchange contract: {config.exchange}")

        # Create order data
        order_data = OrderData(
            maker=safe_address,
            taker="0x0000000000000000000000000000000000000000",
            tokenId=test_token_id,
            makerAmount=str(maker_amount),
            takerAmount=str(taker_amount),
            side=0,  # BUY
            feeRateBps="0",
            nonce="0",
            signer=signer_address,
            expiration="0",
            signatureType=POLY_GNOSIS_SAFE,  # Type 2
        )

        print(f"\nOrder Data:")
        print(f"  maker:         {order_data.maker}")
        print(f"  signer:        {order_data.signer}")
        print(f"  taker:         {order_data.taker}")
        print(f"  tokenId:       {order_data.tokenId[:40]}...")
        print(f"  makerAmount:   {order_data.makerAmount}")
        print(f"  takerAmount:   {order_data.takerAmount}")
        print(f"  side:          {order_data.side}")
        print(f"  feeRateBps:    {order_data.feeRateBps}")
        print(f"  nonce:         {order_data.nonce}")
        print(f"  expiration:    {order_data.expiration}")
        print(f"  signatureType: {order_data.signatureType} (POLY_GNOSIS_SAFE)")

        # Build the order using the low-level builder
        order_builder = UtilsOrderBuilder(
            config.exchange,
            CHAIN_ID,
            signer,
        )

        print(f"\nDomain Separator:")
        print(f"  name:              Polymarket CTF Exchange")
        print(f"  version:           1")
        print(f"  chainId:           {CHAIN_ID}")
        print(f"  verifyingContract: {config.exchange}")

        try:
            order = order_builder.build_order(order_data)
            print(f"\nBuilt Order:")
            print(f"  salt:          {order.salt}")
            print(f"  maker:         {order.maker}")
            print(f"  signer:        {order.signer}")
            print(f"  taker:         {order.taker}")
            print(f"  tokenId:       {order.tokenId}")
            print(f"  makerAmount:   {order.makerAmount}")
            print(f"  takerAmount:   {order.takerAmount}")
            print(f"  side:          {order.side}")
            print(f"  expiration:    {order.expiration}")
            print(f"  nonce:         {order.nonce}")
            print(f"  feeRateBps:    {order.feeRateBps}")
            print(f"  signatureType: {order.signatureType}")

            # Get struct hash
            struct_hash = order_builder._create_struct_hash(order)
            print(f"\nStruct Hash: {struct_hash[:40]}...")

            # Sign
            signature = order_builder.build_order_signature(order)
            print(f"Signature: {signature[:40]}...")
            print(f"Signature length: {len(signature)}")

        except Exception as e:
            print(f"\n❌ Error building order: {e}")
            import traceback
            traceback.print_exc()

    # Now test via the full ClobClient to see if there's any difference
    print("\n" + "=" * 70)
    print("TESTING VIA CLOBCLIENT")
    print("=" * 70)

    try:
        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=2,  # POLY_GNOSIS_SAFE
            funder=safe_address,
        )

        print("Client created, getting API credentials...")

        try:
            creds = client.derive_api_key()
            client.set_api_creds(creds)
            print(f"API Key: {creds.api_key[:20]}...")
        except Exception as e:
            print(f"derive_api_key failed: {e}")
            try:
                creds = client.create_api_key()
                client.set_api_creds(creds)
                print(f"API Key (created): {creds.api_key[:20]}...")
            except Exception as e2:
                print(f"create_api_key also failed: {e2}")
                return

        # Check what neg_risk the API reports for this token
        print("\nChecking API for market info...")
        try:
            neg_risk_api = client.get_neg_risk(test_token_id)
            print(f"API reports neg_risk: {neg_risk_api}")
        except Exception as e:
            print(f"get_neg_risk failed: {e}")
            neg_risk_api = None

        try:
            tick_size = client.get_tick_size(test_token_id)
            print(f"API reports tick_size: {tick_size}")
        except Exception as e:
            print(f"get_tick_size failed: {e}")

        # Try creating orders with both neg_risk values
        for neg_risk in [False, True]:
            print(f"\n--- Creating order with neg_risk={neg_risk} ---")
            try:
                order_args = OrderArgs(
                    token_id=test_token_id,
                    price=test_price,
                    size=test_size,
                    side=BUY,
                )
                options = PartialCreateOrderOptions(neg_risk=neg_risk)

                signed_order = client.create_order(order_args, options)
                print(f"Order created successfully!")
                print(f"Order: {signed_order}")

            except Exception as e:
                print(f"❌ Failed: {e}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
