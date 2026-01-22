"""
Tests for risk manager.
"""

import pytest
from src.risk.risk_manager import RiskManager, RiskLimits
from src.risk.position_tracker import PositionTracker
from src.risk.pnl import PnLTracker


class TestRiskManager:
    """Tests for RiskManager."""

    def setup_method(self):
        """Setup test fixtures."""
        self.limits = RiskLimits(
            max_position_size=500.0,
            max_total_exposure=5000.0,
            max_drawdown_pct=10.0,
            max_inventory_skew=0.6,
            min_time_remaining=300,
            max_trades_per_minute=30,
        )
        self.position_tracker = PositionTracker()
        self.pnl_tracker = PnLTracker()
        self.risk_manager = RiskManager(
            limits=self.limits,
            position_tracker=self.position_tracker,
            pnl_tracker=self.pnl_tracker,
            starting_capital=10000.0,
        )

    def test_can_trade_default(self):
        """Should be able to trade by default."""
        can, reason = self.risk_manager.can_trade()
        assert can
        assert reason == "OK"

    def test_can_trade_paused(self):
        """Should not trade when paused."""
        self.risk_manager.pause_trading("Test pause")
        can, reason = self.risk_manager.can_trade()
        assert not can
        assert "paused" in reason.lower()

    def test_can_trade_resumed(self):
        """Should trade after resuming."""
        self.risk_manager.pause_trading("Test pause")
        self.risk_manager.resume_trading()
        can, reason = self.risk_manager.can_trade()
        assert can

    def test_check_market_time_sufficient(self):
        """Should allow trading with sufficient time."""
        can, _ = self.risk_manager.check_market_time(600)  # 10 min
        assert can

    def test_check_market_time_insufficient(self):
        """Should not allow trading with insufficient time."""
        can, reason = self.risk_manager.check_market_time(100)  # < 5 min
        assert not can
        assert "time" in reason.lower()

    def test_get_allowed_size(self):
        """Test allowed size calculation."""
        size = self.risk_manager.get_allowed_size(price=0.50)
        # max_position_size / price = 500 / 0.50 = 1000
        assert size == 1000.0

    def test_get_allowed_size_with_exposure(self):
        """Test allowed size with existing exposure."""
        # Add some exposure
        self.position_tracker.add_fill(
            market_id="test",
            token_type="YES",
            shares=2000,
            price=0.50,  # $1000 exposure
        )

        size = self.risk_manager.get_allowed_size(price=0.50)
        # Remaining exposure: 5000 - 1000 = 4000
        # Size: 4000 / 0.50 = 8000
        # But also capped by position size: 500 / 0.50 = 1000
        assert size == 1000.0

    def test_can_open_position_size_limit(self):
        """Should reject positions over size limit."""
        can, reason = self.risk_manager.can_open_position(
            token_type="YES",
            size=2000,
            price=0.50,  # $1000 > $500 limit
        )
        assert not can
        assert "size" in reason.lower()

    def test_should_hedge_aggressively(self):
        """Should flag aggressive hedging at high skew."""
        # Add skewed position
        self.position_tracker.add_fill(
            market_id="test",
            token_type="YES",
            shares=1000,
            price=0.50,
        )

        assert self.risk_manager.should_hedge_aggressively()

    def test_rate_limiting(self):
        """Test rate limiting."""
        # Record many trades
        for _ in range(35):
            self.risk_manager.on_trade()

        can, reason = self.risk_manager.can_trade()
        assert not can
        assert "rate" in reason.lower()

    def test_get_state(self):
        """Test getting risk state."""
        state = self.risk_manager.get_state()
        assert state.can_trade
        assert state.current_exposure == 0
        assert state.current_skew == 0
        assert state.current_drawdown_pct == 0


class TestPositionTracker:
    """Tests for PositionTracker."""

    def setup_method(self):
        """Setup test fixtures."""
        self.tracker = PositionTracker()

    def test_add_fill(self):
        """Test adding fills."""
        self.tracker.add_fill(
            market_id="test",
            token_type="YES",
            shares=100,
            price=0.50,
        )

        position = self.tracker.get_position("test")
        assert position is not None
        assert position.yes_shares == 100

    def test_inventory_skew(self):
        """Test inventory skew calculation."""
        # Balanced
        self.tracker.add_fill("test", "YES", 100, 0.50)
        self.tracker.add_fill("test", "NO", 100, 0.50)
        assert self.tracker.inventory_skew == 0.0

        # Skewed to YES
        self.tracker.add_fill("test", "YES", 100, 0.50)
        assert self.tracker.inventory_skew > 0

    def test_total_exposure(self):
        """Test total exposure calculation."""
        self.tracker.add_fill("test", "YES", 100, 0.50)  # $50
        self.tracker.add_fill("test", "NO", 100, 0.40)  # $40

        assert self.tracker.total_exposure == 90.0

    def test_total_hedged_profit(self):
        """Test hedged profit calculation."""
        # Buy both sides for less than $1
        self.tracker.add_fill("test", "YES", 100, 0.48)  # $48
        self.tracker.add_fill("test", "NO", 100, 0.47)  # $47
        # Total cost: $95, payout: $100, profit: $5

        assert self.tracker.total_hedged_profit == pytest.approx(5.0, rel=0.01)


class TestPnLTracker:
    """Tests for PnLTracker."""

    def setup_method(self):
        """Setup test fixtures."""
        self.tracker = PnLTracker()

    def test_record_trade(self):
        """Test recording trades."""
        self.tracker.record_trade(
            token_type="YES",
            side="BUY",
            price=0.50,
            size=100,
        )

        assert self.tracker.trade_count == 1

    def test_hedged_pairs(self):
        """Test hedged pairs calculation."""
        self.tracker.record_trade("YES", "BUY", 0.50, 100)
        self.tracker.record_trade("NO", "BUY", 0.45, 80)

        assert self.tracker.hedged_pairs == 80

    def test_guaranteed_profit(self):
        """Test guaranteed profit calculation."""
        # Buy both for $0.95 total
        self.tracker.record_trade("YES", "BUY", 0.48, 100)
        self.tracker.record_trade("NO", "BUY", 0.47, 100)

        # Guaranteed profit = $100 - $95 = $5
        assert self.tracker.guaranteed_profit == pytest.approx(5.0, rel=0.01)

    def test_settle_market_yes_wins(self):
        """Test market settlement when YES wins."""
        self.tracker.record_trade("YES", "BUY", 0.48, 100)
        self.tracker.record_trade("NO", "BUY", 0.47, 100)

        pnl = self.tracker.settle_market(yes_won=True)
        # YES pays out $100, cost was $95
        assert pnl == pytest.approx(5.0, rel=0.01)

    def test_settle_market_no_wins(self):
        """Test market settlement when NO wins."""
        self.tracker.record_trade("YES", "BUY", 0.48, 100)
        self.tracker.record_trade("NO", "BUY", 0.47, 100)

        pnl = self.tracker.settle_market(yes_won=False)
        # NO pays out $100, cost was $95
        assert pnl == pytest.approx(5.0, rel=0.01)
