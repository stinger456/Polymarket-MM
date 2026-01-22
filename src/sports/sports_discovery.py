"""
Sports market discovery for Polymarket.

Uses Gamma API to discover NBA, NFL, MLB, NHL, and Soccer game markets.

Sports markets on Polymarket are typically:
- Moneyline (team to win)
- Spread (team to cover)
- Over/Under (total points)
- Player props

This module focuses on moneyline markets as they have the simplest pricing.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
import httpx
import structlog

logger = structlog.get_logger()


GAMMA_URL = "https://gamma-api.polymarket.com"

# Sports tags - these are common tags for sports events
SPORTS_TAGS = {
    "NBA": ["nba", "basketball", "nba-basketball"],
    "NFL": ["nfl", "football", "nfl-football", "american-football"],
    "MLB": ["mlb", "baseball"],
    "NHL": ["nhl", "hockey", "ice-hockey"],
    "SOCCER": ["soccer", "football", "mls", "premier-league", "uefa", "fifa"],
    "NCAA_BASKETBALL": ["ncaa", "college-basketball", "march-madness"],
    "NCAA_FOOTBALL": ["ncaa-football", "college-football"],
    "UFC": ["ufc", "mma", "mixed-martial-arts"],
    "BOXING": ["boxing"],
    "TENNIS": ["tennis", "atp", "wta"],
    "GOLF": ["golf", "pga"],
}

# Keywords to identify sports events in titles
SPORTS_KEYWORDS = [
    "vs", "versus", "match", "game", "fight",
    "Lakers", "Celtics", "Warriors", "Heat", "Knicks", "Bulls",  # NBA
    "Chiefs", "Eagles", "Cowboys", "49ers", "Patriots", "Bills",  # NFL
    "Yankees", "Dodgers", "Red Sox", "Cubs", "Mets",  # MLB
    "Manchester", "Liverpool", "Arsenal", "Chelsea", "Barcelona", "Real Madrid",  # Soccer
]

# Market types we're interested in
MARKET_TYPES = [
    "moneyline", "winner", "win", "to win",
    "spread", "point spread", "cover",
    "over/under", "total", "over", "under",
]


@dataclass
class SportMarket:
    """A single sports market (e.g., Lakers vs Celtics moneyline)."""

    event_id: str
    event_title: str
    market_id: str
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    outcomes: List[str]
    outcome_prices: List[float]
    game_start_time: Optional[datetime]
    sport: str
    market_type: str = "moneyline"
    volume: float = 0.0
    liquidity: float = 0.0

    @property
    def home_team(self) -> str:
        """Get home team (first outcome)."""
        return self.outcomes[0] if self.outcomes else ""

    @property
    def away_team(self) -> str:
        """Get away team (second outcome)."""
        return self.outcomes[1] if len(self.outcomes) > 1 else ""

    @property
    def home_price(self) -> float:
        """Get home team price (probability)."""
        return self.outcome_prices[0] if self.outcome_prices else 0.5

    @property
    def away_price(self) -> float:
        """Get away team price (probability)."""
        return self.outcome_prices[1] if len(self.outcome_prices) > 1 else 0.5

    @property
    def time_to_start(self) -> Optional[float]:
        """Get seconds until game starts."""
        if not self.game_start_time:
            return None
        delta = self.game_start_time - datetime.now(self.game_start_time.tzinfo)
        return delta.total_seconds()

    @property
    def is_upcoming(self) -> bool:
        """Check if game hasn't started yet."""
        if not self.game_start_time:
            return False  # Unknown start time, skip
        now = datetime.now(self.game_start_time.tzinfo)
        return self.game_start_time > now

    @property
    def is_today(self) -> bool:
        """Check if game is scheduled for today."""
        if not self.game_start_time:
            return False
        from datetime import timezone
        now = datetime.now(timezone.utc)
        game_date = self.game_start_time.date()
        today = now.date()
        return game_date == today

    @property
    def is_tradeable(self) -> bool:
        """Check if game is today AND hasn't started yet - ready to trade."""
        return self.is_today and self.is_upcoming


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not dt_str:
        return None
    try:
        # Handle various ISO formats
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None


