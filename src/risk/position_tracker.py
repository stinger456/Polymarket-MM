"""
Position tracking for market making.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional
import threading
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class Position:
    """Position in a single market."""
    market_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    yes_avg_price: float = 0.0
    no_avg_price: float = 0.0
    total_cost: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_hedged(self) -> bool:
        """Do we have shares on both sides?"""
        return self.yes_shares > 0 and self.no_shares > 0

    @property
    def hedged_size(self) -> float:
        """Number of fully hedged share pairs."""
        return min(self.yes_shares, self.no_shares)

    @property
    def guaranteed_payout(self) -> float:
        """Minimum payout at resolution."""
        return self.hedged_size  # Each pair pays $1

    @property
    def guaranteed_profit(self) -> float:
        """Profit from fully hedged positions."""
        if self.hedged_size == 0:
            return 0.0
        return self.guaranteed_payout - self.total_cost

    @property
    def net_exposure(self) -> float:
        """Net exposure: positive = long YES, negative = long NO."""
        return self.yes_shares - self.no_shares

    @property
    def skew(self) -> float:
        """
        Position skew normalized to [-1, 1].

        +1 = 100% YES, -1 = 100% NO, 0 = balanced
        """
        total = self.yes_shares + self.no_shares
        if total == 0:
            return 0.0
        return (self.yes_shares - self.no_shares) / total

    @property
    def total_shares(self) -> float:
        """Total shares held."""
        return self.yes_shares + self.no_shares

    def add_yes(self, shares: float, price: float):
        """Add YES shares to position."""
        if shares <= 0:
            return

        # Update weighted average price
        total = self.yes_shares + shares
        if total > 0:
            self.yes_avg_price = (
                self.yes_avg_price * self.yes_shares + price * shares
            ) / total

        self.yes_shares = total
        self.total_cost += shares * price
        self.updated_at = datetime.now(timezone.utc)

    def add_no(self, shares: float, price: float):
        """Add NO shares to position."""
        if shares <= 0:
            return

        total = self.no_shares + shares
        if total > 0:
            self.no_avg_price = (
                self.no_avg_price * self.no_shares + price * shares
            ) / total

        self.no_shares = total
        self.total_cost += shares * price
        self.updated_at = datetime.now(timezone.utc)

    def calculate_unrealized_pnl(
        self,
        yes_mark: float,
        no_mark: float,
    ) -> float:
        """
        Calculate unrealized P&L at mark prices.

        Args:
            yes_mark: Current YES price
            no_mark: Current NO price

        Returns:
            Unrealized P&L
        """
        mtm = self.yes_shares * yes_mark + self.no_shares * no_mark
        return mtm - self.total_cost


class PositionTracker:
    """
    Tracks positions across multiple markets.

    Thread-safe for use with async callbacks.
    """

    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._lock = threading.Lock()

    def get_position(self, market_id: str) -> Optional[Position]:
        """Get position for a market."""
        with self._lock:
            return self._positions.get(market_id)

    def get_or_create_position(self, market_id: str) -> Position:
        """Get or create position for a market."""
        with self._lock:
            if market_id not in self._positions:
                self._positions[market_id] = Position(market_id=market_id)
            return self._positions[market_id]

    def add_fill(
        self,
        market_id: str,
        token_type: str,
        shares: float,
        price: float,
    ):
        """
        Add a fill to position.

        Args:
            market_id: Market condition ID
            token_type: "YES" or "NO"
            shares: Number of shares
            price: Fill price
        """
        with self._lock:
            position = self.get_or_create_position(market_id)

            if token_type.upper() == "YES":
                position.add_yes(shares, price)
            else:
                position.add_no(shares, price)

        logger.info(
            "Fill added to position",
            market_id=market_id,
            token_type=token_type,
            shares=shares,
            price=price,
            skew=position.skew,
            hedged=position.hedged_size,
        )

    @property
    def inventory_skew(self) -> float:
        """
        Get overall inventory skew across all positions.

        Returns weighted average skew.
        """
        with self._lock:
            total_shares = sum(p.total_shares for p in self._positions.values())
            if total_shares == 0:
                return 0.0

            weighted_skew = sum(
                p.skew * p.total_shares
                for p in self._positions.values()
            )
            return weighted_skew / total_shares

    @property
    def total_exposure(self) -> float:
        """Total USD exposure across all positions."""
        with self._lock:
            return sum(p.total_cost for p in self._positions.values())

    @property
    def total_hedged_profit(self) -> float:
        """Total guaranteed profit from hedged positions."""
        with self._lock:
            return sum(p.guaranteed_profit for p in self._positions.values())

    def get_all_positions(self) -> Dict[str, Position]:
        """Get copy of all positions."""
        with self._lock:
            return dict(self._positions)

    def remove_position(self, market_id: str):
        """Remove position for a settled market."""
        with self._lock:
            self._positions.pop(market_id, None)

    def clear(self):
        """Clear all positions."""
        with self._lock:
            self._positions.clear()
