#!/usr/bin/env python3
"""
Interactive setup script for Polymarket Market Maker.

Helps users configure their .env file with the required credentials.
"""

import os
import sys
from pathlib import Path


def print_header():
    print("\n" + "=" * 60)
    print("  POLYMARKET HOURLY BTC MARKET MAKER - SETUP")
    print("=" * 60 + "\n")


def print_warning():
    print("⚠️  WARNING: This bot trades with REAL MONEY!")
    print("    - Start with a SMALL amount ($10-20)")
    print("    - Monitor the bot closely at first")
    print("    - You can lose money if markets move against you\n")


def get_input(prompt: str, required: bool = True, default: str = "") -> str:
    """Get user input with optional default."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "

    while True:
        value = input(prompt).strip()
        if not value and default:
            return default
        if not value and required:
            print("  This field is required. Please enter a value.")
            continue
        return value


def get_credentials():
    """Get wallet credentials from user."""
    print("STEP 1: Wallet Credentials")
    print("-" * 40)
    print("\nTo find your credentials:")
    print("1. Go to https://polymarket.com")
    print("2. Log in with your email")
    print("3. Click your profile → Settings")
    print("4. Find your wallet address\n")

    safe_address = get_input("Enter your Polymarket wallet address (0x...)")

    print("\n⚠️  For email login, getting the private key is complex.")
    print("   If you can't find it, you may need to use a different wallet.\n")

    private_key = get_input("Enter your private key (without 0x prefix)", required=False)

    return safe_address, private_key


def get_signature_type():
    """Get the wallet type."""
    print("\nSTEP 2: Wallet Type")
    print("-" * 40)
    print("\nHow did you create your Polymarket account?")
    print("  1. Email login (most common)")
    print("  2. MetaMask / hardware wallet")
    print("  3. Browser extension / Polymarket Safe\n")

    choice = get_input("Enter choice (1, 2, or 3)", default="1")

    sig_map = {"1": "1", "2": "0", "3": "2"}
    return sig_map.get(choice, "1")


def get_trading_params():
    """Get trading parameters."""
    print("\nSTEP 3: Trading Parameters")
    print("-" * 40)
    print("\nRecommended settings for testing:")
    print("  - Max position: $10-50")
    print("  - Max exposure: $50-100\n")

    max_position = get_input("Max position size in USD", default="25")
    max_exposure = get_input("Max total exposure in USD", default="100")
    order_size = get_input("Base order size (shares)", default="10")

    return max_position, max_exposure, order_size


def create_env_file(
    safe_address: str,
    private_key: str,
    sig_type: str,
    max_position: str,
    max_exposure: str,
    order_size: str,
):
    """Create the .env file."""
    env_content = f"""# Polymarket MM Configuration (auto-generated)

# Wallet Credentials
POLY_PRIVATE_KEY={private_key}
POLY_SAFE_ADDRESS={safe_address}
POLY_SIGNATURE_TYPE={sig_type}

# Trading Parameters (conservative for testing)
MIN_EDGE_THRESHOLD=0.02
MAX_POSITION_SIZE={max_position}
MAX_TOTAL_EXPOSURE={max_exposure}
BASE_ORDER_SIZE={order_size}
NUM_QUOTE_LEVELS=2
LEVEL_SPACING=0.01
QUOTE_REFRESH_SECONDS=5.0

# Risk Management
MAX_DRAWDOWN_PCT=10
MAX_INVENTORY_SKEW=0.6
MIN_TIME_REMAINING=300
MAX_TRADES_PER_MINUTE=20

# LIVE TRADING MODE
PAPER_TRADING=false

# Logging
LOG_LEVEL=INFO

# Endpoints
POLY_CLOB_URL=https://clob.polymarket.com
POLY_GAMMA_URL=https://gamma-api.polymarket.com
BINANCE_WS_URL=wss://stream.binance.com:9443/ws
"""

    env_path = Path(__file__).parent.parent / ".env"

    if env_path.exists():
        overwrite = get_input(f"\n.env file exists. Overwrite? (y/n)", default="n")
        if overwrite.lower() != "y":
            print("Keeping existing .env file.")
            return False

    with open(env_path, "w") as f:
        f.write(env_content)

    print(f"\n✅ Created .env file at: {env_path}")
    return True


def print_next_steps(has_private_key: bool):
    """Print next steps for the user."""
    print("\n" + "=" * 60)
    print("  NEXT STEPS")
    print("=" * 60)

    if not has_private_key:
        print("\n⚠️  You didn't provide a private key.")
        print("   The bot cannot trade without it.\n")
        print("   Options:")
        print("   1. Check Polymarket settings for 'Export Key'")
        print("   2. Create a MetaMask wallet and transfer funds")
        print("   3. Use Polymarket's web interface manually\n")
    else:
        print("\n✅ Configuration complete!")
        print("\nTo run the bot:\n")
        print("   # Install dependencies first:")
        print("   pip install -r requirements.txt")
        print()
        print("   # Run in live mode:")
        print("   python -m src.main --live")
        print()
        print("   # Or run with debug logging:")
        print("   python -m src.main --live --log-level DEBUG")

    print("\n⚠️  IMPORTANT REMINDERS:")
    print("   - Deposit funds to your Polymarket account first")
    print("   - Start with a small amount ($10-20)")
    print("   - Watch the bot for the first few trades")
    print("   - Ctrl+C to stop the bot at any time")
    print()


def main():
    print_header()
    print_warning()

    proceed = get_input("Do you want to continue with setup? (y/n)", default="y")
    if proceed.lower() != "y":
        print("Setup cancelled.")
        return

    safe_address, private_key = get_credentials()
    sig_type = get_signature_type()
    max_position, max_exposure, order_size = get_trading_params()

    create_env_file(
        safe_address=safe_address,
        private_key=private_key,
        sig_type=sig_type,
        max_position=max_position,
        max_exposure=max_exposure,
        order_size=order_size,
    )

    print_next_steps(bool(private_key))


if __name__ == "__main__":
    main()
