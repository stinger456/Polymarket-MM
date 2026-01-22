"""
Quote generation engine for market making.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import structlog

from .spread_calc import SpreadCalculator, LevelSpacingCalculator, SizeCalculator
from .inventory import InventoryManager
from ..market.market_state import MarketState


logger = structlog.get_logger(__name__)


@dataclass
class Quote:
    """Single quote (price, size)."""
    price: float
    size: float

    def __str__(self) -> str:
        return f"Quote(price={self.price:.4f}, size={self.size:.1f})"


@dataclass
class QuoteSet:
    """
    Complete set of quotes for a market.

    Contains bids and asks for both YES and NO tokens.
    """
    yes_bids: List[Quote] = field(default_factory=list)
    yes_asks: List[Quote] = field(default_factory=list)
    no_bids: List[Quote] = field(default_factory=list)
    no_asks: List[Quote] = field(default_factory=list)

    def add_yes_bid(self, price: float, size: float):
        """Add a YES bid."""
        if 0 < price < 1 and size > 0:
            self.yes_bids.append(Quote(price=price, size=size))

    def add_yes_ask(self, price: float, size: float):
        """Add a YES ask."""
        if 0 < price < 1 and size > 0:
            self.yes_asks.append(Quote(price=price, size=size))

    def add_no_bid(self, price: float, size: float):
        """Add a NO bid."""
        if 0 < price < 1 and size > 0:
            self.no_bids.append(Quote(price=price, size=size))

    def add_no_ask(self, price: float, size: float):
        """Add a NO ask."""
        if 0 < price < 1 and size > 0:
            self.no_asks.append(Quote(price=price, size=size))

    @property
    def best_yes_bid(self) -> Optional[float]:
        """Best (highest) YES bid price."""
        return max((q.price for q in self.yes_bids), default=None)

    @property
    def best_no_bid(self) -> Optional[float]:
        """Best (highest) NO bid price."""
        return max((q.price for q in self.no_bids), default=None)

    @property
    def total_yes_bid_size(self) -> float:
        """Total size on YES bids."""
        return sum(q.size for q in self.yes_bids)

    @property
    def total_no_bid_size(self) -> float:
        """Total size on NO bids."""
        return sum(q.size for q in self.no_bids)

    @property
    def implied_edge(self) -> Optional[float]:
        """
        Implied edge if both best bids fill.

        Edge = 1.0 - (yes_bid + no_bid)
        Positive = profit, negative = loss
        """
        if self.best_yes_bid is None or self.best_no_bid is None:
            return None
        return 1.0 - self.best_yes_bid - self.best_no_bid

    def __str__(self) -> str:
        return (
            f"QuoteSet(\n"
            f"  YES bids: {[str(q) for q in self.yes_bids]}\n"
            f"  YES asks: {[str(q) for q in self.yes_asks]}\n"
            f"  NO bids: {[str(q) for q in self.no_bids]}\n"
            f"  NO asks: {[str(q) for q in self.no_asks]}\n"
            f"  Implied edge: {self.implied_edge}\n"
            f")"
        )


class QuoteEngine:
    """
    Generates bid/ask quotes for both YES and NO tokens.

    Quote logic:
    1. Start with fair value from pricing model
    2. Add half-spread to create bid/ask
    3. Adjust for inventory (skew away from overweight side)
    4. Generate multiple price levels
    """

    def __init__(
        self,
        base_half_spread: float = 0.01,
        num_levels: int = 3,
        level_spacing: float = 0.01,
        base_size: float = 50.0,
        min_size: float = 5.0,
        size_decay: float = 0.6,
        inventory_scale: float = 0.02,
        min_price: float = 0.02,
        max_price: float = 0.98,
    ):
        """
        Initialize quote engine.

        Args:
            base_half_spread: Base half-spread for quotes
            num_levels: Number of price levels per side
            level_spacing: Base spacing between levels
            base_size: Size at best level
            min_size: Minimum order size
            size_decay: Size decay per level
            inventory_scale: Scale for inventory adjustments
            min_price: Minimum quote price
            max_price: Maximum quote price
        """
        self.base_half_spread = base_half_spread
        self.num_levels = num_levels
        self.min_price = min_price
        self.max_price = max_price

        self.spread_calc = SpreadCalculator(
            base_half_spread=base_half_spread,
            inventory_scale=inventory_scale,
        )
        self.spacing_calc = LevelSpacingCalculator(
            base_spacing=level_spacing,
        )
        self.size_calc = SizeCalculator(
            base_size=base_size,
            min_size=min_size,
            size_decay=size_decay,
        )

    def generate_quotes(
        self,
        fair_yes: float,
        fair_no: float,
        inventory_skew: float = 0.0,
        volatility: Optional[float] = None,
        time_remaining_seconds: Optional[int] = None,
        market_state: Optional[MarketState] = None,
    ) -> QuoteSet:
        """
        Generate full quote set for both tokens.

        Args:
            fair_yes: Fair value for YES token
            fair_no: Fair value for NO token
            inventory_skew: Current inventory skew (-1 to 1)
            volatility: Current volatility
            time_remaining_seconds: Time to expiry
            market_state: Current market state

        Returns:
            QuoteSet with quotes for both tokens
        """
        quotes = QuoteSet()

        # Calculate dynamic half-spread
        half_spread = self.spread_calc.calculate_half_spread(
            volatility=volatility,
            time_remaining_seconds=time_remaining_seconds,
            inventory_skew=inventory_skew,
        )

        # Get inventory adjustments
        yes_adj, no_adj = self.spread_calc.calculate_asymmetric_spread(
            inventory_skew=inventory_skew,
            volatility=volatility,
            time_remaining_seconds=time_remaining_seconds,
        )

        # Get level offsets and sizes
        offsets = self.spacing_calc.get_level_offsets(self.num_levels)
        sizes = self.size_calc.get_level_sizes(self.num_levels)

        # Generate YES quotes
        for level, (offset, size) in enumerate(zip(offsets, sizes)):
            # YES bid: fair - spread - offset - inventory_adj
            yes_bid_price = fair_yes - half_spread - offset + yes_adj
            yes_bid_price = max(self.min_price, min(self.max_price, yes_bid_price))

            # YES ask: fair + spread + offset - inventory_adj
            yes_ask_price = fair_yes + half_spread + offset + yes_adj
            yes_ask_price = max(self.min_price, min(self.max_price, yes_ask_price))

            quotes.add_yes_bid(yes_bid_price, size)
            quotes.add_yes_ask(yes_ask_price, size)

        # Generate NO quotes
        for level, (offset, size) in enumerate(zip(offsets, sizes)):
            # NO bid: fair - spread - offset + inventory_adj
            no_bid_price = fair_no - half_spread - offset + no_adj
            no_bid_price = max(self.min_price, min(self.max_price, no_bid_price))

            # NO ask: fair + spread + offset + inventory_adj
            no_ask_price = fair_no + half_spread + offset + no_adj
            no_ask_price = max(self.min_price, min(self.max_price, no_ask_price))

            quotes.add_no_bid(no_bid_price, size)
            quotes.add_no_ask(no_ask_price, size)

        # Validate combined quotes make sense
        self._validate_quotes(quotes, fair_yes, fair_no)

        return quotes

    def _validate_quotes(
        self,
        quotes: QuoteSet,
        fair_yes: float,
        fair_no: float,
    ):
        """
        Validate quotes are sensible.

        Logs warnings if quotes don't make economic sense.
        """
        # Best bids should sum to less than 1.0 for profit
        if quotes.best_yes_bid and quotes.best_no_bid:
            combined = quotes.best_yes_bid + quotes.best_no_bid
            if combined >= 1.0:
                logger.warning(
                    "Combined bids >= 1.0 (unprofitable)",
                    combined=combined,
                    yes_bid=quotes.best_yes_bid,
                    no_bid=quotes.best_no_bid,
                )

        # Bids should be below fair value
        if quotes.best_yes_bid and quotes.best_yes_bid >= fair_yes:
            logger.warning(
                "YES bid >= fair value",
                bid=quotes.best_yes_bid,
                fair=fair_yes,
            )

        if quotes.best_no_bid and quotes.best_no_bid >= fair_no:
            logger.warning(
                "NO bid >= fair value",
                bid=quotes.best_no_bid,
                fair=fair_no,
            )

    def generate_aggressive_hedge_quotes(
        self,
        fair_yes: float,
        fair_no: float,
        inventory_skew: float,
        target_size: float,
    ) -> QuoteSet:
        """
        Generate aggressive quotes to reduce inventory imbalance.

        Used when inventory is over the skew limit.

        Args:
            fair_yes: Fair value for YES
            fair_no: Fair value for NO
            inventory_skew: Current skew
            target_size: Size to hedge

        Returns:
            QuoteSet with aggressive hedging quotes
        """
        quotes = QuoteSet()

        if inventory_skew > 0:
            # Long YES - need to buy NO aggressively
            # Bid at or above fair value
            no_bid = fair_no + 0.01  # Slight premium
            no_bid = min(self.max_price, no_bid)
            quotes.add_no_bid(no_bid, target_size)

        elif inventory_skew < 0:
            # Long NO - need to buy YES aggressively
            yes_bid = fair_yes + 0.01
            yes_bid = min(self.max_price, yes_bid)
            quotes.add_yes_bid(yes_bid, target_size)

        return quotes

    @classmethod
    def from_config(cls, config) -> "QuoteEngine":
        """
        Create QuoteEngine from config object.

        Args:
            config: Config with trading parameters

        Returns:
            Configured QuoteEngine
        """
        trading = config.get_trading_config()
        return cls(
            base_half_spread=trading.min_edge_threshold / 2,
            num_levels=trading.num_quote_levels,
            level_spacing=trading.level_spacing,
            base_size=trading.base_order_size,
        )
