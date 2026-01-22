"""
Order management for sports markets.

Uses the official py-clob-client for all order operations.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
import structlog

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

logger = structlog.get_logger()


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    token_id: str
    market_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    filled_size: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def remaining_size(self) -> float:
        """Size remaining to be filled."""
        return max(0, self.size - self.filled_size)

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]

    def update_fill(self, filled: float):
        """Update order with fill."""
        self.filled_size += filled
        self.updated_at = datetime.now()

        if self.filled_size >= self.size:
            self.status = OrderStatus.FILLED
        elif self.filled_size > 0:
            self.status = OrderStatus.PARTIALLY_FILLED


@dataclass
class Fill:
    """Represents a fill event."""
    fill_id: str
    order_id: str
    token_id: str
    market_id: str
    side: str
    price: float
    size: float
    timestamp: datetime = field(default_factory=datetime.now)


class OrderManager:
    """
    Manages orders on Polymarket CLOB.

    Uses official py-clob-client for all operations.
    """

    def __init__(
        self,
        client: ClobClient,
        paper_trading: bool = True,
    ):
        """
        Initialize order manager.

        Args:
            client: Authenticated ClobClient
            paper_trading: If True, simulates orders without sending to exchange
        """
        self.client = client
        self.paper_trading = paper_trading
        self.orders: Dict[str, Order] = {}
        self.fills: List[Fill] = []
        self._paper_order_counter = 0

    async def place_order(
        self,
        token_id: str,
        market_id: str,
        side: str,
        price: float,
        size: float,
    ) -> Optional[str]:
        """
        Place a limit order.

        Args:
            token_id: Token ID to trade
            market_id: Market ID for tracking
            side: "BUY" or "SELL"
            price: Limit price (0.01 to 0.99)
            size: Size in shares

        Returns:
            Order ID if successful, None otherwise
        """
        # Validate inputs
        if price < 0.01 or price > 0.99:
            logger.error("invalid_price", price=price)
            return None

        if size <= 0:
            logger.error("invalid_size", size=size)
            return None

        if self.paper_trading:
            return await self._place_paper_order(token_id, market_id, side, price, size)

        return await self._place_live_order(token_id, market_id, side, price, size)

    async def _place_paper_order(
        self,
        token_id: str,
        market_id: str,
        side: str,
        price: float,
        size: float,
    ) -> str:
        """Place a simulated paper order."""
        self._paper_order_counter += 1
        order_id = f"PAPER_{self._paper_order_counter}_{token_id[:8]}_{side}"

        order = Order(
            order_id=order_id,
            token_id=token_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.OPEN,
        )

        self.orders[order_id] = order

        logger.info(
            "paper_order_placed",
            order_id=order_id,
            side=side,
            price=f"{price:.3f}",
            size=f"{size:.2f}",
        )

        return order_id

    async def _place_live_order(
        self,
        token_id: str,
        market_id: str,
        side: str,
        price: float,
        size: float,
    ) -> Optional[str]:
        """Place a live order on the exchange."""
        try:
            # Create order args
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == "BUY" else SELL,
            )

            # Sign and submit order
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            order_id = resp.get("orderID")
            if not order_id:
                logger.error("order_placement_failed", response=resp)
                return None

            # Track order locally
            order = Order(
                order_id=order_id,
                token_id=token_id,
                market_id=market_id,
                side=side,
                price=price,
                size=size,
                status=OrderStatus.OPEN,
            )

            self.orders[order_id] = order

            logger.info(
                "order_placed",
                order_id=order_id,
                side=side,
                price=f"{price:.3f}",
                size=f"{size:.2f}",
            )

            return order_id

        except Exception as e:
            logger.error("order_placement_error", error=str(e))
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if successful
        """
        if order_id not in self.orders:
            logger.warning("order_not_found", order_id=order_id)
            return False

        if self.paper_trading:
            self.orders[order_id].status = OrderStatus.CANCELLED
            logger.info("paper_order_cancelled", order_id=order_id)
            return True

        try:
            self.client.cancel(order_id)
            self.orders[order_id].status = OrderStatus.CANCELLED
            logger.info("order_cancelled", order_id=order_id)
            return True

        except Exception as e:
            logger.error("cancel_error", order_id=order_id, error=str(e))
            return False

    async def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        if self.paper_trading:
            count = 0
            for order in self.orders.values():
                if order.is_active:
                    order.status = OrderStatus.CANCELLED
                    count += 1
            logger.info("paper_orders_cancelled", count=count)
            return count

        try:
            self.client.cancel_all()

            count = 0
            for order in self.orders.values():
                if order.is_active:
                    order.status = OrderStatus.CANCELLED
                    count += 1

            logger.info("orders_cancelled", count=count)
            return count

        except Exception as e:
            logger.error("cancel_all_error", error=str(e))
            return 0

    async def cancel_market_orders(self, market_id: str) -> int:
        """
        Cancel all orders for a specific market.

        Args:
            market_id: Market ID to cancel orders for

        Returns:
            Number of orders cancelled
        """
        count = 0
        for order in list(self.orders.values()):
            if order.market_id == market_id and order.is_active:
                if await self.cancel_order(order.order_id):
                    count += 1

        return count

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """
        Get all open orders.

        Args:
            market_id: Optional market ID to filter by

        Returns:
            List of open orders
        """
        orders = [o for o in self.orders.values() if o.is_active]

        if market_id:
            orders = [o for o in orders if o.market_id == market_id]

        return orders

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID."""
        return self.orders.get(order_id)

    def record_fill(
        self,
        order_id: str,
        fill_size: float,
        fill_price: float,
    ) -> Optional[Fill]:
        """
        Record a fill event.

        Args:
            order_id: Order that was filled
            fill_size: Size filled
            fill_price: Price filled at

        Returns:
            Fill object if successful
        """
        order = self.orders.get(order_id)
        if not order:
            logger.warning("fill_for_unknown_order", order_id=order_id)
            return None

        # Update order
        order.update_fill(fill_size)

        # Record fill
        fill = Fill(
            fill_id=f"FILL_{len(self.fills) + 1}",
            order_id=order_id,
            token_id=order.token_id,
            market_id=order.market_id,
            side=order.side,
            price=fill_price,
            size=fill_size,
        )

        self.fills.append(fill)

        logger.info(
            "fill_recorded",
            order_id=order_id,
            side=order.side,
            price=f"{fill_price:.3f}",
            size=f"{fill_size:.2f}",
            remaining=f"{order.remaining_size:.2f}",
        )

        return fill

    def get_fills(
        self,
        market_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> List[Fill]:
        """
        Get fills with optional filtering.

        Args:
            market_id: Filter by market ID
            since: Only fills after this time

        Returns:
            List of fills
        """
        fills = self.fills

        if market_id:
            fills = [f for f in fills if f.market_id == market_id]

        if since:
            fills = [f for f in fills if f.timestamp >= since]

        return fills

    def get_stats(self) -> Dict:
        """Get order statistics."""
        total_orders = len(self.orders)
        open_orders = len([o for o in self.orders.values() if o.is_active])
        filled_orders = len([o for o in self.orders.values() if o.status == OrderStatus.FILLED])
        cancelled_orders = len([o for o in self.orders.values() if o.status == OrderStatus.CANCELLED])
        total_fills = len(self.fills)

        return {
            "total_orders": total_orders,
            "open_orders": open_orders,
            "filled_orders": filled_orders,
            "cancelled_orders": cancelled_orders,
            "total_fills": total_fills,
        }
