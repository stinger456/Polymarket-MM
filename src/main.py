"""
Entry point for Polymarket Hourly BTC Market Maker.
"""

import asyncio
import argparse
import sys
from pathlib import Path

from .config import Config, load_config
from .orchestrator import Orchestrator
from .utils.logging import setup_logging, get_logger


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Polymarket Hourly BTC Market Maker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run in paper trading mode (default)
  python -m src.main --paper

  # Run in live mode
  python -m src.main --live

  # Run with custom config
  python -m src.main --config config/production.yaml --live

  # Run with debug logging
  python -m src.main --log-level DEBUG
        """,
    )

    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Run in paper trading mode (default)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--event",
        type=str,
        default=None,
        help="Event slug to trade (e.g., 'bitcoin-up-or-down-january-22-8pm-et')",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Determine trading mode
    paper_trading = not args.live

    # Setup initial logging
    setup_logging(level=args.log_level)
    logger = get_logger(__name__)

    logger.info(
        "Starting Polymarket Hourly BTC Market Maker",
        paper_trading=paper_trading,
        config_file=args.config,
    )

    # Load configuration
    try:
        config = load_config(
            config_path=args.config,
            paper_trading=paper_trading,
            event_slug=args.event,
        )
    except Exception as e:
        logger.error("Failed to load config", error=str(e))
        sys.exit(1)

    # Validate credentials for live trading
    if not paper_trading and not config.validate_credentials():
        logger.error(
            "Missing credentials for live trading. "
            "Set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS environment variables."
        )
        sys.exit(1)

    # Print startup info
    trading_config = config.get_trading_config()
    risk_config = config.get_risk_config()

    logger.info(
        "Configuration loaded",
        min_edge=trading_config.min_edge_threshold,
        max_position=trading_config.max_position_size,
        max_exposure=trading_config.max_total_exposure,
        num_levels=trading_config.num_quote_levels,
        max_drawdown=risk_config.max_drawdown_pct,
        max_skew=risk_config.max_inventory_skew,
    )

    if paper_trading:
        logger.info("=" * 50)
        logger.info("PAPER TRADING MODE - NO REAL TRADES WILL BE EXECUTED")
        logger.info("=" * 50)
    else:
        logger.warning("=" * 50)
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK")
        logger.warning("=" * 50)

    # Create and run orchestrator
    orchestrator = Orchestrator(config)

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
