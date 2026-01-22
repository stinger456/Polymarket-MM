"""
Main orchestrator for the market making bot.

Coordinates all components and runs the main trading loop.
"""

import asyncio
import signal
from datetime import datetime, timezone
from typing import Optional
import structlog

from .config import Config, create_clob_client, create_read_only_client
from .market.discovery import MarketDiscovery
from .market.market_state import Market, MarketState
from .pricing.price_feed import BinancePriceFeed, MockPriceFeed
from .pricing.fair_value import PricingEngine, FairValueModel
from .quoting.quote_engine import QuoteEngine
from .quoting.inventory import InventoryManager
from .execution.clob_client import PolymarketCLOB
from .execution.order_manager import OrderManager
from .execution.fill_handler import FillHandler
from .risk.risk_manager import RiskManager
from .risk.position_tracker import PositionTracker
from .risk.pnl import PnLTracker
from .data.orderbook import OrderbookManager
from .data.websocket_manager import WebSocketManager
from .utils.metrics import MetricsCollector
from .utils.logging import setup_logging, get_logger


logger = structlog.get_logger(__name__)


class Orchestrator:
    """
    Main controller that coordinates all components.

    Loop:
    1. Find/track active market
    2. Update price feed
    3. Calculate fair value
    4. Generate quotes
    5. Update orders
    6. Handle fills
    7. Manage risk
    8. Rotate to next market when needed
    """

    def __init__(self, config: Config):
        """
        Initialize orchestrator.

        Args:
            config: Bot configuration
        """
        self.config = config
        self.running = False

        # Components (initialized in startup)
        self.market_discovery: Optional[MarketDiscovery] = None
        self.price_feed = None
        self.pricing_engine: Optional[PricingEngine] = None
        self.quote_engine: Optional[QuoteEngine] = None
        self.inventory_manager: Optional[InventoryManager] = None
        self.clob_client: Optional[PolymarketCLOB] = None
        self.order_manager: Optional[OrderManager] = None
        self.fill_handler: Optional[FillHandler] = None
        self.risk_manager: Optional[RiskManager] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.pnl_tracker: Optional[PnLTracker] = None
        self.orderbook_manager: Optional[OrderbookManager] = None
        self.ws_manager: Optional[WebSocketManager] = None
        self.metrics: Optional[MetricsCollector] = None

        # State
        self.current_market: Optional[Market] = None
        self.market_state: Optional[MarketState] = None
        self._iteration_count = 0

    async def startup(self):
        """Initialize all components."""
        logger.info(
            "Starting orchestrator",
            paper_trading=self.config.paper_trading,
        )

        # Setup logging
        setup_logging(level=self.config.log_level)

        # Initialize market discovery
        self.market_discovery = MarketDiscovery(self.config.gamma_url)

        # Initialize price feed
        if self.config.paper_trading:
            self.price_feed = MockPriceFeed()
        else:
            self.price_feed = BinancePriceFeed(
                volatility_window=self.config.pricing.volatility_window,
                volatility_scale=self.config.pricing.volatility_scale,
            )
        self.price_feed.start()

        # Initialize pricing engine
        self.pricing_engine = PricingEngine(FairValueModel())

        # Initialize quote engine
        trading_config = self.config.get_trading_config()
        self.quote_engine = QuoteEngine(
            base_half_spread=trading_config.min_edge_threshold / 2,
            num_levels=trading_config.num_quote_levels,
            level_spacing=trading_config.level_spacing,
            base_size=trading_config.base_order_size,
        )

        # Initialize inventory manager
        risk_config = self.config.get_risk_config()
        self.inventory_manager = InventoryManager(
            max_inventory_skew=risk_config.max_inventory_skew,
            max_position_size=trading_config.max_position_size,
        )

        # Initialize CLOB client
        if self.config.paper_trading:
            read_client = create_read_only_client(self.config)
            self.clob_client = PolymarketCLOB(
                client=read_client,
                paper_trading=True,
            )
        else:
            client = create_clob_client(self.config)
            self.clob_client = PolymarketCLOB(
                client=client,
                paper_trading=False,
            )

        # Initialize risk and position tracking
        self.position_tracker = PositionTracker()
        self.pnl_tracker = PnLTracker()
        self.risk_manager = RiskManager(
            position_tracker=self.position_tracker,
            pnl_tracker=self.pnl_tracker,
        )

        # Initialize fill handler
        self.fill_handler = FillHandler()
        self.fill_handler.add_callback(self._on_fill)

        # Initialize order manager
        self.order_manager = OrderManager(self.clob_client)

        # Initialize orderbook and WebSocket
        self.orderbook_manager = OrderbookManager()
        self.ws_manager = WebSocketManager(
            orderbook_manager=self.orderbook_manager,
            api_key=self.config.api_key,
            api_secret=self.config.api_secret,
            api_passphrase=self.config.api_passphrase,
        )

        # Initialize metrics
        self.metrics = MetricsCollector()

        # Wait for price feed
        if not self.price_feed.wait_for_price(timeout=30.0):
            logger.warning("Price feed not available, using default volatility")

        # Find initial market
        self.current_market = await self.market_discovery.get_active_hourly_btc_market()
        if self.current_market:
            self.market_state = MarketState(market=self.current_market)
            self.order_manager.set_market(
                self.current_market.yes_token_id,
                self.current_market.no_token_id,
            )

            # Start WebSocket for market data
            self.ws_manager.start_market_stream([
                self.current_market.yes_token_id,
                self.current_market.no_token_id,
            ])

            logger.info(
                "Found initial market",
                question=self.current_market.question,
                strike=self.current_market.strike_price,
                time_remaining=self.current_market.time_remaining_seconds,
            )
        else:
            logger.warning("No active hourly BTC market found")

        self.running = True
        logger.info("Orchestrator started")

    async def shutdown(self):
        """Gracefully shutdown all components."""
        logger.info("Shutting down orchestrator")
        self.running = False

        # Cancel all orders
        if self.order_manager:
            self.order_manager.cancel_all_orders()

        # Stop WebSocket
        if self.ws_manager:
            self.ws_manager.stop()

        # Stop price feed
        if self.price_feed:
            self.price_feed.stop()

        # Close market discovery client
        if self.market_discovery:
            await self.market_discovery.close()

        # Log final metrics
        if self.metrics and self.pnl_tracker:
            snapshot = self.pnl_tracker.get_snapshot()
            session = self.metrics.get_session_metrics(
                pnl=snapshot.total_pnl,
                hedged_pairs=snapshot.hedged_pairs,
                guaranteed_profit=snapshot.guaranteed_profit,
            )
            logger.info(
                "Session summary",
                total_trades=session.total_trades,
                total_volume=session.total_volume,
                pnl=session.total_pnl,
                hedged_pairs=session.hedged_pairs,
                guaranteed_profit=session.guaranteed_profit,
            )

        logger.info("Orchestrator shutdown complete")

    async def run(self):
        """Main event loop."""
        await self.startup()

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown()),
            )

        try:
            while self.running:
                await self.iteration()
                await asyncio.sleep(self.config.quote_refresh_seconds)
        except Exception as e:
            logger.error("Main loop error", error=str(e))
        finally:
            await self.shutdown()

    async def iteration(self):
        """Single iteration of the main loop."""
        self._iteration_count += 1

        try:
            # 1. Check if we need to rotate markets
            await self._check_market_rotation()

            if not self.current_market or not self.current_market.is_active:
                logger.debug("No active market")
                return

            # 2. Update market state from orderbook
            self._update_market_state()

            # 3. Check risk limits
            can_trade, reason = self.risk_manager.can_trade()
            if not can_trade:
                logger.warning("Trading paused", reason=reason)
                return

            # Check time remaining
            can_time, time_reason = self.risk_manager.check_market_time(
                self.current_market.time_remaining_seconds
            )
            if not can_time:
                logger.info("Not trading", reason=time_reason)
                return

            # 4. Get current price and volatility
            current_price = self.price_feed.current_price
            volatility = self.price_feed.volatility or 50.0

            if current_price is None:
                logger.warning("No price available")
                return

            # 5. Calculate fair value
            fair_yes, fair_no = self.pricing_engine.calculate_fair_value(
                current_price=current_price,
                strike_price=self.current_market.strike_price,
                time_remaining_seconds=self.current_market.time_remaining_seconds,
                volatility=volatility,
            )

            # 6. Generate quotes
            quotes = self.quote_engine.generate_quotes(
                fair_yes=fair_yes,
                fair_no=fair_no,
                inventory_skew=self.inventory_manager.skew,
                volatility=volatility,
                time_remaining_seconds=self.current_market.time_remaining_seconds,
                market_state=self.market_state,
            )

            # 7. Check if quotes have edge
            if quotes.implied_edge and quotes.implied_edge > 0:
                logger.info(
                    "Quote update",
                    fair_yes=f"{fair_yes:.4f}",
                    fair_no=f"{fair_no:.4f}",
                    yes_bid=f"{quotes.best_yes_bid:.4f}" if quotes.best_yes_bid else None,
                    no_bid=f"{quotes.best_no_bid:.4f}" if quotes.best_no_bid else None,
                    implied_edge=f"{quotes.implied_edge:.4f}",
                    btc_price=f"{current_price:.0f}",
                    time_remaining=self.current_market.time_remaining_seconds,
                )

                # 8. Update orders
                result = self.order_manager.update_quotes(quotes)
                self.metrics.record_quote()

                for _ in result["placed"]:
                    self.metrics.record_order_placed()
                for _ in result["cancelled"]:
                    self.metrics.record_order_cancelled()
            else:
                logger.debug(
                    "No edge, skipping quotes",
                    fair_yes=fair_yes,
                    fair_no=fair_no,
                )

        except Exception as e:
            logger.error("Iteration error", error=str(e), iteration=self._iteration_count)

    async def _check_market_rotation(self):
        """Switch to new market if current one is expiring."""
        if self.current_market is None:
            # Try to find a market
            self.current_market = await self.market_discovery.get_active_hourly_btc_market()
            if self.current_market:
                self._setup_new_market(self.current_market)
            return

        min_time = self.config.get_risk_config().min_time_remaining

        if self.current_market.time_remaining_seconds < min_time:
            logger.info(
                "Market expiring, rotating",
                current_market=self.current_market.question[:50],
                time_remaining=self.current_market.time_remaining_seconds,
            )

            # Cancel all orders
            self.order_manager.cancel_all_orders()

            # Find next market
            next_market = await self.market_discovery.get_active_hourly_btc_market()

            if next_market and next_market.condition_id != self.current_market.condition_id:
                self._setup_new_market(next_market)
                logger.info(
                    "Rotated to new market",
                    question=next_market.question[:50],
                    strike=next_market.strike_price,
                )
            else:
                logger.warning("No new market found for rotation")
                self.current_market = None

    def _setup_new_market(self, market: Market):
        """Setup components for a new market."""
        self.current_market = market
        self.market_state = MarketState(market=market)

        self.order_manager.set_market(
            market.yes_token_id,
            market.no_token_id,
        )
        self.order_manager.reset()

        self.inventory_manager.reset()

        # Update WebSocket subscriptions
        self.ws_manager.market_ws.subscribe([
            market.yes_token_id,
            market.no_token_id,
        ])

    def _update_market_state(self):
        """Update market state from orderbook data."""
        if not self.current_market or not self.market_state:
            return

        yes_book = self.orderbook_manager.get_orderbook(
            self.current_market.yes_token_id
        )
        no_book = self.orderbook_manager.get_orderbook(
            self.current_market.no_token_id
        )

        if yes_book and no_book:
            self.market_state.update_orderbook(
                yes_bids=[(l.price, l.size) for l in yes_book.bids],
                yes_asks=[(l.price, l.size) for l in yes_book.asks],
                no_bids=[(l.price, l.size) for l in no_book.bids],
                no_asks=[(l.price, l.size) for l in no_book.asks],
            )

    def _on_fill(self, fill):
        """Handle a fill event."""
        if not self.current_market:
            return

        # Update inventory
        if fill.token_type.value == "YES":
            self.inventory_manager.add_yes_fill(fill.size, fill.price)
        else:
            self.inventory_manager.add_no_fill(fill.size, fill.price)

        # Update P&L tracker
        self.pnl_tracker.record_trade(
            token_type=fill.token_type.value,
            side=fill.side.value,
            price=fill.price,
            size=fill.size,
            fee=fill.fee,
        )

        # Update position tracker
        self.position_tracker.add_fill(
            market_id=self.current_market.condition_id,
            token_type=fill.token_type.value,
            shares=fill.size,
            price=fill.price,
        )

        # Update metrics
        self.metrics.record_trade(
            token_type=fill.token_type.value,
            side=fill.side.value,
            price=fill.price,
            size=fill.size,
            is_maker=fill.is_maker,
        )

        # Update risk manager
        self.risk_manager.on_trade()

        # Log fill
        logger.info(
            "Fill processed",
            token_type=fill.token_type.value,
            side=fill.side.value,
            price=fill.price,
            size=fill.size,
            hedged_pairs=self.inventory_manager.hedged_shares,
            guaranteed_profit=self.pnl_tracker.guaranteed_profit,
        )
