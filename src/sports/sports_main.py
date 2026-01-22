#!/usr/bin/env python3
"""
Entry point for the Polymarket Sports Market Maker.

Usage:
    # Paper trading (default)
    python -m src.sports.sports_main --paper

    # Live trading
    python -m src.sports.sports_main --live

    # Filter to specific sport
    python -m src.sports.sports_main --paper --sport NBA
"""
import argparse
import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import structlog
from src.config import load_config
from src.sports.sports_orchestrator import run_sports_market_maker

logger = structlog.get_logger()


def setup_logging(level: str = "INFO"):
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    import logging
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper()),
    )


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Polymarket Sports Market Maker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Paper trading
    python -m src.sports.sports_main --paper

    # Live trading (CAREFUL!)
    python -m src.sports.sports_main --live

    # Trade only NBA markets
    python -m src.sports.sports_main --paper --sport NBA

    # Custom config
    python -m src.sports.sports_main --config config/paper.yaml
        """,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Paper trading mode (default)",
    )
    mode_group.add_argument(
        "--live",
        action="store_true",
        help="Live trading mode - REAL MONEY AT RISK",
    )

    parser.add_argument(
        "--config",
        type=str,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--sport",
        type=str,
        choices=["NBA", "NFL", "MLB", "NHL", "SOCCER", "ALL"],
        default="ALL",
        help="Sport to trade (default: ALL)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        help="Minimum edge to trade (e.g., 0.02 for 2%%)",
    )
    parser.add_argument(
        "--max-position",
        type=float,
        help="Maximum position per market in USD",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    return parser.parse_args()


def print_banner(paper_trading: bool, sport: str):
    """Print startup banner."""
    mode = "PAPER TRADING" if paper_trading else "LIVE TRADING"
    print("\n" + "=" * 60)
    print("   POLYMARKET SPORTS MARKET MAKER")
    print("=" * 60)
    print(f"   Mode: {mode}")
    print(f"   Sport Filter: {sport}")

    if not paper_trading:
        print("\n   *** LIVE TRADING MODE ***")
        print("   *** REAL MONEY AT RISK ***")

    print("=" * 60 + "\n")


async def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging
    setup_logging(args.log_level)

    # Determine trading mode
    paper_trading = not args.live

    # Print banner
    print_banner(paper_trading, args.sport)

    # Confirmation for live trading
    if not paper_trading:
        confirm = input("Type 'CONFIRM' to proceed with live trading: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            return

    # Load config
    config = load_config(
        config_path=args.config,
        paper_trading=paper_trading,
    )

    # Override config from CLI
    if args.min_edge:
        config.min_edge_threshold = args.min_edge

    if args.max_position:
        config.max_position_size = args.max_position

    # Validate credentials for live trading
    if not paper_trading and not config.validate_credentials():
        logger.error(
            "missing_credentials",
            message="Set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS for live trading",
        )
        return

    # Log config
    logger.info(
        "config_loaded",
        paper_trading=config.paper_trading,
        min_edge=f"{config.min_edge_threshold:.2%}",
        max_position=f"${config.max_position_size}",
        max_total=f"${config.max_total_exposure}",
        refresh_interval=f"{config.quote_refresh_seconds}s",
    )

    # Run the bot
    await run_sports_market_maker(config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested...")
    except Exception as e:
        logger.exception("fatal_error", error=str(e))
        sys.exit(1)