def _parse_list(value: Any) -> List[str]:
    """Parse a list from string or return as-is."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            import json
            return json.loads(value)
        except:
            try:
                return eval(value)
            except:
                return []
    return []


def _parse_prices(value: Any) -> List[float]:
    """Parse prices from string or return as-is."""
    if isinstance(value, list):
        return [float(p) for p in value]
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            return [float(p) for p in parsed]
        except:
            try:
                parsed = eval(value)
                return [float(p) for p in parsed]
            except:
                return []
    return []


def _detect_sport(event: Dict) -> str:
    """Detect sport from event data."""
    title = event.get("title", "").lower()
    tags = str(event.get("tags", [])).lower()
    category = event.get("category", "").lower()

    # Check each sport's tags and keywords
    for sport, sport_tags in SPORTS_TAGS.items():
        for tag in sport_tags:
            if tag in tags or tag in title or tag in category:
                return sport

    # Check for team names
    title_upper = event.get("title", "").upper()
    if any(team in title_upper for team in ["LAKERS", "CELTICS", "WARRIORS", "NETS", "HEAT", "BULLS"]):
        return "NBA"
    if any(team in title_upper for team in ["CHIEFS", "EAGLES", "COWBOYS", "49ERS", "PATRIOTS"]):
        return "NFL"
    if any(team in title_upper for team in ["YANKEES", "DODGERS", "RED SOX", "CUBS", "METS"]):
        return "MLB"

    return "UNKNOWN"


def _detect_market_type(question: str) -> str:
    """Detect market type from question."""
    question_lower = question.lower()

    # Check spread first (contains "by more than" which could also have "win")
    if any(x in question_lower for x in ["spread", "cover", "by more than", "by at least"]):
        return "spread"
    if any(x in question_lower for x in ["over/under", "total", "combined"]):
        return "over_under"
    if any(x in question_lower for x in ["winner", "win", "moneyline", "to beat"]):
        return "moneyline"

    return "moneyline"


def _is_sports_event(event: Dict) -> bool:
    """Check if event is sports-related."""
    title = event.get("title", "").lower()
    tags = str(event.get("tags", [])).lower()
    category = event.get("category", "").lower()

    # Check for sports tags
    for sport_tags in SPORTS_TAGS.values():
        for tag in sport_tags:
            if tag in tags or tag in category:
                return True

    # Check for sports keywords in title
    title_lower = title.lower()
    if " vs " in title_lower or " versus " in title_lower:
        # Likely a match/game
        for keyword in SPORTS_KEYWORDS:
            if keyword.lower() in title_lower:
                return True

    return False


async def get_sports_events(
    sport: Optional[str] = None,
    active_only: bool = True,
    limit: int = 100,
) -> List[Dict]:
    """
    Get active sports events from Gamma API.

    Args:
        sport: Specific sport to filter (NBA, NFL, MLB, etc.)
        active_only: Only return active markets
        limit: Maximum events to return

    Returns:
        List of event dictionaries
    """
    params = {
        "active": "true" if active_only else "false",
        "closed": "false",
        "limit": limit,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        try:
            resp = await client.get(f"{GAMMA_URL}/events", params=params)
            resp.raise_for_status()
            events = resp.json()

            if not events:
                logger.info("no_events_found")
                return []

            # Filter to sports events
            sports_events = []
            for event in events:
                if not _is_sports_event(event):
                    continue

                detected_sport = _detect_sport(event)
                if sport and detected_sport != sport.upper():
                    continue

                event["_detected_sport"] = detected_sport
                sports_events.append(event)

            logger.info(
                "sports_events_found",
                total_events=len(events),
                sports_events=len(sports_events),
                sport_filter=sport,
            )

            return sports_events

        except httpx.HTTPError as e:
            logger.error("gamma_api_error", error=str(e))
            return []
        except Exception as e:
            logger.error("event_fetch_error", error=str(e))
            return []


async def get_game_markets(sport: Optional[str] = None) -> List[SportMarket]:
    """
    Get all active game markets for sports.

    Args:
        sport: Specific sport to filter (NBA, NFL, MLB, etc.)

    Returns:
        List of SportMarket objects ready for trading
    """
    events = await get_sports_events(sport=sport)
    markets = []

    for event in events:
        event_id = str(event.get("id", ""))
        event_title = event.get("title", "")
        detected_sport = event.get("_detected_sport", "UNKNOWN")

        for market in event.get("markets", []):
            # Skip if no CLOB token IDs
            clob_ids = market.get("clobTokenIds", [])
            if len(clob_ids) < 2:
                continue

            # Parse outcomes and prices
            outcomes = _parse_list(market.get("outcomes", "[]"))
            prices = _parse_prices(market.get("outcomePrices", "[]"))

            if len(outcomes) < 2 or len(prices) < 2:
                continue

            # Get question
            question = market.get("question", "")
            market_type = _detect_market_type(question)

            # Parse game start time
            start_time = _parse_datetime(
                market.get("gameStartTime") or event.get("startDate")
            )

            # Get volume and liquidity
            volume = float(market.get("volume", 0) or 0)
            liquidity = float(market.get("liquidity", 0) or 0)

            markets.append(SportMarket(
                event_id=event_id,
                event_title=event_title,
                market_id=str(market.get("id", "")),
                question=question,
                condition_id=market.get("conditionId", ""),
                yes_token_id=clob_ids[0],
                no_token_id=clob_ids[1],
                outcomes=outcomes,
                outcome_prices=prices,
                game_start_time=start_time,
                sport=detected_sport,
                market_type=market_type,
                volume=volume,
                liquidity=liquidity,
            ))

    logger.info("game_markets_found", count=len(markets))
    return markets


async def get_all_sports_markets() -> List[SportMarket]:
    """Get markets across all supported sports."""
    return await get_game_markets(sport=None)


async def get_markets_by_sport() -> Dict[str, List[SportMarket]]:
    """Get markets grouped by sport."""
    all_markets = await get_all_sports_markets()

    by_sport: Dict[str, List[SportMarket]] = {}
    for market in all_markets:
        sport = market.sport
        if sport not in by_sport:
            by_sport[sport] = []
        by_sport[sport].append(market)

    return by_sport


async def search_markets(query: str) -> List[SportMarket]:
    """
    Search for markets by query string.

    Args:
        query: Search term (team name, event name, etc.)

    Returns:
        List of matching SportMarket objects
    """
    all_markets = await get_all_sports_markets()
    query_lower = query.lower()

    matching = []
    for market in all_markets:
        if (query_lower in market.event_title.lower() or
            query_lower in market.question.lower() or
            any(query_lower in outcome.lower() for outcome in market.outcomes)):
            matching.append(market)

    return matching


# Convenience functions for specific sports
async def get_nba_markets() -> List[SportMarket]:
    """Get NBA basketball markets."""
    return await get_game_markets(sport="NBA")


async def get_nfl_markets() -> List[SportMarket]:
    """Get NFL football markets."""
    return await get_game_markets(sport="NFL")


async def get_mlb_markets() -> List[SportMarket]:
    """Get MLB baseball markets."""
    return await get_game_markets(sport="MLB")


async def get_nhl_markets() -> List[SportMarket]:
    """Get NHL hockey markets."""
    return await get_game_markets(sport="NHL")


async def get_soccer_markets() -> List[SportMarket]:
    """Get soccer/football markets."""
    return await get_game_markets(sport="SOCCER")


if __name__ == "__main__":
    # Test the discovery
    async def main():
        print("Fetching all sports markets...")
        markets = await get_all_sports_markets()
        print(f"\nFound {len(markets)} sports markets:\n")

        for market in markets[:10]:
            print(f"Sport: {market.sport}")
            print(f"Event: {market.event_title}")
            print(f"Question: {market.question}")
            print(f"Outcomes: {market.outcomes}")
            print(f"Prices: {market.outcome_prices}")
            print(f"Volume: ${market.volume:,.0f}")
            print("-" * 50)

    asyncio.run(main())
