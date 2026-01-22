"""
Risk management for market making.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple, Optional
from collections import deque
import threading
import structlog

from .position_tracker import PositionTracker
from .pnl import PnLTracker


logger = structlog.get_logger(__name__)


@dataclass
class RiskLimits:
    """Risk limit configuration."""
    max_position_size: float = 500.0
    max_total_exposure: float = 5000.0
    max_drawdown_pct: float = 10.0
    max_inventory_skew: float = 0.6
    min_time_remaining: int = 300
    max_trades_per_minute: int = 30


@dataclass
class RiskState:
    """Current risk state."""
    can_trade: bool
    reason: str
    current_exposure: float
    current_skew: float
    current_drawdown_pct: float
    trades_last_minute: int


class RiskManager:
    """
    Enforces risk limits and monitors bot health.

    Tracks:
    - Position size limits
    - Total exposure limits
    - Drawdown limits
    - Rate limiting
    - Inventory skew
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        position_tracker: Optional[PositionTracker] = None,
        pnl_tracker: Optional[PnLTracker] = None,
        starting_capital: float = 10000.0,
    ):
        """
        Initialize risk manager.

        Args:
            limits: Risk limit configuration
            position_tracker: Position tracker instance
            pnl_tracker: P&L tracker instance
            starting_capital: Initial capital for drawdown calculation
        """
        self.limits = limits or RiskLimits()
        self.position_tracker = position_tracker or PositionTracker()
        self.pnl_tracker = pnl_tracker or PnLTracker()
        self.starting_capital = starting_capital

        self._trade_timestamps: deque = deque(maxlen=100)
        self._peak_capital = starting_capital
        self._lock = threading.Lock()
        self._is_paused = False
        self._pause_reason = ""

    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed.

        Returns:
            Tuple of (allowed, reason)
        """
        with self._lock:
            # Check if manually paused
            if self._is_paused:
                return False, f"Trading paused: {self._pause_reason}"

            # Check drawdown
            drawdown = self._calculate_drawdown()
            if drawdown > self.limits.max_drawdown_pct:
                return False, f"Drawdown exceeded: {drawdown:.1f}%"

            # Check total exposure
            exposure = self.position_tracker.total_exposure
            if exposure > self.limits.max_total_exposure:
                return False, f"Exposure limit: ${exposure:.0f}"

            # Check rate limit
            trades = self._count_recent_trades()
            if trades > self.limits.max_trades_per_minute:
                return False, f"Rate limit: {trades} trades/min"

            return True, "OK"

    def can_open_position(
        self,
        token_type: str,
        size: float,
        price: float,
    ) -> Tuple[bool, str]:
        """
        Check if we can open a new position.

        Args:
            token_type: "YES" or "NO"
            size: Position size
            price: Entry price

        Returns:
            Tuple of (allowed, reason)
        """
        can, reason = self.can_trade()
        if not can:
            return False, reason

        # Check position size
        notional = size * price
        if notional > self.limits.max_position_size:
            return False, f"Position size limit: ${notional:.0f}"

        # Check if this would exceed exposure limit
        new_exposure = self.position_tracker.total_exposure + notional
        if new_exposure > self.limits.max_total_exposure:
            return False, f"Would exceed exposure limit: ${new_exposure:.0f}"

        # Check inventory skew
        skew = self.position_tracker.inventory_skew
        if token_type.upper() == "YES" and skew > self.limits.max_inventory_skew:
            return False, f"Skew limit (long YES): {skew:.2f}"
        if token_type.upper() == "NO" and skew < -self.limits.max_inventory_skew:
            return False, f"Skew limit (long NO): {skew:.2f}"

        return True, "OK"

    def check_market_time(self, time_remaining_seconds: int) -> Tuple[bool, str]:
        """
        Check if there's enough time to trade.

        Args:
            time_remaining_seconds: Seconds until market expires

        Returns:
            Tuple of (allowed, reason)
        """
        if time_remaining_seconds < self.limits.min_time_remaining:
            return False, f"Insufficient time: {time_remaining_seconds}s"
        return True, "OK"

    def get_allowed_size(self, price: float) -> float:
        """
        Calculate maximum allowed position size.

        Args:
            price: Entry price

        Returns:
            Maximum size in shares
        """
        if price <= 0:
            return 0.0

        # Based on position size limit
        max_by_position = self.limits.max_position_size / price

        # Based on remaining exposure
        remaining_exposure = max(
            0,
            self.limits.max_total_exposure - self.position_tracker.total_exposure
        )
        max_by_exposure = remaining_exposure / price

        return min(max_by_position, max_by_exposure)

    def on_trade(self):
        """Record a trade for rate limiting."""
        with self._lock:
            self._trade_timestamps.append(datetime.now(timezone.utc))

    def on_pnl_update(self, current_capital: float):
        """
        Update peak capital for drawdown calculation.

        Args:
            current_capital: Current portfolio value
        """
        with self._lock:
            if current_capital > self._peak_capital:
                self._peak_capital = current_capital

    def _calculate_drawdown(self) -> float:
        """Calculate current drawdown percentage."""
        # Get current P&L
        snapshot = self.pnl_tracker.get_snapshot()
        current_capital = self.starting_capital + snapshot.total_pnl

        if self._peak_capital <= 0:
            return 0.0

        drawdown = (self._peak_capital - current_capital) / self._peak_capital * 100
        return max(0, drawdown)

    def _count_recent_trades(self) -> int:
        """Count trades in the last minute."""
        now = datetime.now(timezone.utc)
        cutoff = 60  # seconds

        count = sum(
            1 for ts in self._trade_timestamps
            if (now - ts).total_seconds() < cutoff
        )
        return count

    def pause_trading(self, reason: str):
        """Pause trading."""
        with self._lock:
            self._is_paused = True
            self._pause_reason = reason
        logger.warning("Trading paused", reason=reason)

    def resume_trading(self):
        """Resume trading."""
        with self._lock:
            self._is_paused = False
            self._pause_reason = ""
        logger.info("Trading resumed")

    def get_state(self) -> RiskState:
        """Get current risk state."""
        can, reason = self.can_trade()
        return RiskState(
            can_trade=can,
            reason=reason,
            current_exposure=self.position_tracker.total_exposure,
            current_skew=self.position_tracker.inventory_skew,
            current_drawdown_pct=self._calculate_drawdown(),
            trades_last_minute=self._count_recent_trades(),
        )

    def should_hedge_aggressively(self) -> bool:
        """Check if we should hedge aggressively."""
        skew = abs(self.position_tracker.inventory_skew)
        return skew > self.limits.max_inventory_skew * 0.8

    @classmethod
    def from_config(cls, config) -> "RiskManager":
        """
        Create RiskManager from config.

        Args:
            config: Config with risk parameters

        Returns:
            Configured RiskManager
        """
        risk_config = config.get_risk_config()
        trading_config = config.get_trading_config()

        limits = RiskLimits(
            max_position_size=trading_config.max_position_size,
            max_total_exposure=trading_config.max_total_exposure,
            max_drawdown_pct=risk_config.max_drawdown_pct,
            max_inventory_skew=risk_config.max_inventory_skew,
            min_time_remaining=risk_config.min_time_remaining,
            max_trades_per_minute=risk_config.max_trades_per_minute,
        )

        return cls(limits=limits)
