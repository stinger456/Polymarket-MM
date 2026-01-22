"""
Position tracking for sports markets.

Tracks positions, P&L, and risk metrics per market.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import structlog

from src.sports.sports_quote_engine import Position
from src.sports.sports_order_manager import Fill

logger = structlog.get_logger()


@dataclass
class MarketPosition:
    """Position in a single market."""
    market_id: str
    home_token_id: str
    away_token_id: str

    # Shares held
    home_shares: float = 0.0  # YES token
    away_shares: float = 0.0  # NO token

    # Cost basis
    home_cost: float = 0.0  # Total USD spent on home
    away_cost: float = 0.0  # Total USD spent on away

    # Realized P&L
    realized_pnl: float = 0.0

    # Tracking
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def total_shares(self) -> float:
        """Total shares held."""
        return self.home_shares + self.away_shares

    @property
    def hedged_shares(self) -> float:
        """Number of fully hedged shares."""
        return min(self.home_shares, self.away_shares)

    @property
    def unhedged_shares(self) -> float:
        """Number of unhedged shares."""
        return abs(self.home_shares - self.away_shares)

    @property
    def net_position(self) -> float:
        """Net position (positive = long home, negative = long away)."""
        return self.home_shares - self.away_shares

    @property
    def total_cost(self) -> float:
        """Total USD invested."""
        return self.home_cost + self.away_cost

    @property
    def hedged_cost(self) -> float:
        """Cost of hedged portion."""
        if self.total_shares == 0:
            return 0.0
        return self.total_cost * (self.hedged_shares / self.total_shares)

    @property
    def hedged_pnl(self) -> float:
        """
        P&L from fully hedged shares.

        If we hold N shares of both home and away, we get $N at settlement.
        P&L = $N - cost of those shares
        """
        return self.hedged_shares - self.hedged_cost

    @property
    def inventory_skew(self) -> float:
        """Inventory skew (-1 to 1)."""
        if self.total_shares == 0:
            return 0.0
        return self.net_position / self.total_shares

    def to_position(self) -> Position:
        """Convert to Position object for quote engine."""
        return Position(
            home_shares=self.home_shares,
            away_shares=self.away_shares,
        )

    def add_fill(self, fill: Fill):
        """Update position with a fill."""
        if fill.token_id == self.home_token_id:
            if fill.side == "BUY":
                self.home_shares += fill.size
                self.home_cost += fill.size * fill.price
            else:  # SELL
                # Calculate realized P&L
                if self.home_shares > 0:
                    avg_cost = self.home_cost / self.home_shares
                    pnl = (fill.price - avg_cost) * fill.size
                    self.realized_pnl += pnl

                    # Reduce position
                    self.home_shares -= fill.size
                    self.home_cost -= fill.size * avg_cost

        elif fill.token_id == self.away_token_id:
            if fill.side == "BUY":
                self.away_shares += fill.size
                self.away_cost += fill.size * fill.price
            else:  # SELL
                if self.away_shares > 0:
                    avg_cost = self.away_cost / self.away_shares
                    pnl = (fill.price - avg_cost) * fill.size
                    self.realized_pnl += pnl

                    self.away_shares -= fill.size
                    self.away_cost -= fill.size * avg_cost

        self.updated_at = datetime.now()


@dataclass
class PortfolioMetrics:
    """Aggregate portfolio metrics."""
    total_exposure: float
    total_hedged: float
    total_unhedged: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    num_positions: int
    max_position_size: float
    avg_inventory_skew: float


class PositionTracker:
    """
    Tracks positions across all markets.

    Provides:
    - Per-market position tracking
    - Aggregate portfolio metrics
    - P&L calculation
    - Risk metrics
    """

    def __init__(self, max_position: float = 500.0, max_total: float = 5000.0):
        """
        Initialize position tracker.

        Args:
            max_position: Maximum position per market
            max_total: Maximum total exposure
        """
        self.max_position = max_position
        self.max_total = max_total
        self.positions: Dict[str, MarketPosition] = {}
        self.total_realized_pnl: float = 0.0

    def get_position(self, market_id: str) -> Optional[MarketPosition]:
        """Get position for a market."""
        return self.positions.get(market_id)

    def get_or_create_position(
        self,
        market_id: str,
        home_token_id: str,
        away_token_id: str,
    ) -> MarketPosition:
        """Get or create a position for a market."""
        if market_id not in self.positions:
            self.positions[market_id] = MarketPosition(
                market_id=market_id,
                home_token_id=home_token_id,
                away_token_id=away_token_id,
            )
        return self.positions[market_id]

    def record_fill(
        self,
        fill: Fill,
        home_token_id: str,
        away_token_id: str,
    ):
        """
        Record a fill and update position.

        Args:
            fill: Fill to record
            home_token_id: Home (YES) token ID
            away_token_id: Away (NO) token ID
        """
        position = self.get_or_create_position(
            fill.market_id,
            home_token_id,
            away_token_id,
        )

        old_realized = position.realized_pnl
        position.add_fill(fill)
        pnl_change = position.realized_pnl - old_realized

        if pnl_change != 0:
            self.total_realized_pnl += pnl_change
            logger.info(
                "realized_pnl",
                market_id=fill.market_id,
                pnl_change=f"${pnl_change:.2f}",
                total_realized=f"${self.total_realized_pnl:.2f}",
            )

    def get_portfolio_metrics(
        self,
        current_prices: Optional[Dict[str, float]] = None,
    ) -> PortfolioMetrics:
        """
        Calculate aggregate portfolio metrics.

        Args:
            current_prices: Dict of token_id -> current price for unrealized P&L

        Returns:
            PortfolioMetrics with aggregate stats
        """
        total_exposure = 0.0
        total_hedged = 0.0
        total_unhedged = 0.0
        total_unrealized_pnl = 0.0
        max_position_size = 0.0
        skews = []

        for position in self.positions.values():
            if position.total_shares == 0:
                continue

            total_exposure += position.total_cost
            total_hedged += position.hedged_shares
            total_unhedged += position.unhedged_shares
            max_position_size = max(max_position_size, position.total_cost)
            skews.append(abs(position.inventory_skew))

            # Calculate unrealized P&L if prices provided
            if current_prices:
                home_price = current_prices.get(position.home_token_id, 0.5)
                away_price = current_prices.get(position.away_token_id, 0.5)

                home_value = position.home_shares * home_price
                away_value = position.away_shares * away_price
                total_value = home_value + away_value
                total_unrealized_pnl += total_value - position.total_cost

        avg_skew = sum(skews) / len(skews) if skews else 0.0

        return PortfolioMetrics(
            total_exposure=total_exposure,
            total_hedged=total_hedged,
            total_unhedged=total_unhedged,
            total_realized_pnl=self.total_realized_pnl,
            total_unrealized_pnl=total_unrealized_pnl,
            num_positions=len([p for p in self.positions.values() if p.total_shares > 0]),
            max_position_size=max_position_size,
            avg_inventory_skew=avg_skew,
        )

    def can_add_position(
        self,
        market_id: str,
        side: str,
        size: float,
    ) -> tuple[bool, str]:
        """
        Check if we can add to a position.

        Args:
            market_id: Market ID
            side: "home" or "away"
            size: Size to add

        Returns:
            (can_add, reason) tuple
        """
        metrics = self.get_portfolio_metrics()

        # Check total exposure
        new_exposure = metrics.total_exposure + size
        if new_exposure > self.max_total:
            return False, f"Would exceed max total exposure (${self.max_total})"

        # Check position size
        position = self.positions.get(market_id)
        if position:
            current = position.home_shares if side == "home" else position.away_shares
            if current + size > self.max_position:
                return False, f"Would exceed max position size (${self.max_position})"

        return True, ""

    def close_market_position(self, market_id: str):
        """
        Close out a market position (e.g., after settlement).

        Args:
            market_id: Market ID to close
        """
        if market_id in self.positions:
            position = self.positions[market_id]

            # Record hedged P&L as realized
            self.total_realized_pnl += position.hedged_pnl

            logger.info(
                "position_closed",
                market_id=market_id,
                hedged_pnl=f"${position.hedged_pnl:.2f}",
                total_realized=f"${self.total_realized_pnl:.2f}",
            )

            del self.positions[market_id]

    def get_summary(self) -> Dict:
        """Get position summary."""
        metrics = self.get_portfolio_metrics()

        return {
            "num_positions": metrics.num_positions,
            "total_exposure": f"${metrics.total_exposure:.2f}",
            "total_hedged_shares": f"{metrics.total_hedged:.2f}",
            "total_unhedged_shares": f"{metrics.total_unhedged:.2f}",
            "realized_pnl": f"${metrics.total_realized_pnl:.2f}",
            "avg_inventory_skew": f"{metrics.avg_inventory_skew:.2%}",
            "max_position_size": f"${metrics.max_position_size:.2f}",
        }
