"""
Tests for sports quote engine.
"""
import pytest
from datetime import datetime, timezone

from src.sports.sports_discovery import SportMarket
from src.sports.sports_quote_engine import (
    Quote,
    TwoSidedQuote,
    Position,
    QuoteEngine,
    MultiLevelQuoteEngine,
)


@pytest.fixture
def market():
    """Create a test market."""
    return SportMarket(
        event_id="1",
        event_title="Lakers vs Celtics",
        market_id="market1",
        question="Will Lakers win?",
        condition_id="abc",
        yes_token_id="token1",
        no_token_id="token2",
        outcomes=["Lakers", "Celtics"],
        outcome_prices=[0.55, 0.45],
        game_start_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
        sport="NBA",
    )


@pytest.fixture
def quote_engine():
    """Create a test quote engine."""
    return QuoteEngine(
        min_edge=0.02,
        default_size=100.0,
        max_position=500.0,
    )


class TestQuote:
    """Tests for Quote dataclass."""

    def test_price_clamping(self):
        """Test price is clamped to valid range."""
        quote = Quote(price=1.5, size=100)
        assert quote.price == 0.99

        quote = Quote(price=-0.5, size=100)
        assert quote.price == 0.01

    def test_size_non_negative(self):
        """Test size is non-negative."""
        quote = Quote(price=0.5, size=-10)
        assert quote.size == 0


class TestPosition:
    """Tests for Position dataclass."""

    def test_net_position(self):
        """Test net position calculation."""
        pos = Position(home_shares=100, away_shares=50)
        assert pos.net_position == 50

    def test_hedged_shares(self):
        """Test hedged shares calculation."""
        pos = Position(home_shares=100, away_shares=50)
        assert pos.hedged_shares == 50

    def test_inventory_skew(self):
        """Test inventory skew calculation."""
        pos = Position(home_shares=100, away_shares=100)
        assert pos.inventory_skew == 0.0

        pos = Position(home_shares=100, away_shares=0)
        assert pos.inventory_skew == 1.0

        pos = Position(home_shares=0, away_shares=100)
        assert pos.inventory_skew == -1.0


