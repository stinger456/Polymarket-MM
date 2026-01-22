"""
Tests for fair value model.
"""

import pytest
from src.pricing.fair_value import FairValueModel, PricingEngine


class TestFairValueModel:
    """Tests for FairValueModel."""

    def setup_method(self):
        """Setup test fixtures."""
        self.model = FairValueModel()

    def test_fair_value_at_strike(self):
        """Price at strike should give ~50% probability."""
        yes, no = self.model.calculate_fair_value(
            current_price=97500,
            strike_price=97500,
            time_remaining_seconds=1800,  # 30 min
            volatility=100,
        )
        assert 0.45 < yes < 0.55
        assert 0.45 < no < 0.55
        assert abs(yes + no - 1.0) < 0.01

    def test_fair_value_deep_itm(self):
        """Price well above strike should give high YES prob."""
        yes, no = self.model.calculate_fair_value(
            current_price=98000,
            strike_price=97500,
            time_remaining_seconds=300,  # 5 min left
            volatility=50,
        )
        assert yes > 0.80
        assert no < 0.20

    def test_fair_value_deep_otm(self):
        """Price well below strike should give low YES prob."""
        yes, no = self.model.calculate_fair_value(
            current_price=97000,
            strike_price=97500,
            time_remaining_seconds=300,
            volatility=50,
        )
        assert yes < 0.20
        assert no > 0.80

    def test_fair_value_expired_above(self):
        """Expired market with price above strike."""
        yes, no = self.model.calculate_fair_value(
            current_price=98000,
            strike_price=97500,
            time_remaining_seconds=0,
            volatility=100,
        )
        assert yes == 1.0
        assert no == 0.0

    def test_fair_value_expired_below(self):
        """Expired market with price below strike."""
        yes, no = self.model.calculate_fair_value(
            current_price=97000,
            strike_price=97500,
            time_remaining_seconds=0,
            volatility=100,
        )
        assert yes == 0.0
        assert no == 1.0

    def test_fair_value_sum_to_one(self):
        """YES and NO should always sum to 1."""
        test_cases = [
            (97500, 97500, 1800, 100),
            (98000, 97500, 300, 50),
            (97000, 97500, 600, 200),
            (100000, 97500, 3600, 150),
        ]

        for current, strike, time, vol in test_cases:
            yes, no = self.model.calculate_fair_value(
                current_price=current,
                strike_price=strike,
                time_remaining_seconds=time,
                volatility=vol,
            )
            assert abs(yes + no - 1.0) < 0.01

    def test_probability_bounds(self):
        """Probabilities should be clamped to reasonable range."""
        # Extreme case - very high probability
        yes, _ = self.model.calculate_fair_value(
            current_price=150000,
            strike_price=97500,
            time_remaining_seconds=60,
            volatility=10,
        )
        assert yes <= 0.98  # Should be clamped

        # Extreme case - very low probability
        yes, _ = self.model.calculate_fair_value(
            current_price=50000,
            strike_price=97500,
            time_remaining_seconds=60,
            volatility=10,
        )
        assert yes >= 0.02  # Should be clamped

    def test_higher_volatility_wider_distribution(self):
        """Higher volatility should move probability toward 50%."""
        # Low vol - more certain
        yes_low, _ = self.model.calculate_fair_value(
            current_price=98000,
            strike_price=97500,
            time_remaining_seconds=1800,
            volatility=20,
        )

        # High vol - less certain
        yes_high, _ = self.model.calculate_fair_value(
            current_price=98000,
            strike_price=97500,
            time_remaining_seconds=1800,
            volatility=200,
        )

        # High vol should be closer to 50%
        assert abs(yes_high - 0.5) < abs(yes_low - 0.5)

    def test_time_decay(self):
        """Less time remaining should increase certainty."""
        # More time - more uncertainty
        yes_long, _ = self.model.calculate_fair_value(
            current_price=97600,
            strike_price=97500,
            time_remaining_seconds=3600,  # 1 hour
            volatility=100,
        )

        # Less time - more certainty
        yes_short, _ = self.model.calculate_fair_value(
            current_price=97600,
            strike_price=97500,
            time_remaining_seconds=60,  # 1 minute
            volatility=100,
        )

        # Short time should be further from 50%
        assert abs(yes_short - 0.5) > abs(yes_long - 0.5)


class TestPricingEngine:
    """Tests for PricingEngine."""

    def setup_method(self):
        """Setup test fixtures."""
        self.engine = PricingEngine()

    def test_calculate_fair_value(self):
        """Test fair value calculation through engine."""
        yes, no = self.engine.calculate_fair_value(
            current_price=97500,
            strike_price=97500,
            time_remaining_seconds=1800,
            volatility=100,
        )
        assert 0 < yes < 1
        assert 0 < no < 1

    def test_calculate_fair_value_detailed(self):
        """Test detailed fair value with greeks."""
        result = self.engine.calculate_fair_value_detailed(
            current_price=97500,
            strike_price=97500,
            time_remaining_seconds=1800,
            volatility=100,
        )

        assert result.yes_price > 0
        assert result.no_price > 0
        # Delta should be negative (price up = YES up)
        assert result.delta != 0

    def test_has_edge(self):
        """Test edge detection."""
        # Good edge - wide spreads
        has_edge = self.engine.has_edge(
            fair_yes=0.50,
            fair_no=0.50,
            market_yes_ask=0.55,
            market_no_ask=0.55,
            min_edge=0.02,
        )
        assert has_edge

        # No edge - tight spreads
        has_edge = self.engine.has_edge(
            fair_yes=0.50,
            fair_no=0.50,
            market_yes_ask=0.51,
            market_no_ask=0.51,
            min_edge=0.02,
        )
        assert not has_edge
