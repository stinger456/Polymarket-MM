"""
Inventory management for market making.
"""

from dataclasses import dataclass
from typing import Optional
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class InventoryState:
    """Current inventory state."""
    yes_shares: float
    no_shares: float
    yes_avg_price: float
    no_avg_price: float

    @property
    def net_exposure(self) -> float:
        """Net exposure: positive = long YES, negative = long NO."""
        return self.yes_shares - self.no_shares

    @property
    def hedged_shares(self) -> float:
        """Number of fully hedged share pairs."""
        return min(self.yes_shares, self.no_shares)

    @property
    def unhedged_shares(self) -> float:
        """Number of unhedged shares."""
        return abs(self.yes_shares - self.no_shares)

    @property
    def is_hedged(self) -> bool:
        """Check if fully hedged."""
        return self.yes_shares > 0 and self.no_shares > 0

    @property
    def skew(self) -> float:
        """
        Inventory skew normalized to [-1, 1].

        +1 = 100% YES, -1 = 100% NO, 0 = balanced
        """
        total = self.yes_shares + self.no_shares
        if total == 0:
            return 0.0
        return (self.yes_shares - self.no_shares) / total


class InventoryManager:
    """
    Manages inventory for a single market.

    Tracks YES and NO positions and calculates adjustments.
    """

    def __init__(
        self,
        max_inventory_skew: float = 0.6,
        max_position_size: float = 500.0,
    ):
        """
        Initialize inventory manager.

        Args:
            max_inventory_skew: Maximum allowed skew before aggressive rebalancing
            max_position_size: Maximum USD in any position
        """
        self.max_inventory_skew = max_inventory_skew
        self.max_position_size = max_position_size

        self._yes_shares = 0.0
        self._no_shares = 0.0
        self._yes_avg_price = 0.0
        self._no_avg_price = 0.0
        self._total_cost = 0.0

    @property
    def state(self) -> InventoryState:
        """Get current inventory state."""
        return InventoryState(
            yes_shares=self._yes_shares,
            no_shares=self._no_shares,
            yes_avg_price=self._yes_avg_price,
            no_avg_price=self._no_avg_price,
        )

    @property
    def skew(self) -> float:
        """Get current inventory skew."""
        return self.state.skew

    @property
    def is_over_limit(self) -> bool:
        """Check if inventory is over skew limit."""
        return abs(self.skew) > self.max_inventory_skew

    @property
    def hedged_shares(self) -> float:
        """Number of hedged pairs."""
        return min(self._yes_shares, self._no_shares)

    @property
    def guaranteed_payout(self) -> float:
        """Minimum guaranteed payout at settlement."""
        return self.hedged_shares

    @property
    def guaranteed_profit(self) -> float:
        """Guaranteed profit from hedged positions."""
        return self.guaranteed_payout - self._total_cost

    def add_yes_fill(self, shares: float, price: float):
        """
        Record a YES fill (bought YES shares).

        Args:
            shares: Number of shares filled
            price: Fill price
        """
        if shares <= 0:
            return

        # Update average price
        total_yes = self._yes_shares + shares
        if total_yes > 0:
            self._yes_avg_price = (
                self._yes_avg_price * self._yes_shares + price * shares
            ) / total_yes

        self._yes_shares = total_yes
        self._total_cost += shares * price

        logger.info(
            "YES fill recorded",
            shares=shares,
            price=price,
            total_yes=self._yes_shares,
            skew=self.skew,
        )

    def add_no_fill(self, shares: float, price: float):
        """
        Record a NO fill (bought NO shares).

        Args:
            shares: Number of shares filled
            price: Fill price
        """
        if shares <= 0:
            return

        # Update average price
        total_no = self._no_shares + shares
        if total_no > 0:
            self._no_avg_price = (
                self._no_avg_price * self._no_shares + price * shares
            ) / total_no

        self._no_shares = total_no
        self._total_cost += shares * price

        logger.info(
            "NO fill recorded",
            shares=shares,
            price=price,
            total_no=self._no_shares,
            skew=self.skew,
        )

    def get_quote_adjustment(self) -> tuple:
        """
        Get quote adjustments based on inventory.

        Returns:
            Tuple of (yes_adjustment, no_adjustment)
            Positive adjustment = raise bid/lower ask
            Negative adjustment = lower bid/raise ask
        """
        skew = self.skew

        # If long YES (positive skew), want to:
        # - Lower YES bids (less eager to buy more)
        # - Raise NO bids (more eager to hedge)
        # This is represented as negative yes_adj, positive no_adj

        # Scale: at max skew, adjustment is 2 cents
        scale = 0.02

        yes_adjustment = -skew * scale
        no_adjustment = skew * scale

        return (yes_adjustment, no_adjustment)

    def get_size_adjustment(self) -> tuple:
        """
        Get size adjustments based on inventory.

        When skewed, we want smaller sizes on the overweight side.

        Returns:
            Tuple of (yes_size_mult, no_size_mult) in range [0.5, 1.0]
        """
        skew = self.skew

        # At max skew, reduce size by 50%
        min_mult = 0.5

        if skew > 0:
            # Long YES - reduce YES size
            yes_mult = 1.0 - (skew * (1.0 - min_mult))
            no_mult = 1.0
        elif skew < 0:
            # Long NO - reduce NO size
            yes_mult = 1.0
            no_mult = 1.0 - (abs(skew) * (1.0 - min_mult))
        else:
            yes_mult = 1.0
            no_mult = 1.0

        return (yes_mult, no_mult)

    def can_add_yes(self, shares: float, price: float) -> bool:
        """Check if we can add more YES exposure."""
        # Check max position size
        new_cost = self._yes_shares * self._yes_avg_price + shares * price
        if new_cost > self.max_position_size:
            return False

        # Check if this would exceed skew limit
        new_yes = self._yes_shares + shares
        new_skew = (new_yes - self._no_shares) / (new_yes + self._no_shares)
        if abs(new_skew) > self.max_inventory_skew:
            return False

        return True

    def can_add_no(self, shares: float, price: float) -> bool:
        """Check if we can add more NO exposure."""
        new_cost = self._no_shares * self._no_avg_price + shares * price
        if new_cost > self.max_position_size:
            return False

        new_no = self._no_shares + shares
        new_skew = (self._yes_shares - new_no) / (self._yes_shares + new_no)
        if abs(new_skew) > self.max_inventory_skew:
            return False

        return True

    def reset(self):
        """Reset inventory (e.g., after market settlement)."""
        self._yes_shares = 0.0
        self._no_shares = 0.0
        self._yes_avg_price = 0.0
        self._no_avg_price = 0.0
        self._total_cost = 0.0
