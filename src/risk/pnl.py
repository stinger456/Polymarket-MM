"""
P&L tracking and calculation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class Trade:
    """A completed trade for P&L tracking."""
    token_type: str  # "YES" or "NO"
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    fee: float
    timestamp: datetime

    @property
    def notional(self) -> float:
        """Dollar value of trade."""
        return self.price * self.size

    @property
    def net_cost(self) -> float:
        """Cost including fee (negative for sells)."""
        if self.side == "BUY":
            return self.notional + self.fee
        else:
            return -self.notional + self.fee


@dataclass
class PnLSnapshot:
    """Snapshot of P&L at a point in time."""
    timestamp: datetime
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    total_fees: float
    trade_count: int
    hedged_pairs: float
    guaranteed_profit: float


class PnLTracker:
    """
    Tracks profit and loss for the trading session.

    Calculates:
    - Realized P&L (from closed positions)
    - Unrealized P&L (from open positions)
    - Guaranteed profit (from hedged pairs)
    """

    def __init__(self, starting_balance: float = 0.0):
        """
        Initialize P&L tracker.

        Args:
            starting_balance: Initial account balance
        """
        self.starting_balance = starting_balance
        self._trades: List[Trade] = []

        # Position tracking
        self._yes_shares = 0.0
        self._no_shares = 0.0
        self._yes_cost_basis = 0.0
        self._no_cost_basis = 0.0
        self._total_fees = 0.0
        self._realized_pnl = 0.0

    def record_trade(
        self,
        token_type: str,
        side: str,
        price: float,
        size: float,
        fee: float = 0.0,
        timestamp: Optional[datetime] = None,
    ):
        """
        Record a trade.

        Args:
            token_type: "YES" or "NO"
            side: "BUY" or "SELL"
            price: Trade price
            size: Trade size
            fee: Fee charged
            timestamp: Trade time
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        trade = Trade(
            token_type=token_type,
            side=side,
            price=price,
            size=size,
            fee=fee,
            timestamp=timestamp,
        )
        self._trades.append(trade)
        self._total_fees += fee

        # Update positions
        if token_type == "YES":
            if side == "BUY":
                self._yes_cost_basis += price * size
                self._yes_shares += size
            else:
                # Calculate realized P&L for sells
                if self._yes_shares > 0:
                    avg_cost = self._yes_cost_basis / self._yes_shares
                    realized = (price - avg_cost) * size
                    self._realized_pnl += realized
                    self._yes_cost_basis -= avg_cost * size
                    self._yes_shares -= size

        else:  # NO
            if side == "BUY":
                self._no_cost_basis += price * size
                self._no_shares += size
            else:
                if self._no_shares > 0:
                    avg_cost = self._no_cost_basis / self._no_shares
                    realized = (price - avg_cost) * size
                    self._realized_pnl += realized
                    self._no_cost_basis -= avg_cost * size
                    self._no_shares -= size

        logger.debug(
            "Trade recorded",
            token_type=token_type,
            side=side,
            price=price,
            size=size,
            realized_pnl=self._realized_pnl,
        )

    @property
    def hedged_pairs(self) -> float:
        """Number of fully hedged YES+NO pairs."""
        return min(self._yes_shares, self._no_shares)

    @property
    def total_cost(self) -> float:
        """Total cost of current positions."""
        return self._yes_cost_basis + self._no_cost_basis

    @property
    def guaranteed_payout(self) -> float:
        """Guaranteed payout from hedged pairs."""
        return self.hedged_pairs  # Each pair pays $1

    @property
    def guaranteed_profit(self) -> float:
        """Guaranteed profit from hedged positions."""
        if self.hedged_pairs == 0:
            return 0.0

        # Cost of hedged portion
        hedged_yes_cost = (
            self._yes_cost_basis * (self.hedged_pairs / self._yes_shares)
            if self._yes_shares > 0 else 0
        )
        hedged_no_cost = (
            self._no_cost_basis * (self.hedged_pairs / self._no_shares)
            if self._no_shares > 0 else 0
        )
        hedged_cost = hedged_yes_cost + hedged_no_cost

        return self.guaranteed_payout - hedged_cost - self._total_fees

    def calculate_unrealized_pnl(
        self,
        yes_mark_price: float,
        no_mark_price: float,
    ) -> float:
        """
        Calculate unrealized P&L at current prices.

        Args:
            yes_mark_price: Current YES price
            no_mark_price: Current NO price

        Returns:
            Unrealized P&L
        """
        # Mark-to-market value
        yes_mtm = self._yes_shares * yes_mark_price
        no_mtm = self._no_shares * no_mark_price

        # Unrealized = MTM - cost basis
        return (yes_mtm + no_mtm) - self.total_cost

    def get_snapshot(
        self,
        yes_mark_price: float = 0.5,
        no_mark_price: float = 0.5,
    ) -> PnLSnapshot:
        """
        Get P&L snapshot.

        Args:
            yes_mark_price: Current YES price for MTM
            no_mark_price: Current NO price for MTM

        Returns:
            PnLSnapshot with current state
        """
        unrealized = self.calculate_unrealized_pnl(yes_mark_price, no_mark_price)

        return PnLSnapshot(
            timestamp=datetime.now(timezone.utc),
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized,
            total_pnl=self._realized_pnl + unrealized,
            total_fees=self._total_fees,
            trade_count=len(self._trades),
            hedged_pairs=self.hedged_pairs,
            guaranteed_profit=self.guaranteed_profit,
        )

    def settle_market(self, yes_won: bool) -> float:
        """
        Settle market and calculate final P&L.

        Args:
            yes_won: True if YES won (BTC above strike)

        Returns:
            Final realized P&L
        """
        # Payout from positions
        if yes_won:
            payout = self._yes_shares  # YES pays $1 each
        else:
            payout = self._no_shares  # NO pays $1 each

        # Final P&L = payout - cost - fees
        final_pnl = payout - self.total_cost - self._total_fees

        logger.info(
            "Market settled",
            yes_won=yes_won,
            payout=payout,
            total_cost=self.total_cost,
            final_pnl=final_pnl,
        )

        # Record as realized
        self._realized_pnl += final_pnl

        # Reset positions
        self._yes_shares = 0.0
        self._no_shares = 0.0
        self._yes_cost_basis = 0.0
        self._no_cost_basis = 0.0

        return final_pnl

    def reset(self):
        """Reset P&L tracker."""
        self._trades = []
        self._yes_shares = 0.0
        self._no_shares = 0.0
        self._yes_cost_basis = 0.0
        self._no_cost_basis = 0.0
        self._total_fees = 0.0
        self._realized_pnl = 0.0

    @property
    def trades(self) -> List[Trade]:
        """Get all trades."""
        return list(self._trades)

    @property
    def trade_count(self) -> int:
        """Number of trades."""
        return len(self._trades)