class TestQuoteEngine:
    """Tests for QuoteEngine."""

    def test_calculate_fair_value_from_market(self, market, quote_engine):
        """Test fair value from market prices."""
        fair_home, fair_away = quote_engine.calculate_fair_value(
            market,
            home_best_bid=None,
            home_best_ask=None,
            away_best_bid=None,
            away_best_ask=None,
        )

        # Should use market prices, normalized
        assert abs(fair_home + fair_away - 1.0) < 0.01
        assert fair_home > fair_away  # Lakers favored in fixture

    def test_calculate_fair_value_from_orderbook(self, market, quote_engine):
        """Test fair value from orderbook mid."""
        fair_home, fair_away = quote_engine.calculate_fair_value(
            market,
            home_best_bid=0.50,
            home_best_ask=0.52,
            away_best_bid=0.46,
            away_best_ask=0.48,
        )

        # Should use orderbook mid, normalized
        assert abs(fair_home + fair_away - 1.0) < 0.01

    def test_calculate_fair_value_sharp_odds(self, market, quote_engine):
        """Test fair value from sharp odds."""
        fair_home, fair_away = quote_engine.calculate_fair_value(
            market,
            home_best_bid=0.50,
            home_best_ask=0.52,
            away_best_bid=0.46,
            away_best_ask=0.48,
            sharp_home_prob=0.60,
        )

        # Should use sharp odds
        assert fair_home == 0.60
        assert fair_away == 0.40

    def test_calculate_edge(self, quote_engine):
        """Test edge calculation."""
        # No edge
        edge = quote_engine.calculate_edge(0.52, 0.50)
        assert edge < 0.01

        # Positive edge
        edge = quote_engine.calculate_edge(0.48, 0.48)
        assert edge == 0.04  # 1 - 0.96 = 0.04

    def test_generate_quotes_no_edge(self, market, quote_engine):
        """Test no quotes when no edge."""
        quotes = quote_engine.generate_quotes(
            market,
            home_best_bid=0.50,
            home_best_ask=0.52,  # Total = 1.02, no edge
            away_best_bid=0.48,
            away_best_ask=0.50,
        )

        assert quotes.home_bid is None
        assert quotes.away_bid is None
        assert quotes.edge < quote_engine.min_edge

    def test_generate_quotes_with_edge(self, market, quote_engine):
        """Test quotes generated when edge available."""
        # Create edge by having asks sum to less than 1
        quotes = quote_engine.generate_quotes(
            market,
            home_best_bid=0.45,
            home_best_ask=0.47,  # Total = 0.94, edge = 0.06
            away_best_bid=0.45,
            away_best_ask=0.47,
        )

        assert quotes.can_trade
        assert quotes.edge >= quote_engine.min_edge

        if quotes.home_bid:
            assert 0.01 <= quotes.home_bid.price <= 0.99
            assert quotes.home_bid.size > 0

    def test_generate_quotes_inventory_adjustment(self, market, quote_engine):
        """Test inventory adjustment in quotes."""
        position_long_home = Position(home_shares=200, away_shares=0)
        position_long_away = Position(home_shares=0, away_shares=200)

        quotes_long_home = quote_engine.generate_quotes(
            market,
            home_best_bid=0.45,
            home_best_ask=0.47,
            away_best_bid=0.45,
            away_best_ask=0.47,
            position=position_long_home,
        )

        quotes_long_away = quote_engine.generate_quotes(
            market,
            home_best_bid=0.45,
            home_best_ask=0.47,
            away_best_bid=0.45,
            away_best_ask=0.47,
            position=position_long_away,
        )

        # When long home, should raise home prices to reduce inventory
        # When long away, should lower home prices
        if quotes_long_home.home_bid and quotes_long_away.home_bid:
            # Long home should have lower bid (less eager to buy more)
            assert quotes_long_home.home_bid.price < quotes_long_away.home_bid.price

    def test_should_requote(self, market, quote_engine):
        """Test requote threshold."""
        quotes1 = TwoSidedQuote(
            home_bid=Quote(0.50, 100),
            home_ask=Quote(0.52, 100),
            away_bid=Quote(0.48, 100),
            away_ask=Quote(0.50, 100),
            fair_home=0.51,
            fair_away=0.49,
            edge=0.02,
        )

        # Same quotes - should not requote
        quotes2 = TwoSidedQuote(
            home_bid=Quote(0.50, 100),
            home_ask=Quote(0.52, 100),
            away_bid=Quote(0.48, 100),
            away_ask=Quote(0.50, 100),
            fair_home=0.51,
            fair_away=0.49,
            edge=0.02,
        )

        assert not quote_engine.should_requote(quotes1, quotes2)

        # Changed quotes - should requote
        quotes3 = TwoSidedQuote(
            home_bid=Quote(0.55, 100),  # Changed
            home_ask=Quote(0.52, 100),
            away_bid=Quote(0.48, 100),
            away_ask=Quote(0.50, 100),
            fair_home=0.51,
            fair_away=0.49,
            edge=0.02,
        )

        assert quote_engine.should_requote(quotes1, quotes3)


class TestMultiLevelQuoteEngine:
    """Tests for MultiLevelQuoteEngine."""

    def test_multi_level_quotes(self, market):
        """Test multi-level quote generation."""
        engine = MultiLevelQuoteEngine(
            min_edge=0.02,
            default_size=100.0,
            max_position=500.0,
            num_levels=3,
            level_spacing=0.01,
            size_decay=0.5,
        )

        quotes = engine.generate_multi_level_quotes(
            market,
            home_best_bid=0.45,
            home_best_ask=0.47,
            away_best_bid=0.45,
            away_best_ask=0.47,
        )

        assert len(quotes) == 3

        # First level should have best prices
        # Subsequent levels should be wider
        if quotes[0].home_bid and quotes[1].home_bid:
            assert quotes[1].home_bid.price < quotes[0].home_bid.price
            assert quotes[1].home_bid.size < quotes[0].home_bid.size
