"""
Market discovery for Polymarket hourly BTC markets.

Uses the Gamma API to find and track active markets.
"""

import re
import asyncio
from datetime import datetime, timezone
from typing import Optional, List
import httpx
import structlog

from .market_state import Market, MarketStatus


logger = structlog.get_logger(__name__)


class MarketDiscovery:
    """
    Finds and tracks active hourly BTC markets on Polymarket.

    Polymarket creates new markets continuously. This class:
    1. Finds the current active hourly market
    2. Tracks time until expiry
    3. Identifies upcoming markets for rotation
    """

    GAMMA_URL = "https://gamma-api.polymarket.com"

    # Patterns to identify hourly BTC markets
    BTC_PATTERNS = [
        r"btc",
        r"bitcoin",
    ]

    HOURLY_PATTERNS = [
        r"(\d{1,2}):00",  # "3:00", "15:00"
        r"(\d{1,2})\s*(am|pm)",  # "3 PM", "3PM"
    ]

    EXCLUDE_PATTERNS = [
        r"15.?min",  # Exclude 15-minute markets
        r"4.?hour",  # Exclude 4-hour markets
        r"daily",
        r"weekly",
    ]

    def __init__(self, gamma_url: Optional[str] = None):
        self.gamma_url = gamma_url or self.GAMMA_URL
        self._client: Optional[httpx.AsyncClient] = None
        self._markets_cache: List[Market] = []
        self._cache_time: Optional[datetime] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_all_markets(self, limit: int = 100) -> List[dict]:
        """
        Fetch all active markets from Gamma API.

        Returns raw market data from API.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.gamma_url}/markets",
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            logger.error("Failed to fetch markets", error=str(e))
            return []

    def _is_hourly_btc_market(self, market: dict) -> bool:
        """
        Check if a market is an hourly BTC prediction market.

        Filters for markets that:
        1. Are about BTC/Bitcoin
        2. Have hourly resolution (not 15-min or 4-hour)
        3. Have a strike price
        """
        question = market.get("question", "").lower()
        description = market.get("description", "").lower()
        text = f"{question} {description}"

        # Must mention BTC
        has_btc = any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in self.BTC_PATTERNS
        )
        if not has_btc:
            return False

        # Must have hourly indicator
        has_hourly = any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in self.HOURLY_PATTERNS
        )
        if not has_hourly:
            return False

        # Must NOT match exclusion patterns
        is_excluded = any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in self.EXCLUDE_PATTERNS
        )
        if is_excluded:
            return False

        return True

    def _parse_strike_price(self, question: str) -> Optional[float]:
        """
        Extract strike price from market question.

        Examples:
        - "Will BTC be above $97,500 at 3:00 PM?" -> 97500.0
        - "Bitcoin price above 98000 USD" -> 98000.0
        """
        # Pattern for prices like $97,500 or 97500
        patterns = [
            r"\$?([\d,]+(?:\.\d+)?)",  # $97,500 or 97500
            r"([\d,]+)\s*(?:usd|dollars?)?",  # 97500 USD
        ]

        for pattern in patterns:
            matches = re.findall(pattern, question, re.IGNORECASE)
            for match in matches:
                try:
                    # Remove commas and convert
                    price = float(match.replace(",", ""))
                    # Sanity check: BTC price should be reasonable
                    if 10000 < price < 500000:
                        return price
                except ValueError:
                    continue

        return None

    def _parse_end_time(self, market: dict) -> Optional[datetime]:
        """
        Parse market end time from API response.

        The API returns endDate or end_date_iso in ISO format.
        """
        end_date_str = market.get("endDate") or market.get("end_date_iso")
        if not end_date_str:
            return None

        try:
            # Parse ISO format
            if end_date_str.endswith("Z"):
                end_date_str = end_date_str[:-1] + "+00:00"
            return datetime.fromisoformat(end_date_str)
        except ValueError:
            return None

    def _parse_market(self, raw_market: dict) -> Optional[Market]:
        """
        Parse raw API market data into Market object.
        """
        try:
            condition_id = raw_market.get("conditionId") or raw_market.get("condition_id")
            if not condition_id:
                return None

            question = raw_market.get("question", "")
            description = raw_market.get("description", "")

            # Get token IDs
            tokens = raw_market.get("tokens", [])
            clob_token_ids = raw_market.get("clobTokenIds", [])

            yes_token_id = None
            no_token_id = None

            # Try to get from tokens array
            for token in tokens:
                outcome = token.get("outcome", "").lower()
                token_id = token.get("token_id")
                if outcome == "yes":
                    yes_token_id = token_id
                elif outcome == "no":
                    no_token_id = token_id

            # Fallback to clobTokenIds (index 0 = YES, index 1 = NO)
            if not yes_token_id and len(clob_token_ids) >= 1:
                yes_token_id = clob_token_ids[0]
            if not no_token_id and len(clob_token_ids) >= 2:
                no_token_id = clob_token_ids[1]

            if not yes_token_id or not no_token_id:
                return None

            # Parse strike price
            strike_price = self._parse_strike_price(question)
            if strike_price is None:
                return None

            # Parse end time
            end_time = self._parse_end_time(raw_market)
            if end_time is None:
                return None

            # Get market parameters
            min_order_size = float(raw_market.get("minimum_order_size", 1))
            min_tick_size = float(raw_market.get("minimum_tick_size", 0.01))

            # Get current prices from tokens
            yes_price = 0.5
            no_price = 0.5
            for token in tokens:
                outcome = token.get("outcome", "").lower()
                price = float(token.get("price", 0.5))
                if outcome == "yes":
                    yes_price = price
                elif outcome == "no":
                    no_price = price

            return Market(
                condition_id=condition_id,
                question=question,
                description=description,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                end_time=end_time,
                strike_price=strike_price,
                minimum_order_size=min_order_size,
                minimum_tick_size=min_tick_size,
                status=MarketStatus.ACTIVE,
                yes_price=yes_price,
                no_price=no_price,
                volume=float(raw_market.get("volume", 0)),
                liquidity=float(raw_market.get("liquidity", 0)),
            )

        except Exception as e:
            logger.warning("Failed to parse market", error=str(e), market_id=raw_market.get("conditionId"))
            return None

    async def get_hourly_btc_markets(self) -> List[Market]:
        """
        Find all active hourly BTC markets.

        Returns markets sorted by end time (soonest first).
        """
        raw_markets = await self.get_all_markets()

        markets = []
        for raw_market in raw_markets:
            if self._is_hourly_btc_market(raw_market):
                market = self._parse_market(raw_market)
                if market and market.is_active:
                    markets.append(market)

        # Sort by end time
        markets.sort(key=lambda m: m.end_time)

        self._markets_cache = markets
        self._cache_time = datetime.now(timezone.utc)

        logger.info("Found hourly BTC markets", count=len(markets))
        return markets

    async def get_active_hourly_btc_market(self) -> Optional[Market]:
        """
        Get the current active hourly BTC market.

        Returns the market with the soonest expiry that still has
        enough time remaining for trading.
        """
        markets = await self.get_hourly_btc_markets()

        for market in markets:
            # Skip if too little time remaining
            if market.time_remaining_seconds < 60:  # Less than 1 minute
                continue
            return market

        return None

    async def get_upcoming_markets(self, hours_ahead: int = 3) -> List[Market]:
        """
        Get markets for the next few hours.

        Useful for preparing for market rotation.
        """
        markets = await self.get_hourly_btc_markets()

        now = datetime.now(timezone.utc)
        upcoming = []

        for market in markets:
            hours_until = (market.end_time - now).total_seconds() / 3600
            if 0 < hours_until <= hours_ahead:
                upcoming.append(market)

        return upcoming

    async def get_market_by_condition_id(self, condition_id: str) -> Optional[Market]:
        """
        Get a specific market by its condition ID.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.gamma_url}/markets/{condition_id}",
            )
            response.raise_for_status()
            raw_market = response.json()
            return self._parse_market(raw_market)

        except httpx.HTTPError as e:
            logger.error("Failed to fetch market", condition_id=condition_id, error=str(e))
            return None

    def time_until_expiry(self, market: Market) -> int:
        """Seconds until market resolves."""
        return market.time_remaining_seconds
