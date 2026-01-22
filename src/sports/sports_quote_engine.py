"""
Quote generation for sports markets.

Generates bid/ask quotes for both sides of a game market.

Strategy:
1. Get fair value from market mid or sharp book odds
2. Quote around fair value with spread
3. Adjust for inventory to minimize risk
4. Ensure total cost (buy both sides) < $1 for guaranteed profit
"""
from dataclasses import dataclass
from typing import Tuple, Optional, List
import structlog

from src.sports.sports_discovery import SportMarket

logger = structlog.get_logger()


@dataclass
class Quote:
    """A single quote (price + size)."""
    price: float
    size: float

    def __post_init__(self):
        # Ensure price is in valid range
        self.price = max(0.01, min(0.99, self.price))
        self.size = max(0, self.size)


@dataclass
class TwoSidedQuote:
    """Quotes for both sides of a market."""
    # Home team (YES token) quotes
    home_bid: Optional[Quote]  # Buy home team
    home_ask: Optional[Quote]  # Sell home team

    # Away team (NO token) quotes
    away_bid: Optional[Quote]  # Buy away team
    away_ask: Optional[Quote]  # Sell away team

    # Fair values
    fair_home: float
    fair_away: float

    # Edge calculation
    edge: float  # $1 - (best_ask_home + best_ask_away)

    # Market info
    market_id: str = ""
    spread: float = 0.0

    @property
    def can_trade(self) -> bool:
        """Check if we have any valid quotes."""
        return any([
            self.home_bid is not None,
            self.home_ask is not None,
            self.away_bid is not None,
            self.away_ask is not None,
        ])

    @property
    def has_edge(self) -> bool:
        """Check if there's positive edge."""
        return self.edge > 0


@dataclass
class Position:
    """Position in a market."""
    home_shares: float = 0.0  # YES token shares
    away_shares: float = 0.0  # NO token shares

    @property
    def net_position(self) -> float:
        """Net position (positive = long home, negative = long away)."""
        return self.home_shares - self.away_shares

    @property
    def hedged_shares(self) -> float:
        """Number of fully hedged shares."""
        return min(self.home_shares, self.away_shares)

    @property
    def inventory_skew(self) -> float:
        """Inventory skew (-1 to 1). 0 = balanced."""
        total = self.home_shares + self.away_shares
        if total == 0:
            return 0.0
        return (self.home_shares - self.away_shares) / total


