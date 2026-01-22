#!/usr/bin/env python3
"""
Simple launcher for the Polymarket Sports Market Maker.

Usage:
    python run_sports_bot.py --paper    # Paper trading (safe)
    python run_sports_bot.py --live     # Live trading (real money!)
"""
import os
import sys

# Ensure we're in the project directory
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

# Import and run
from src.sports.sports_main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
