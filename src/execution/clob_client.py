"""
Polymarket CLOB API client wrapper.

Wraps the official py-clob-client with additional functionality.
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import structlog

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    BookParams,
    OpenOrderParams,
)
from py_clob_client.order_builder.constants import BUY, SELL


logger = structlog.get_logger(__name__)


@dataclass
class OrderbookSnapshot:
    """Snapshot of an orderbook."""
    token_id: str
    bids: List[tuple]  # [(price, size), ...]
    asks: List[tuple]
    timestamp: int


@dataclass
class OrderResponse:
    """Response from order placement."""
    order_id: Optional[str]
    success: bool
    error_message: Optional[str] = None


class PolymarketCLOB:
    """
    Client for Polymarket's Central Limit Order Book API.

    Wraps the official py-clob-client with convenience methods.
    """

    def __init__(
        self,
        client: ClobClient,
        paper_trading: bool = False,
    ):
        """
        Initialize CLOB client.

        Args:
            client: Initialized ClobClient from py-clob-client
            paper_trading: If True, don't actually submit orders
        """
        self.client = client
        self.paper_trading = paper_trading

    @classmethod
    def create(
        cls,
        host: str,
        private_key: str,
        safe_address: str,
        chain_id: int = 137,
        paper_trading: bool = False,
    ) -> "PolymarketCLOB":
        """
        Create a new CLOB client.

        Args:
            host: CLOB API URL
            private_key: Private key for signing
            safe_address: Polymarket Safe address
            chain_id: Polygon chain ID
            paper_trading: Paper trading mode

        Returns:
            Configured PolymarketCLOB
        """
        if paper_trading:
            # Read-only client for paper trading
            client = ClobClient(host)
        else:
            client = ClobClient(
                host,
                key=private_key,
                chain_id=chain_id,
                signature_type=2,  # Polymarket proxy wallet
                funder=safe_address,
            )
            # Set API credentials
            client.set_api_creds(client.create_or_derive_api_creds())

        return cls(client=client, paper_trading=paper_trading)

    def get_orderbook(self, token_id: str) -> OrderbookSnapshot:
        """
        Get current orderbook for a token.

        Args:
            token_id: Token ID to query

        Returns:
            OrderbookSnapshot with bids and asks
        """
        try:
            book = self.client.get_order_book(token_id)

            bids = [
                (float(level.price), float(level.size))
                for level in (book.bids or [])
            ]
            asks = [
                (float(level.price), float(level.size))
                for level in (book.asks or [])
            ]

            return OrderbookSnapshot(
                token_id=token_id,
                bids=bids,
                asks=asks,
                timestamp=book.timestamp if hasattr(book, 'timestamp') else 0,
            )

        except Exception as e:
            logger.error("Failed to get orderbook", token_id=token_id, error=str(e))
            return OrderbookSnapshot(token_id=token_id, bids=[], asks=[], timestamp=0)

    def get_orderbooks(
        self,
        yes_token_id: str,
        no_token_id: str,
    ) -> Dict[str, OrderbookSnapshot]:
        """
        Get orderbooks for both YES and NO tokens.

        Args:
            yes_token_id: YES token ID
            no_token_id: NO token ID

        Returns:
            Dict with 'yes' and 'no' orderbook snapshots
        """
        try:
            books = self.client.get_order_books([
                BookParams(token_id=yes_token_id),
                BookParams(token_id=no_token_id),
            ])

            result = {}
            for book in books:
                token_id = book.asset_id
                bids = [
                    (float(level.price), float(level.size))
                    for level in (book.bids or [])
                ]
                asks = [
                    (float(level.price), float(level.size))
                    for level in (book.asks or [])
                ]

                key = 'yes' if token_id == yes_token_id else 'no'
                result[key] = OrderbookSnapshot(
                    token_id=token_id,
                    bids=bids,
                    asks=asks,
                    timestamp=book.timestamp if hasattr(book, 'timestamp') else 0,
                )

            return result

        except Exception as e:
            logger.error("Failed to get orderbooks", error=str(e))
            return {
                'yes': OrderbookSnapshot(token_id=yes_token_id, bids=[], asks=[], timestamp=0),
                'no': OrderbookSnapshot(token_id=no_token_id, bids=[], asks=[], timestamp=0),
            }

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token."""
        try:
            return self.client.get_midpoint(token_id)
        except Exception as e:
            logger.error("Failed to get midpoint", token_id=token_id, error=str(e))
            return None

    def get_spread(self, token_id: str) -> Optional[float]:
        """Get spread for a token."""
        try:
            return self.client.get_spread(token_id)
        except Exception as e:
            logger.error("Failed to get spread", token_id=token_id, error=str(e))
            return None

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> OrderResponse:
        """
        Place a limit order (GTC).

        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            price: Limit price (0.00 - 1.00)
            size: Number of shares

        Returns:
            OrderResponse with order_id or error
        """
        if self.paper_trading:
            logger.info(
                "Paper trade: limit order",
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )
            return OrderResponse(
                order_id=f"paper_{token_id}_{side}_{price}",
                success=True,
            )

        try:
            order_side = BUY if side.upper() == "BUY" else SELL

            order = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )

            # BTC UP/DOWN markets are NEG_RISK markets - require neg_risk=True
            from py_clob_client.clob_types import PartialCreateOrderOptions
            options = PartialCreateOrderOptions(neg_risk=True)
            signed = self.client.create_order(order, options)
            resp = self.client.post_order(signed, OrderType.GTC)

            order_id = resp.get("orderID") or resp.get("id")

            logger.info(
                "Limit order placed",
                order_id=order_id,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )

            return OrderResponse(order_id=order_id, success=True)

        except Exception as e:
            logger.error(
                "Failed to place limit order",
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                error=str(e),
            )
            return OrderResponse(order_id=None, success=False, error_message=str(e))

    def place_market_order(
        self,
        token_id: str,
        side: str,
        amount: float,
    ) -> OrderResponse:
        """
        Place a market order (FOK).

        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            amount: Dollar amount to spend

        Returns:
            OrderResponse with order_id or error
        """
        if self.paper_trading:
            logger.info(
                "Paper trade: market order",
                token_id=token_id,
                side=side,
                amount=amount,
            )
            return OrderResponse(
                order_id=f"paper_mkt_{token_id}_{side}",
                success=True,
            )

        try:
            order_side = BUY if side.upper() == "BUY" else SELL

            order = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=order_side,
            )

            signed = self.client.create_market_order(order)
            resp = self.client.post_order(signed, OrderType.FOK)

            order_id = resp.get("orderID") or resp.get("id")

            return OrderResponse(order_id=order_id, success=True)

        except Exception as e:
            logger.error(
                "Failed to place market order",
                token_id=token_id,
                side=side,
                amount=amount,
                error=str(e),
            )
            return OrderResponse(order_id=None, success=False, error_message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if self.paper_trading:
            logger.info("Paper trade: cancel order", order_id=order_id)
            return True

        try:
            self.client.cancel(order_id)
            logger.info("Order cancelled", order_id=order_id)
            return True

        except Exception as e:
            logger.error("Failed to cancel order", order_id=order_id, error=str(e))
            return False

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        if self.paper_trading:
            logger.info("Paper trade: cancel all orders")
            return 0

        try:
            resp = self.client.cancel_all()
            cancelled = resp.get("canceled", []) if resp else []
            logger.info("Cancelled all orders", count=len(cancelled))
            return len(cancelled)

        except Exception as e:
            logger.error("Failed to cancel all orders", error=str(e))
            return 0

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders.

        Args:
            market_id: Optional market to filter by

        Returns:
            List of open orders
        """
        try:
            params = OpenOrderParams()
            if market_id:
                params.market = market_id

            return self.client.get_orders(params) or []

        except Exception as e:
            logger.error("Failed to get open orders", error=str(e))
            return []

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades."""
        try:
            return self.client.get_trades() or []
        except Exception as e:
            logger.error("Failed to get trades", error=str(e))
            return []
