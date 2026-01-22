"""
Tests for order manager.
"""

import pytest
from unittest.mock import Mock, MagicMock
from src.execution.order_manager import OrderManager, OrderSide, OrderStatus
from src.execution.clob_client import PolymarketCLOB, OrderResponse
from src.quoting.quote_engine import QuoteSet


class TestOrderManager:
    """Tests for OrderManager."""

    def setup_method(self):
        """Setup test fixtures."""
        # Create mock CLOB client
        self.mock_clob = Mock(spec=PolymarketCLOB)
        self.mock_clob.place_limit_order.return_value = OrderResponse(
            order_id="test_order_123",
            success=True,
        )
        self.mock_clob.cancel_order.return_value = True
        self.mock_clob.cancel_all_orders.return_value = 5

        self.order_manager = OrderManager(
            clob_client=self.mock_clob,
            yes_token_id="yes_token",
            no_token_id="no_token",
        )

    def test_place_order(self):
        """Test placing an order."""
        order_id = self.order_manager.place_order(
            token_id="yes_token",
            side=OrderSide.BUY,
            price=0.50,
            size=100,
        )

        assert order_id == "test_order_123"
        assert len(self.order_manager.active_orders) == 1

    def test_cancel_order(self):
        """Test cancelling an order."""
        # Place order first
        order_id = self.order_manager.place_order(
            token_id="yes_token",
            side=OrderSide.BUY,
            price=0.50,
            size=100,
        )

        # Cancel it
        success = self.order_manager.cancel_order(order_id)

        assert success
        assert len(self.order_manager.active_orders) == 0

        order = self.order_manager.get_order(order_id)
        assert order.status == OrderStatus.CANCELLED

    def test_cancel_all_orders(self):
        """Test cancelling all orders."""
        # Place multiple orders
        self.order_manager.place_order("yes_token", OrderSide.BUY, 0.50, 100)
        self.order_manager.place_order("no_token", OrderSide.BUY, 0.45, 100)

        count = self.order_manager.cancel_all_orders()

        assert count == 5  # Mock returns 5
        assert len(self.order_manager.active_orders) == 0

    def test_update_quotes(self):
        """Test updating quotes."""
        quotes = QuoteSet()
        quotes.add_yes_bid(0.48, 100)
        quotes.add_yes_ask(0.52, 100)
        quotes.add_no_bid(0.47, 100)
        quotes.add_no_ask(0.53, 100)

        result = self.order_manager.update_quotes(quotes)

        # Should place 4 orders
        assert len(result["placed"]) == 4

    def test_on_fill(self):
        """Test handling fill events."""
        # Place order
        order_id = self.order_manager.place_order(
            token_id="yes_token",
            side=OrderSide.BUY,
            price=0.50,
            size=100,
        )

        # Partial fill
        self.order_manager.on_fill(order_id, fill_size=50, fill_price=0.50)

        order = self.order_manager.get_order(order_id)
        assert order.filled_size == 50
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order_id in [o.order_id for o in self.order_manager.active_orders]

        # Complete fill
        self.order_manager.on_fill(order_id, fill_size=50, fill_price=0.50)

        order = self.order_manager.get_order(order_id)
        assert order.filled_size == 100
        assert order.status == OrderStatus.FILLED
        assert order_id not in [o.order_id for o in self.order_manager.active_orders]

    def test_yes_orders(self):
        """Test filtering YES orders."""
        self.order_manager.place_order("yes_token", OrderSide.BUY, 0.50, 100)
        self.order_manager.place_order("no_token", OrderSide.BUY, 0.45, 100)

        yes_orders = self.order_manager.yes_orders
        assert len(yes_orders) == 1
        assert yes_orders[0].token_id == "yes_token"

    def test_no_orders(self):
        """Test filtering NO orders."""
        self.order_manager.place_order("yes_token", OrderSide.BUY, 0.50, 100)
        self.order_manager.place_order("no_token", OrderSide.BUY, 0.45, 100)

        no_orders = self.order_manager.no_orders
        assert len(no_orders) == 1
        assert no_orders[0].token_id == "no_token"

    def test_set_market(self):
        """Test setting market tokens."""
        self.order_manager.set_market("new_yes", "new_no")

        assert self.order_manager.yes_token_id == "new_yes"
        assert self.order_manager.no_token_id == "new_no"

    def test_reset(self):
        """Test resetting order manager."""
        self.order_manager.place_order("yes_token", OrderSide.BUY, 0.50, 100)
        self.order_manager.reset()

        assert len(self.order_manager.active_orders) == 0

    def test_failed_order_placement(self):
        """Test handling failed order placement."""
        self.mock_clob.place_limit_order.return_value = OrderResponse(
            order_id=None,
            success=False,
            error_message="Insufficient funds",
        )

        order_id = self.order_manager.place_order(
            token_id="yes_token",
            side=OrderSide.BUY,
            price=0.50,
            size=100,
        )

        assert order_id is None
        assert len(self.order_manager.active_orders) == 0
