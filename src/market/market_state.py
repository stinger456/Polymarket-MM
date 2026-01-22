"""
Market state tracking and data models.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List
from enum import Enum


class MarketStatus(Enum):
    """Market lifecycle status."""
    UNKNOWN = "unknown"
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"


@dataclass
class Token:
    """Represents a market outcome token (YES or NO)."""
    token_id: str
    outcome: str  # "Yes" or "No"
    price: float = 0.0
    winner: Optional[bool] = None


@dataclass
class Market:
    """
    Represents a Polymarket hourly BTC market.

    Contains all the information needed to trade this market.
    """
    condition_id: str
    question: str
    description: str
    yes_token_id: str
    no_token_id: str
    end_time: datetime
    strike_price: float
    minimum_order_size: float = 1.0
    minimum_tick_size: float = 0.01
    status: MarketStatus = MarketStatus.ACTIVE

    # Current prices (updated from orderbook)
    yes_price: float = 0.5
    no_price: float = 0.5

    # Market metadata
    volume: float = 0.0
    liquidity: float = 0.0

    @property
    def time_remaining_seconds(self) -> int:
        """Seconds until market resolves."""
        now = datetime.now(timezone.utc)
        delta = self.end_time - now
        return max(0, int(delta.total_seconds()))

    @property
    def time_remaining_minutes(self) -> float:
        """Minutes until market resolves."""
        return self.time_remaining_seconds / 60

    @property
    def is_active(self) -> bool:
        """Check if market is still active for trading."""
        return self.status == MarketStatus.ACTIVE and self.time_remaining_seconds > 0

    @property
    def implied_probability(self) -> float:
        """Implied probability from YES price."""
        return self.yes_price

    def update_prices(self, yes_price: float, no_price: float):
        """Update market prices."""
        self.yes_price = yes_price
        self.no_price = no_price

    def __str__(self) -> str:
        return (
            f"Market({self.question[:50]}... | "
            f"Strike: ${self.strike_price:,.0f} | "
            f"Time: {self.time_remaining_minutes:.1f}min | "
            f"YES: {self.yes_price:.2f}, NO: {self.no_price:.2f})"
        )


@dataclass
class MarketState:
    """
    Tracks the current state of a market during trading.

    Includes orderbook state, recent trades, and trading metrics.
    """
    market: Market

    # Orderbook state
    best_bid_yes: float = 0.0
    best_ask_yes: float = 1.0
    best_bid_no: float = 0.0
    best_ask_no: float = 1.0

    bid_depth_yes: float = 0.0
    ask_depth_yes: float = 0.0
    bid_depth_no: float = 0.0
    ask_depth_no: float = 0.0

    # Trade metrics
    our_bid_yes: Optional[float] = None
    our_ask_yes: Optional[float] = None
    our_bid_no: Optional[float] = None
    our_ask_no: Optional[float] = None

    # Timing
    last_update: Optional[datetime] = None

    @property
    def spread_yes(self) -> float:
        """Spread on YES token."""
        return self.best_ask_yes - self.best_bid_yes

    @property
    def spread_no(self) -> float:
        """Spread on NO token."""
        return self.best_ask_no - self.best_bid_no

    @property
    def mid_yes(self) -> float:
        """Midpoint price for YES."""
        return (self.best_bid_yes + self.best_ask_yes) / 2

    @property
    def mid_no(self) -> float:
        """Midpoint price for NO."""
        return (self.best_bid_no + self.best_ask_no) / 2

    @property
    def combined_spread(self) -> float:
        """
        Combined spread across both tokens.

        If YES bid + NO bid < 1.0, there's an arbitrage opportunity.
        """
        return 1.0 - (self.best_bid_yes + self.best_bid_no)

    @property
    def has_arbitrage(self) -> bool:
        """Check if there's a risk-free arbitrage opportunity."""
        # If we can buy YES and NO for less than $1, guaranteed profit
        return self.best_ask_yes + self.best_ask_no < 1.0

    def update_orderbook(
        self,
        yes_bids: List[tuple],
        yes_asks: List[tuple],
        no_bids: List[tuple],
        no_asks: List[tuple],
    ):
        """
        Update orderbook state from market data.

        Each list is [(price, size), ...]
        """
        # YES token
        if yes_bids:
            self.best_bid_yes = yes_bids[0][0]
            self.bid_depth_yes = sum(size for _, size in yes_bids)
        if yes_asks:
            self.best_ask_yes = yes_asks[0][0]
            self.ask_depth_yes = sum(size for _, size in yes_asks)

        # NO token
        if no_bids:
            self.best_bid_no = no_bids[0][0]
            self.bid_depth_no = sum(size for _, size in no_bids)
        if no_asks:
            self.best_ask_no = no_asks[0][0]
            self.ask_depth_no = sum(size for _, size in no_asks)

        # Update market prices
        self.market.update_prices(self.mid_yes, self.mid_no)
        self.last_update = datetime.now(timezone.utc)

    def is_stale(self, max_age_seconds: float = 10.0) -> bool:
        """Check if market state is stale."""
        if self.last_update is None:
            return True
        age = (datetime.now(timezone.utc) - self.last_update).total_seconds()
        return age > max_age_seconds
