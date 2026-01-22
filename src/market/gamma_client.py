"""
Gamma API client for Polymarket market data.

The Gamma API provides market metadata and discovery.
"""

from typing import Optional, List, Dict, Any
import httpx
import structlog


logger = structlog.get_logger(__name__)


class GammaClient:
    """
    Client for Polymarket's Gamma API.

    The Gamma API provides:
    - Market discovery and metadata
    - Historical data
    - Market resolution status
    """

    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self.BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Make an API request."""
        client = await self._get_client()
        url = f"{self.base_url}{endpoint}"

        try:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=data,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                "Gamma API error",
                status=e.response.status_code,
                endpoint=endpoint,
                error=str(e),
            )
            return None

        except httpx.HTTPError as e:
            logger.error(
                "Gamma API request failed",
                endpoint=endpoint,
                error=str(e),
            )
            return None

    async def get_markets(
        self,
        closed: bool = False,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
        tag: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get list of markets.

        Args:
            closed: Include closed markets
            active: Only active markets
            limit: Maximum number of results
            offset: Pagination offset
            tag: Filter by tag (e.g., "crypto-btc")

        Returns:
            List of market dictionaries
        """
        params = {
            "closed": str(closed).lower(),
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        if tag:
            params["tag"] = tag

        result = await self._request("GET", "/markets", params=params)
        return result if result else []

    async def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific market by condition ID.

        Args:
            condition_id: Market condition ID

        Returns:
            Market dictionary or None
        """
        return await self._request("GET", f"/markets/{condition_id}")

    async def search_markets(
        self,
        query: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Search markets by query string.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching markets
        """
        params = {
            "q": query,
            "limit": limit,
        }
        result = await self._request("GET", "/markets", params=params)
        return result if result else []

    async def get_btc_markets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get BTC-related markets.

        Uses the crypto-btc tag to filter.
        """
        return await self.get_markets(tag="crypto-btc", limit=limit)

    async def get_market_events(
        self,
        condition_id: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get events for a specific market.

        Includes trades, resolutions, etc.
        """
        result = await self._request(
            "GET",
            f"/markets/{condition_id}/events",
            params={"limit": limit},
        )
        return result if result else []

    async def get_market_trades(
        self,
        condition_id: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades for a market.
        """
        result = await self._request(
            "GET",
            f"/markets/{condition_id}/trades",
            params={"limit": limit},
        )
        return result if result else []