class QuoteEngine:
    """
    Generates quotes for sports markets.

    Key principles:
    1. Always try to be market maker (post, don't cross)
    2. Quote both sides to capture spread
    3. If both sides fill, you're hedged with guaranteed profit
    4. Adjust inventory to prevent large one-sided exposure
    """

    def __init__(
        self,
        min_edge: float = 0.02,
        default_size: float = 100.0,
        max_position: float = 500.0,
        spread_bps: float = 100,  # 1% = 100 bps
    ):
        """
        Initialize quote engine.

        Args:
            min_edge: Minimum edge required to quote (0.02 = 2%)
            default_size: Default order size in USD
            max_position: Maximum position per side
            spread_bps: Spread in basis points
        """
        self.min_edge = min_edge
        self.default_size = default_size
        self.max_position = max_position
        self.spread_bps = spread_bps

    def calculate_fair_value(
        self,
        market: SportMarket,
        home_best_bid: Optional[float],
        home_best_ask: Optional[float],
        away_best_bid: Optional[float],
        away_best_ask: Optional[float],
        sharp_home_prob: Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        Calculate fair value for home and away.

        Priority:
        1. Sharp book odds (if provided)
        2. Market mid-point
        3. Current Polymarket prices

        Returns:
            (fair_home, fair_away) probabilities that sum to ~1
        """
        # Use sharp odds if available (from external sources like Pinnacle)
        if sharp_home_prob is not None:
            fair_home = max(0.01, min(0.99, sharp_home_prob))
            return fair_home, 1 - fair_home

        # Calculate from orderbook mid
        home_mid = None
        if home_best_bid is not None and home_best_ask is not None:
            home_mid = (home_best_bid + home_best_ask) / 2
        elif home_best_bid is not None:
            home_mid = home_best_bid
        elif home_best_ask is not None:
            home_mid = home_best_ask
        else:
            home_mid = market.home_price

        away_mid = None
        if away_best_bid is not None and away_best_ask is not None:
            away_mid = (away_best_bid + away_best_ask) / 2
        elif away_best_bid is not None:
            away_mid = away_best_bid
        elif away_best_ask is not None:
            away_mid = away_best_ask
        else:
            away_mid = market.away_price

        # Normalize to sum to 1
        total = home_mid + away_mid
        if total > 0:
            fair_home = home_mid / total
            fair_away = away_mid / total
        else:
            fair_home = 0.5
            fair_away = 0.5

        return fair_home, fair_away

    def calculate_edge(
        self,
        home_best_ask: Optional[float],
        away_best_ask: Optional[float],
    ) -> float:
        """
        Calculate edge available in the market.

        Edge = $1.00 - (cost to buy both sides)

        If edge > 0, buying both sides guarantees profit.

        Returns:
            Edge as a decimal (0.02 = 2%)
        """
        if home_best_ask is None or away_best_ask is None:
            return 0.0

        total_cost = home_best_ask + away_best_ask
        return max(0, 1.0 - total_cost)

    def calculate_spread(self, fair_price: float) -> float:
        """
        Calculate spread based on fair price.

        Wider spread near extremes (0.1 or 0.9) to compensate for gamma.

        Returns:
            Half-spread to apply on each side
        """
        base_spread = self.spread_bps / 10000  # Convert bps to decimal

        # Widen spread near extremes
        distance_from_center = abs(fair_price - 0.5)
        extreme_factor = 1 + distance_from_center  # Up to 1.5x at extremes

        return (base_spread * extreme_factor) / 2

    def calculate_inventory_adjustment(
        self,
        position: Position,
        max_skew: float = 0.6,
    ) -> float:
        """
        Calculate price adjustment based on inventory.

        Positive adjustment = raise prices (we're long home)
        Negative adjustment = lower prices (we're long away)

        Returns:
            Price adjustment to apply
        """
        skew = position.inventory_skew

        if abs(skew) < 0.1:
            return 0.0

        # Linear adjustment up to 2% at max skew
        max_adjustment = 0.02
        return skew * max_adjustment

    def calculate_size(
        self,
        position: Position,
        side: str,  # "home" or "away"
        default_size: Optional[float] = None,
    ) -> float:
        """
        Calculate order size based on position.

        Reduces size when approaching position limits.

        Returns:
            Order size in USD
        """
        if default_size is None:
            default_size = self.default_size

        current = position.home_shares if side == "home" else position.away_shares
        remaining = self.max_position - current

        if remaining <= 0:
            return 0.0

        # Taper size as we approach limit
        if remaining < default_size:
            return remaining

        return default_size

    def generate_quotes(
        self,
        market: SportMarket,
        home_best_bid: Optional[float],
        home_best_ask: Optional[float],
        away_best_bid: Optional[float],
        away_best_ask: Optional[float],
        position: Optional[Position] = None,
        sharp_home_prob: Optional[float] = None,
    ) -> TwoSidedQuote:
        """
        Generate two-sided quotes for a market.

        Args:
            market: SportMarket to quote
            home_best_bid: Best bid for home (YES) token
            home_best_ask: Best ask for home (YES) token
            away_best_bid: Best bid for away (NO) token
            away_best_ask: Best ask for away (NO) token
            position: Current position in this market
            sharp_home_prob: Fair probability from sharp book

        Returns:
            TwoSidedQuote with bid/ask for both sides
        """
        if position is None:
            position = Position()

        # Calculate fair values
        fair_home, fair_away = self.calculate_fair_value(
            market, home_best_bid, home_best_ask,
            away_best_bid, away_best_ask, sharp_home_prob
        )

        # Calculate edge
        edge = self.calculate_edge(home_best_ask, away_best_ask)

        # Calculate spread
        home_spread = self.calculate_spread(fair_home)
        away_spread = self.calculate_spread(fair_away)

        # Calculate inventory adjustment
        inv_adj = self.calculate_inventory_adjustment(position)

        # Generate quote prices
        home_bid_price = fair_home - home_spread - inv_adj
        home_ask_price = fair_home + home_spread - inv_adj
        away_bid_price = fair_away - away_spread + inv_adj
        away_ask_price = fair_away + away_spread + inv_adj

        # Clamp to valid range
        home_bid_price = max(0.01, min(0.99, home_bid_price))
        home_ask_price = max(0.01, min(0.99, home_ask_price))
        away_bid_price = max(0.01, min(0.99, away_bid_price))
        away_ask_price = max(0.01, min(0.99, away_ask_price))

        # Calculate sizes
        home_bid_size = self.calculate_size(position, "home")
        away_bid_size = self.calculate_size(position, "away")
        home_ask_size = self.default_size  # Can always sell what we have
        away_ask_size = self.default_size

        # Don't quote if no edge
        if edge < self.min_edge:
            logger.debug(
                "no_edge_available",
                market=market.question[:40],
                edge=f"{edge:.2%}",
                min_edge=f"{self.min_edge:.2%}",
            )
            return TwoSidedQuote(
                home_bid=None,
                home_ask=None,
                away_bid=None,
                away_ask=None,
                fair_home=fair_home,
                fair_away=fair_away,
                edge=edge,
                market_id=market.market_id,
                spread=home_spread + away_spread,
            )

        # Create quotes
        home_bid = Quote(home_bid_price, home_bid_size) if home_bid_size > 0 else None
        home_ask = Quote(home_ask_price, home_ask_size) if home_ask_size > 0 else None
        away_bid = Quote(away_bid_price, away_bid_size) if away_bid_size > 0 else None
        away_ask = Quote(away_ask_price, away_ask_size) if away_ask_size > 0 else None

        logger.info(
            "quotes_generated",
            market=market.question[:40],
            fair_home=f"{fair_home:.3f}",
            fair_away=f"{fair_away:.3f}",
            edge=f"{edge:.2%}",
            home_bid=f"{home_bid.price:.3f}" if home_bid else "-",
            away_bid=f"{away_bid.price:.3f}" if away_bid else "-",
        )

        return TwoSidedQuote(
            home_bid=home_bid,
            home_ask=home_ask,
            away_bid=away_bid,
            away_ask=away_ask,
            fair_home=fair_home,
            fair_away=fair_away,
            edge=edge,
            market_id=market.market_id,
            spread=home_spread + away_spread,
        )

    def should_requote(
        self,
        current_quotes: TwoSidedQuote,
        new_quotes: TwoSidedQuote,
        price_threshold: float = 0.005,  # 0.5% price change
    ) -> bool:
        """
        Check if we should update quotes.

        Only requote if prices have moved significantly.

        Returns:
            True if quotes should be updated
        """
        def price_changed(old: Optional[Quote], new: Optional[Quote]) -> bool:
            if old is None and new is None:
                return False
            if old is None or new is None:
                return True
            return abs(old.price - new.price) > price_threshold

        return any([
            price_changed(current_quotes.home_bid, new_quotes.home_bid),
            price_changed(current_quotes.home_ask, new_quotes.home_ask),
            price_changed(current_quotes.away_bid, new_quotes.away_bid),
            price_changed(current_quotes.away_ask, new_quotes.away_ask),
        ])


class MultiLevelQuoteEngine(QuoteEngine):
    """
    Extended quote engine that generates multiple price levels.

    Places orders at multiple price levels for better fill rates.
    """

    def __init__(
        self,
        num_levels: int = 3,
        level_spacing: float = 0.01,
        size_decay: float = 0.5,
        **kwargs,
    ):
        """
        Initialize multi-level quote engine.

        Args:
            num_levels: Number of price levels per side
            level_spacing: Price spacing between levels
            size_decay: Size reduction per level (0.5 = 50% of previous)
            **kwargs: Arguments passed to base QuoteEngine
        """
        super().__init__(**kwargs)
        self.num_levels = num_levels
        self.level_spacing = level_spacing
        self.size_decay = size_decay

    def generate_multi_level_quotes(
        self,
        market: SportMarket,
        home_best_bid: Optional[float],
        home_best_ask: Optional[float],
        away_best_bid: Optional[float],
        away_best_ask: Optional[float],
        position: Optional[Position] = None,
        sharp_home_prob: Optional[float] = None,
    ) -> List[TwoSidedQuote]:
        """
        Generate quotes at multiple price levels.

        Returns:
            List of TwoSidedQuote objects, one per level
        """
        # Get base quote
        base_quote = self.generate_quotes(
            market, home_best_bid, home_best_ask,
            away_best_bid, away_best_ask, position, sharp_home_prob
        )

        if not base_quote.can_trade:
            return [base_quote]

        quotes = [base_quote]

        # Generate additional levels
        for level in range(1, self.num_levels):
            offset = level * self.level_spacing
            size_factor = self.size_decay ** level

            # Widen prices from base
            home_bid = None
            if base_quote.home_bid:
                home_bid = Quote(
                    base_quote.home_bid.price - offset,
                    base_quote.home_bid.size * size_factor
                )

            home_ask = None
            if base_quote.home_ask:
                home_ask = Quote(
                    base_quote.home_ask.price + offset,
                    base_quote.home_ask.size * size_factor
                )

            away_bid = None
            if base_quote.away_bid:
                away_bid = Quote(
                    base_quote.away_bid.price - offset,
                    base_quote.away_bid.size * size_factor
                )

            away_ask = None
            if base_quote.away_ask:
                away_ask = Quote(
                    base_quote.away_ask.price + offset,
                    base_quote.away_ask.size * size_factor
                )

            quotes.append(TwoSidedQuote(
                home_bid=home_bid,
                home_ask=home_ask,
                away_bid=away_bid,
                away_ask=away_ask,
                fair_home=base_quote.fair_home,
                fair_away=base_quote.fair_away,
                edge=base_quote.edge,
                market_id=market.market_id,
                spread=base_quote.spread + (offset * 2),
            ))

        return quotes
