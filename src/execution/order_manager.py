"""
Order lifecycle management.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Set
from enum import Enum
import threading
import structlog

from .clob_client import PolymarketCLOB, OrderResponse
from ..quoting.quote_engine import QuoteSet, Quote


logger = structlog.get_logger(__name__)


class OrderStatus(Enum):
    """Order lifecycle status."""
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """Represents a single order."""
    order_id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    filled_size: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    token_type: str = ""  # "YES" or "NO"

    @property
    def remaining_size(self) -> float:
        """Unfilled size."""
        return self.size - self.filled_size

    @property
    def is_active(self) -> bool:
        """Check if order is active."""
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


class OrderManager:
    """
    Manages order lifecycle on Polymarket CLOB.

    Responsibilities:
    - Place limit orders
    - Cancel/replace orders
    - Track order status
    - Handle fills
    """

    def __init__(
        self,
        clob_client: PolymarketCLOB,
        yes_token_id: str = "",
        no_token_id: str = "",
    ):
        """
        Initialize order manager.

        Args:
            clob_client: Polymarket CLOB client
            yes_token_id: YES token ID for current market
            no_token_id: NO token ID for current market
        """
        self.clob = clob_client
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id

        self._orders: Dict[str, Order] = {}
        self._active_orders: Set[str] = set()
        self._lock = threading.Lock()

    def set_market(self, yes_token_id: str, no_token_id: str):
        """
        Set the current market tokens.

        Args:
            yes_token_id: YES token ID
            no_token_id: NO token ID
        """
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id

    @property
    def active_orders(self) -> List[Order]:
        """Get all active orders."""
        with self._lock:
            return [
                self._orders[oid]
                for oid in self._active_orders
                if oid in self._orders
            ]

    @property
    def yes_orders(self) -> List[Order]:
        """Get active YES orders."""
        return [o for o in self.active_orders if o.token_id == self.yes_token_id]

    @property
    def no_orders(self) -> List[Order]:
        """Get active NO orders."""
        return [o for o in self.active_orders if o.token_id == self.no_token_id]

    def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
    ) -> Optional[str]:
        """
        Place a limit order on the CLOB.

        Args:
            token_id: Token to trade
            side: BUY or SELL
            price: Limit price
            size: Order size

        Returns:
            Order ID if successful, None otherwise
        """
        response = self.clob.place_limit_order(
            token_id=token_id,
            side=side.value,
            price=price,
            size=size,
        )

        if not response.success or not response.order_id:
            logger.error(
                "Order placement failed",
                token_id=token_id,
                side=side.value,
                price=price,
                error=response.error_message,
            )
            return None

        # Track the order
        order = Order(
            order_id=response.order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.OPEN,
            token_type="YES" if token_id == self.yes_token_id else "NO",
        )

        with self._lock:
            self._orders[response.order_id] = order
            self._active_orders.add(response.order_id)

        return response.order_id

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an active order.

        Args:
            order_id: Order to cancel

        Returns:
            True if cancelled successfully
        """
        success = self.clob.cancel_order(order_id)

        if success:
            with self._lock:
                if order_id in self._orders:
                    self._orders[order_id].status = OrderStatus.CANCELLED
                    self._orders[order_id].updated_at = datetime.now(timezone.utc)
                self._active_orders.discard(order_id)

        return success

    def cancel_all_orders(self, market: Optional[str] = None) -> int:
        """
        Cancel all orders, optionally filtered by market.

        Args:
            market: Optional market to filter

        Returns:
            Number of orders cancelled
        """
        count = self.clob.cancel_all_orders()

        with self._lock:
            for order_id in list(self._active_orders):
                if order_id in self._orders:
                    self._orders[order_id].status = OrderStatus.CANCELLED
                    self._orders[order_id].updated_at = datetime.now(timezone.utc)
            self._active_orders.clear()

        return count

    def amend_order(
        self,
        order_id: str,
        new_price: Optional[float] = None,
        new_size: Optional[float] = None,
    ) -> Optional[str]:
        """
        Amend an existing order (cancel + replace).

        Args:
            order_id: Order to amend
            new_price: New price (or keep existing)
            new_size: New size (or keep existing)

        Returns:
            New order ID if successful
        """
        with self._lock:
            if order_id not in self._orders:
                return None
            old_order = self._orders[order_id]

        # Cancel old order
        if not self.cancel_order(order_id):
            return None

        # Place new order
        price = new_price if new_price is not None else old_order.price
        size = new_size if new_size is not None else old_order.size

        return self.place_order(
            token_id=old_order.token_id,
            side=old_order.side,
            price=price,
            size=size,
        )

    def update_quotes(self, quotes: QuoteSet) -> Dict[str, List[str]]:
        """
        Update orders to match new quotes.

        Cancels orders that don't match quotes and places new ones.

        Args:
            quotes: New quote set to implement

        Returns:
            Dict with 'placed' and 'cancelled' order IDs
        """
        result = {"placed": [], "cancelled": []}

        # Cancel all existing orders first (simple approach)
        for order in self.active_orders:
            if self.cancel_order(order.order_id):
                result["cancelled"].append(order.order_id)

        # Place YES bids
        for quote in quotes.yes_bids:
            order_id = self.place_order(
                token_id=self.yes_token_id,
                side=OrderSide.BUY,
                price=quote.price,
                size=quote.size,
            )
            if order_id:
                result["placed"].append(order_id)

        # Place YES asks
        for quote in quotes.yes_asks:
            order_id = self.place_order(
                token_id=self.yes_token_id,
                side=OrderSide.SELL,
                price=quote.price,
                size=quote.size,
            )
            if order_id:
                result["placed"].append(order_id)

        # Place NO bids
        for quote in quotes.no_bids:
            order_id = self.place_order(
                token_id=self.no_token_id,
                side=OrderSide.BUY,
                price=quote.price,
                size=quote.size,
            )
            if order_id:
                result["placed"].append(order_id)

        # Place NO asks
        for quote in quotes.no_asks:
            order_id = self.place_order(
                token_id=self.no_token_id,
                side=OrderSide.SELL,
                price=quote.price,
                size=quote.size,
            )
            if order_id:
                result["placed"].append(order_id)

        logger.info(
            "Quotes updated",
            placed=len(result["placed"]),
            cancelled=len(result["cancelled"]),
        )

        return result

    def on_fill(
        self,
        order_id: str,
        fill_size: float,
        fill_price: float,
    ):
        """
        Handle an order fill event.

        Args:
            order_id: Order that was filled
            fill_size: Size filled
            fill_price: Price of fill
        """
        with self._lock:
            if order_id not in self._orders:
                logger.warning("Fill for unknown order", order_id=order_id)
                return

            order = self._orders[order_id]
            order.filled_size += fill_size
            order.updated_at = datetime.now(timezone.utc)

            if order.filled_size >= order.size:
                order.status = OrderStatus.FILLED
                self._active_orders.discard(order_id)
            else:
                order.status = OrderStatus.PARTIALLY_FILLED

        logger.info(
            "Order fill processed",
            order_id=order_id,
            fill_size=fill_size,
            fill_price=fill_price,
            total_filled=order.filled_size,
        )

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID."""
        with self._lock:
            return self._orders.get(order_id)

    def sync_with_exchange(self):
        """
        Sync local order state with exchange.

        Fetches open orders from exchange and reconciles.
        """
        try:
            exchange_orders = self.clob.get_open_orders()

            exchange_ids = set()
            for order_data in exchange_orders:
                order_id = order_data.get("id") or order_data.get("orderID")
                if order_id:
                    exchange_ids.add(order_id)

            # Mark orders not on exchange as cancelled
            with self._lock:
                for order_id in list(self._active_orders):
                    if order_id not in exchange_ids:
                        if order_id in self._orders:
                            self._orders[order_id].status = OrderStatus.CANCELLED
                        self._active_orders.discard(order_id)

            logger.info(
                "Synced with exchange",
                local_active=len(self._active_orders),
                exchange_open=len(exchange_ids),
            )

        except Exception as e:
            logger.error("Failed to sync with exchange", error=str(e))

    def reset(self):
        """Reset order tracking (e.g., for new market)."""
        self.cancel_all_orders()
        with self._lock:
            self._orders.clear()
            self._active_orders.clear()
