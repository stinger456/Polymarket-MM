"""
Tests for quote engine.
"""

import pytest
from src.quoting.quote_engine import QuoteEngine, QuoteSet


class TestQuoteEngine:
    """Tests for QuoteEngine."""

    def setup_method(self):
        """Setup test fixtures."""
        self.engine = QuoteEngine(
            base_half_spread=0.01,
            num_levels=3,
            level_spacing=0.01,
            base_size=50.0,
            min_size=5.0,
            size_decay=0.6,
        )

    def test_generate_quotes_basic(self):
        """Test basic quote generation."""
        quotes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )

        # Should have quotes on both sides
        assert len(quotes.yes_bids) == 3
        assert len(quotes.yes_asks) == 3
        assert len(quotes.no_bids) == 3
        assert len(quotes.no_asks) == 3

    def test_quote_spread(self):
        """Quotes should have expected spread."""
        quotes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )

        # Best bid should be below fair value
        assert quotes.best_yes_bid < 0.50
        assert quotes.best_no_bid < 0.50

        # Spread should be at least base spread
        spread = quotes.yes_asks[0].price - quotes.yes_bids[0].price
        assert spread >= 0.02  # 2 * half_spread

    def test_inventory_adjustment_long_yes(self):
        """Long YES should lower YES bids."""
        neutral = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )
        long_yes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.5,
        )

        # Long YES -> lower YES bids
        assert long_yes.best_yes_bid < neutral.best_yes_bid

        # Long YES -> raise NO bids (want to hedge)
        assert long_yes.best_no_bid > neutral.best_no_bid

    def test_inventory_adjustment_long_no(self):
        """Long NO should lower NO bids."""
        neutral = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )
        long_no = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=-0.5,
        )

        # Long NO -> lower NO bids
        assert long_no.best_no_bid < neutral.best_no_bid

        # Long NO -> raise YES bids
        assert long_no.best_yes_bid > neutral.best_yes_bid

    def test_price_levels(self):
        """Deeper levels should have worse prices."""
        quotes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )

        # Bids should decrease with level
        for i in range(len(quotes.yes_bids) - 1):
            assert quotes.yes_bids[i].price > quotes.yes_bids[i + 1].price

        # Asks should increase with level
        for i in range(len(quotes.yes_asks) - 1):
            assert quotes.yes_asks[i].price < quotes.yes_asks[i + 1].price

    def test_size_decay(self):
        """Deeper levels should have smaller sizes."""
        quotes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )

        # Sizes should decrease with level
        for i in range(len(quotes.yes_bids) - 1):
            assert quotes.yes_bids[i].size > quotes.yes_bids[i + 1].size

    def test_price_bounds(self):
        """Prices should be within valid range."""
        # Extreme fair values
        quotes = self.engine.generate_quotes(
            fair_yes=0.95,
            fair_no=0.05,
            inventory_skew=0.0,
        )

        for bid in quotes.yes_bids + quotes.no_bids:
            assert 0.02 <= bid.price <= 0.98

        for ask in quotes.yes_asks + quotes.no_asks:
            assert 0.02 <= ask.price <= 0.98

    def test_implied_edge(self):
        """Combined bids should give positive edge."""
        quotes = self.engine.generate_quotes(
            fair_yes=0.50,
            fair_no=0.50,
            inventory_skew=0.0,
        )

        # Best bids should sum to less than 1.0
        assert quotes.best_yes_bid + quotes.best_no_bid < 1.0

        # Implied edge should be positive
        assert quotes.implied_edge > 0


class TestQuoteSet:
    """Tests for QuoteSet."""

    def test_add_quotes(self):
        """Test adding quotes to set."""
        qs = QuoteSet()
        qs.add_yes_bid(0.48, 100)
        qs.add_no_bid(0.47, 100)

        assert len(qs.yes_bids) == 1
        assert len(qs.no_bids) == 1
        assert qs.best_yes_bid == 0.48
        assert qs.best_no_bid == 0.47

    def test_invalid_prices_filtered(self):
        """Invalid prices should be filtered out."""
        qs = QuoteSet()
        qs.add_yes_bid(0.00, 100)  # Invalid
        qs.add_yes_bid(1.00, 100)  # Invalid
        qs.add_yes_bid(-0.10, 100)  # Invalid
        qs.add_yes_bid(1.10, 100)  # Invalid

        assert len(qs.yes_bids) == 0

    def test_implied_edge_calculation(self):
        """Test implied edge calculation."""
        qs = QuoteSet()
        qs.add_yes_bid(0.48, 100)
        qs.add_no_bid(0.47, 100)

        # 1.0 - 0.48 - 0.47 = 0.05
        assert qs.implied_edge == pytest.approx(0.05)

    def test_total_size(self):
        """Test total size calculations."""
        qs = QuoteSet()
        qs.add_yes_bid(0.48, 100)
        qs.add_yes_bid(0.47, 50)
        qs.add_no_bid(0.46, 75)

        assert qs.total_yes_bid_size == 150
        assert qs.total_no_bid_size == 75
