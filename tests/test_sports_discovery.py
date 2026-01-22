"""
Tests for sports market discovery.
"""
import pytest
from datetime import datetime, timezone

from src.sports.sports_discovery import (
    SportMarket,
    _detect_sport,
    _detect_market_type,
    _is_sports_event,
    _parse_datetime,
    _parse_list,
    _parse_prices,
)


class TestSportMarket:
    """Tests for SportMarket dataclass."""

    def test_home_away_properties(self):
        """Test home/away team properties."""
        market = SportMarket(
            event_id="1",
            event_title="Lakers vs Celtics",
            market_id="1",
            question="Will Lakers win?",
            condition_id="abc",
            yes_token_id="token1",
            no_token_id="token2",
            outcomes=["Lakers", "Celtics"],
            outcome_prices=[0.55, 0.45],
            game_start_time=None,
            sport="NBA",
        )

        assert market.home_team == "Lakers"
        assert market.away_team == "Celtics"
        assert market.home_price == 0.55
        assert market.away_price == 0.45

    def test_time_to_start(self):
        """Test time to start calculation."""
        future_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
        market = SportMarket(
            event_id="1",
            event_title="Test",
            market_id="1",
            question="Test?",
            condition_id="abc",
            yes_token_id="token1",
            no_token_id="token2",
            outcomes=["A", "B"],
            outcome_prices=[0.5, 0.5],
            game_start_time=future_time,
            sport="NBA",
        )

        assert market.time_to_start is not None
        assert market.time_to_start > 0
        assert market.is_upcoming is True

    def test_is_upcoming_past_game(self):
        """Test is_upcoming for past games."""
        past_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        market = SportMarket(
            event_id="1",
            event_title="Test",
            market_id="1",
            question="Test?",
            condition_id="abc",
            yes_token_id="token1",
            no_token_id="token2",
            outcomes=["A", "B"],
            outcome_prices=[0.5, 0.5],
            game_start_time=past_time,
            sport="NBA",
        )

        assert market.is_upcoming is False


class TestDetection:
    """Tests for sport and market type detection."""

    def test_detect_nba(self):
        """Test NBA detection."""
        event = {"title": "Lakers vs Celtics", "tags": ["nba", "basketball"]}
        assert _detect_sport(event) == "NBA"

    def test_detect_nfl(self):
        """Test NFL detection."""
        event = {"title": "Chiefs vs Eagles", "tags": ["nfl"]}
        assert _detect_sport(event) == "NFL"

    def test_detect_from_team_name(self):
        """Test detection from team names in title."""
        event = {"title": "Will the Lakers win tonight?", "tags": []}
        assert _detect_sport(event) == "NBA"

    def test_detect_moneyline(self):
        """Test moneyline detection."""
        assert _detect_market_type("Will Lakers win?") == "moneyline"
        assert _detect_market_type("Lakers to beat Celtics") == "moneyline"

    def test_detect_spread(self):
        """Test spread detection."""
        assert _detect_market_type("Lakers cover -5.5?") == "spread"
        assert _detect_market_type("Will Lakers win by more than 5?") == "spread"

    def test_detect_over_under(self):
        """Test over/under detection."""
        assert _detect_market_type("Over/Under 220.5") == "over_under"
        assert _detect_market_type("Total points over 220?") == "over_under"


class TestIsSportsEvent:
    """Tests for sports event detection."""

    def test_nba_event(self):
        """Test NBA event detection."""
        event = {
            "title": "Lakers vs Celtics",
            "tags": ["nba", "basketball"],
            "category": "sports",
        }
        assert _is_sports_event(event) is True

    def test_non_sports_event(self):
        """Test non-sports event detection."""
        event = {
            "title": "Will Bitcoin reach 100k?",
            "tags": ["crypto"],
            "category": "crypto",
        }
        assert _is_sports_event(event) is False

    def test_vs_in_title(self):
        """Test detection from 'vs' in title."""
        event = {
            "title": "Lakers vs Celtics Championship",
            "tags": [],
            "category": "",
        }
        assert _is_sports_event(event) is True


class TestParsers:
    """Tests for parsing utilities."""

    def test_parse_datetime(self):
        """Test datetime parsing."""
        dt = _parse_datetime("2024-01-15T19:00:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_datetime_none(self):
        """Test datetime parsing with None."""
        assert _parse_datetime(None) is None
        assert _parse_datetime("") is None

    def test_parse_list(self):
        """Test list parsing."""
        assert _parse_list('["A", "B"]') == ["A", "B"]
        assert _parse_list(["A", "B"]) == ["A", "B"]
        assert _parse_list("invalid") == []

    def test_parse_prices(self):
        """Test price parsing."""
        assert _parse_prices('[0.55, 0.45]') == [0.55, 0.45]
        assert _parse_prices([0.55, 0.45]) == [0.55, 0.45]
        assert _parse_prices('["0.55", "0.45"]') == [0.55, 0.45]
