"""
Main orchestrator for sports market making.

Coordinates all components:
- Market discovery
- Orderbook tracking
- Quote generation
- Order management
- Position tracking
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Set
import structlog

from src.config import Config, create_clob_client, create_read_only_client
from src.sports.sports_discovery import SportMarket, get_all_sports_markets, get_game_markets
from src.sports.sports_orderbook import OrderbookManager, Orderbook
from src.sports.sports_quote_engine import QuoteEngine, TwoSidedQuote, Position
from src.sports.sports_order_manager import OrderManager, Order
from src.sports.sports_position_tracker import PositionTracker

logger = structlog.get_logger()


class SportsMarketMaker:
    """
    Main market making bot for Polymarket sports markets.

    Trading loop:
    1. Discover active sports markets
    2. Subscribe to orderbook updates
    3. Generate quotes based on fair value
    4. Place orders on both sides
    5. Track fills and update positions
    6. Manage risk and inventory
    """

    def __init__(self, config: Config):
        """
        Initialize sports market maker.

        Args:
            config: Configuration object
        """
        self.config = config

        # Create CLOB client
        if config.paper_trading:
            self.client = create_read_only_client(config)
        else:
            self.client = create_clob_client(config)

        # Initialize components
        self.quote_engine = QuoteEngine(
            min_edge=config.min_edge_threshold,
            default_size=config.base_order_size,
            max_position=config.max_position_size,
        )

        self.order_manager = OrderManager(
            client=self.client,
            paper_trading=config.paper_trading,
        )

        self.position_tracker = PositionTracker(
            max_position=config.max_position_size,
            max_total=config.max_total_exposure,
        )

        self.orderbook_manager = OrderbookManager(
            on_update=self._on_orderbook_update
        )

        # State
        self.markets: Dict[str, SportMarket] = {}
        self.current_quotes: Dict[str, TwoSidedQuote] = {}
        self.running = False
        self._subscribed_tokens: Set[str] = set()

        # Trading parameters
        self.refresh_interval = config.quote_refresh_seconds
        self.market_refresh_interval = 300  # 5 minutes

    async def start(self):
        """Start the market maker."""
        logger.info(
            "starting_sports_market_maker",
            paper_trading=self.config.paper_trading,
            min_edge=f"{self.config.min_edge_threshold:.2%}",
            max_position=f"${self.config.max_position_size}",
        )

        self.running = True

        # Initial market discovery
        await self._refresh_markets()

        # Start main loop
        await self._run_loop()

    async def stop(self):
        """Stop the market maker."""
        logger.info("stopping_sports_market_maker")
        self.running = False

        # Cancel all orders
        cancelled = await self.order_manager.cancel_all_orders()
        logger.info("orders_cancelled_on_stop", count=cancelled)

        # Close orderbook manager
        self.orderbook_manager.close()

        # Log final stats
        self._log_final_stats()

    async def _run_loop(self):
        """Main trading loop."""
        last_market_refresh = datetime.now()

        while self.running:
            try:
                # Refresh markets periodically
                elapsed = (datetime.now() - last_market_refresh).total_seconds()
                if elapsed > self.market_refresh_interval:
                    await self._refresh_markets()
                    last_market_refresh = datetime.now()

                # Run trading iteration
                await self._trading_iteration()

                # Wait for next cycle
                await asyncio.sleep(self.refresh_interval)

            except asyncio.CancelledError:
                logger.info("trading_loop_cancelled")
                break
            except Exception as e:
                logger.error("trading_loop_error", error=str(e))
                await asyncio.sleep(5)  # Brief pause on error

    async def _refresh_markets(self):
        """Refresh list of available markets."""
        try:
            # Get all sports markets
            markets = await get_all_sports_markets()

            # Filter to TODAY's games only (not started yet)
            tradeable = []
            for market in markets:
                # Skip if no token IDs
                if not market.yes_token_id or not market.no_token_id:
                    continue

                # Only trade TODAY's games that haven't started
                if not market.is_tradeable:
                    continue

                tradeable.append(market)

            logger.info(
                "todays_games_found",
                total_fetched=len(markets),
                todays_games=len(tradeable),
            )

            # Update market dict
            old_markets = set(self.markets.keys())
            self.markets = {m.market_id: m for m in tradeable}
            new_markets = set(self.markets.keys())

            # Log changes
            added = new_markets - old_markets
            removed = old_markets - new_markets

            logger.info(
                "markets_refreshed",
                total=len(self.markets),
                added=len(added),
                removed=len(removed),
            )

            # Update orderbook subscriptions
            await self._update_subscriptions()

        except Exception as e:
            logger.error("market_refresh_error", error=str(e))

    async def _update_subscriptions(self):
        """Update orderbook subscriptions."""
        # Get all token IDs we need
        needed_tokens = set()
        for market in self.markets.values():
            needed_tokens.add(market.yes_token_id)
            needed_tokens.add(market.no_token_id)

        # Only resubscribe if tokens changed
        if needed_tokens != self._subscribed_tokens:
            self._subscribed_tokens = needed_tokens

            if needed_tokens:
                self.orderbook_manager.subscribe(list(needed_tokens))
                logger.info("orderbook_subscriptions_updated", count=len(needed_tokens))

    async def _trading_iteration(self):
        """Single iteration of the trading loop."""
        if not self.markets:
            logger.debug("no_markets_to_trade")
            return

        for market_id, market in self.markets.items():
            try:
                await self._process_market(market)
            except Exception as e:
                logger.error(
                    "market_processing_error",
                    market_id=market_id,
                    error=str(e),
                )

    async def _process_market(self, market: SportMarket):
        """Process a single market."""
        # Get orderbooks
        home_book = self.orderbook_manager.get_orderbook(market.yes_token_id)
        away_book = self.orderbook_manager.get_orderbook(market.no_token_id)

        # Get current position
        position = self.position_tracker.get_position(market.market_id)
        pos = position.to_position() if position else Position()

        # Generate quotes
        quotes = self.quote_engine.generate_quotes(
            market=market,
            home_best_bid=home_book.best_bid if home_book else None,
            home_best_ask=home_book.best_ask if home_book else None,
            away_best_bid=away_book.best_bid if away_book else None,
            away_best_ask=away_book.best_ask if away_book else None,
            position=pos,
        )

        # Check if we should update quotes
        current = self.current_quotes.get(market.market_id)
        if current and not self.quote_engine.should_requote(current, quotes):
            return

        # Update quotes
        self.current_quotes[market.market_id] = quotes

        # Skip if no edge
        if not quotes.has_edge:
            return

        # Cancel existing orders for this market
        await self.order_manager.cancel_market_orders(market.market_id)

        # Place new orders
        await self._place_quotes(market, quotes)

    async def _place_quotes(self, market: SportMarket, quotes: TwoSidedQuote):
        """Place quote orders."""
        # Check position limits
        can_add_home, reason = self.position_tracker.can_add_position(
            market.market_id, "home", quotes.home_bid.size if quotes.home_bid else 0
        )
        can_add_away, reason = self.position_tracker.can_add_position(
            market.market_id, "away", quotes.away_bid.size if quotes.away_bid else 0
        )

        orders_placed = 0

        # Place home bid (buy YES)
        if quotes.home_bid and can_add_home:
            order_id = await self.order_manager.place_order(
                token_id=market.yes_token_id,
                market_id=market.market_id,
                side="BUY",
                price=quotes.home_bid.price,
                size=quotes.home_bid.size,
            )
            if order_id:
                orders_placed += 1

        # Place away bid (buy NO)
        if quotes.away_bid and can_add_away:
            order_id = await self.order_manager.place_order(
                token_id=market.no_token_id,
                market_id=market.market_id,
                side="BUY",
                price=quotes.away_bid.price,
                size=quotes.away_bid.size,
            )
            if order_id:
                orders_placed += 1

        if orders_placed > 0:
            logger.info(
                "quotes_placed",
                market=market.question[:40],
                orders=orders_placed,
                edge=f"{quotes.edge:.2%}",
            )

    def _on_orderbook_update(self, token_id: str, book: Orderbook):
        """Handle orderbook update callback."""
        # Could trigger re-quoting here for faster response
        pass

    def _log_final_stats(self):
        """Log final statistics."""
        order_stats = self.order_manager.get_stats()
        position_summary = self.position_tracker.get_summary()

        logger.info(
            "final_stats",
            **order_stats,
            **position_summary,
        )


async def run_sports_market_maker(config: Config):
    """
    Run the sports market maker.

    Args:
        config: Configuration object
    """
    bot = SportsMarketMaker(config)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        await bot.stop()


if __name__ == "__main__":
    # Simple test run
    import sys
    sys.path.insert(0, ".")

    from src.config import load_config

    config = load_config(paper_trading=True)
    asyncio.run(run_sports_market_maker(config))
