"""
Market discovery for Polymarket hourly BTC markets.

Uses the Gamma API to find and track active markets.
Supports fetching by event slug (e.g., "bitcoin-up-or-down-january-21-8pm-et").
"""

import re
import asyncio
from datetime import datetime, timezone, timedelta
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
    CLOB_URL = "https://clob.polymarket.com"

    # Patterns to identify hourly BTC markets
    BTC_PATTERNS = [
        r"btc",
        r"bitcoin",
    ]

    HOURLY_PATTERNS = [
        r"(\d{1,2}):00",  # "3:00", "15:00"
        r"(\d{1,2})\s*(am|pm)",  # "3 PM", "3PM"
        r"up.?or.?down",  # "up or down" markets
        r"above.*\$[\d,]+",  # "above $97,500"
        r"hourly",
    ]

    EXCLUDE_PATTERNS = [
        r"15.?min",  # Exclude 15-minute markets
        r"4.?hour",  # Exclude 4-hour markets
        r"weekly",
    ]

    # Known event slug patterns for hourly BTC markets
    HOURLY_EVENT_PATTERNS = [
        "bitcoin-up-or-down",
        "btc-up-or-down",
    ]

    def __init__(self, gamma_url: Optional[str] = None, event_slug: Optional[str] = None):
        self.gamma_url = gamma_url or self.GAMMA_URL
        self.event_slug = event_slug  # Optional: specific event to trade
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

    async def get_event_by_slug(self, slug: str) -> Optional[dict]:
        """
        Fetch a specific event by its slug.

        Args:
            slug: Event slug (e.g., "bitcoin-up-or-down-january-21-8pm-et")

        Returns:
            Event data with nested markets, or None
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.gamma_url}/events",
                params={"slug": slug},
            )
            response.raise_for_status()
            events = response.json()

            if events:
                event = events[0]
                logger.info(
                    "Found event by slug",
                    slug=slug,
                    title=event.get("title", ""),
                    markets_count=len(event.get("markets", [])),
                )
                return event

            logger.warning("Event not found", slug=slug)
            return None

        except httpx.HTTPError as e:
            logger.error("Failed to fetch event by slug", slug=slug, error=str(e))
            return None

    async def get_markets_from_event(self, event: dict) -> List[Market]:
        """
        Parse all active markets from an event.

        Args:
            event: Event data from Gamma API

        Returns:
            List of Market objects
        """
        markets = []
        event_markets = event.get("markets", [])

        for raw_market in event_markets:
            market = self._parse_market(raw_market)
            if market and market.is_active:
                markets.append(market)

        # Sort by end time
        markets.sort(key=lambda m: m.end_time)

        logger.info("Parsed markets from event", count=len(markets))
        return markets

    async def find_btc_hourly_events(self) -> List[dict]:
        """
        Search for Bitcoin UP/DOWN hourly events.

        Returns list of events matching the pattern.
        """
        client = await self._get_client()

        try:
            # Search for active events with "bitcoin" in title
            response = await client.get(
                f"{self.gamma_url}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                },
            )
            response.raise_for_status()
            events = response.json()

            btc_events = []
            for event in events:
                title = event.get("title", "").lower()
                slug = event.get("slug", "").lower()

                # Match "bitcoin up or down" pattern
                if ("bitcoin" in title or "btc" in title) and ("up" in title or "down" in title):
                    btc_events.append(event)
                    logger.info(
                        "Found BTC hourly event",
                        title=event.get("title"),
                        slug=slug,
                        markets_count=len(event.get("markets", [])),
                    )

            return btc_events

        except httpx.HTTPError as e:
            logger.error("Failed to search for BTC events", error=str(e))
            return []

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
            markets = response.json()

            # Log BTC-related markets for debugging
            btc_markets = []
            for m in markets:
                q = m.get("question", "").lower()
                if "btc" in q or "bitcoin" in q:
                    btc_markets.append(m.get("question", "")[:80])

            if btc_markets:
                logger.info("Found BTC-related markets", count=len(btc_markets), samples=btc_markets[:5])

            return markets

        except httpx.HTTPError as e:
            logger.error("Failed to fetch markets", error=str(e))
            return []

    async def get_events(self, limit: int = 100) -> List[dict]:
        """
        Fetch events from Gamma API.
        Events can contain multiple related markets.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.gamma_url}/events",
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            events = response.json()
            logger.info("Fetched events", count=len(events))
            return events

        except httpx.HTTPError as e:
            logger.error("Failed to fetch events", error=str(e))
            return []

    async def search_markets(self, query: str) -> List[dict]:
        """
        Search for markets using the CLOB API.
        """
        client = await self._get_client()

        try:
            # Try CLOB API search
            response = await client.get(
                f"{self.CLOB_URL}/markets",
            )
            response.raise_for_status()
            markets = response.json()

            # Filter by query
            query_lower = query.lower()
            filtered = []
            for m in markets:
                q = m.get("question", "").lower()
                desc = m.get("description", "").lower()
                if query_lower in q or query_lower in desc:
                    filtered.append(m)

            logger.info("Search results", query=query, count=len(filtered))
            return filtered

        except httpx.HTTPError as e:
            logger.error("Failed to search markets", error=str(e))
            return []

    async def get_btc_hourly_from_clob(self) -> List[dict]:
        """
        Get BTC hourly markets directly from CLOB API.
        """
        client = await self._get_client()

        try:
            response = await client.get(f"{self.CLOB_URL}/markets")
            response.raise_for_status()
            all_markets = response.json()

            # Filter for BTC up/down hourly markets
            btc_hourly = []
            for m in all_markets:
                q = m.get("question", "").lower()
                # Look for BTC/Bitcoin + price levels
                if ("btc" in q or "bitcoin" in q) and ("above" in q or "below" in q or "up" in q or "down" in q):
                    # Exclude long-term markets
                    if "$1m" not in q and "million" not in q and "100k" not in q.replace(",", ""):
                        btc_hourly.append(m)
                        logger.debug("Found potential BTC hourly market", question=m.get("question", "")[:60])

            logger.info("Found BTC hourly markets from CLOB", count=len(btc_hourly))
            return btc_hourly

        except httpx.HTTPError as e:
            logger.error("Failed to fetch from CLOB", error=str(e))
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

        If event_slug is set, fetches markets from that specific event.
        Otherwise, searches for BTC UP/DOWN events automatically.

        Returns markets sorted by end time (soonest first).
        """
        markets = []

        # If specific event slug is provided, use that directly
        if self.event_slug:
            logger.info("Fetching markets from specified event", slug=self.event_slug)
            event = await self.get_event_by_slug(self.event_slug)
            if event:
                markets = await self.get_markets_from_event(event)
                if markets:
                    self._markets_cache = markets
                    self._cache_time = datetime.now(timezone.utc)
                    logger.info("Found markets from event", count=len(markets))
                    return markets

        # Otherwise, search for BTC hourly events
        logger.info("Searching for BTC hourly events...")
        btc_events = await self.find_btc_hourly_events()

        for event in btc_events:
            event_markets = await self.get_markets_from_event(event)
            markets.extend(event_markets)

        # Deduplicate by condition_id
        seen = set()
        unique_markets = []
        for m in markets:
            if m.condition_id not in seen:
                seen.add(m.condition_id)
                unique_markets.append(m)
        markets = unique_markets

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
