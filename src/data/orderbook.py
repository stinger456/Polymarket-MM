"""
Local orderbook tracking for market data.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from collections import defaultdict
import threading
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class OrderbookLevel:
    """Single price level in the orderbook."""
    price: float
    size: float


@dataclass
class Orderbook:
    """
    Local orderbook for a single token.

    Maintains sorted bids (descending) and asks (ascending).
    """
    token_id: str
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    last_update: Optional[datetime] = None
    sequence: int = 0

    @property
    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price."""
        return self.asks[0].price if self.asks else None

    @property
    def best_bid_size(self) -> float:
        """Size at best bid."""
        return self.bids[0].size if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        """Size at best ask."""
        return self.asks[0].size if self.asks else 0.0

    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid-market price."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def total_bid_depth(self) -> float:
        """Total size on bid side."""
        return sum(level.size for level in self.bids)

    @property
    def total_ask_depth(self) -> float:
        """Total size on ask side."""
        return sum(level.size for level in self.asks)

    def update_from_snapshot(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        sequence: int = 0,
    ):
        """
        Update orderbook from a full snapshot.

        Args:
            bids: List of (price, size) tuples
            asks: List of (price, size) tuples
            sequence: Sequence number
        """
        self.bids = [
            OrderbookLevel(price=float(p), size=float(s))
            for p, s in bids if float(s) > 0
        ]
        self.asks = [
            OrderbookLevel(price=float(p), size=float(s))
            for p, s in asks if float(s) > 0
        ]

        # Sort bids descending (highest first)
        self.bids.sort(key=lambda x: x.price, reverse=True)
        # Sort asks ascending (lowest first)
        self.asks.sort(key=lambda x: x.price)

        self.sequence = sequence
        self.last_update = datetime.now(timezone.utc)

    def apply_delta(
        self,
        side: str,
        price: float,
        size: float,
        sequence: int = 0,
    ):
        """
        Apply an incremental update to the orderbook.

        Args:
            side: "bid" or "ask"
            price: Price level
            size: New size (0 to remove)
            sequence: Sequence number
        """
        if sequence and sequence <= self.sequence:
            logger.warning(
                "Stale orderbook update",
                token_id=self.token_id,
                expected_seq=self.sequence + 1,
                received_seq=sequence,
            )
            return

        levels = self.bids if side == "bid" else self.asks

        # Find or insert the price level
        found = False
        for i, level in enumerate(levels):
            if abs(level.price - price) < 0.0001:
                if size > 0:
                    level.size = size
                else:
                    levels.pop(i)
                found = True
                break

        if not found and size > 0:
            levels.append(OrderbookLevel(price=price, size=size))

        # Re-sort
        if side == "bid":
            self.bids.sort(key=lambda x: x.price, reverse=True)
        else:
            self.asks.sort(key=lambda x: x.price)

        if sequence:
            self.sequence = sequence
        self.last_update = datetime.now(timezone.utc)

    def get_price_for_size(self, side: str, size: float) -> Optional[float]:
        """
        Get the average price to fill a given size.

        Walks the orderbook to calculate VWAP for the order.
        """
        levels = self.asks if side == "buy" else self.bids

        remaining = size
        total_value = 0.0

        for level in levels:
            fill_size = min(remaining, level.size)
            total_value += fill_size * level.price
            remaining -= fill_size
            if remaining <= 0:
                break

        if remaining > 0:
            return None  # Not enough liquidity

        return total_value / size

    def is_stale(self, max_age_seconds: float = 10.0) -> bool:
        """Check if orderbook data is stale."""
        if self.last_update is None:
            return True
        age = (datetime.now(timezone.utc) - self.last_update).total_seconds()
        return age > max_age_seconds


class OrderbookManager:
    """
    Manages orderbooks for multiple tokens.

    Thread-safe for use with WebSocket callbacks.
    """

    def __init__(self):
        self._orderbooks: dict[str, Orderbook] = {}
        self._lock = threading.Lock()

    def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get orderbook for a token."""
        with self._lock:
            return self._orderbooks.get(token_id)

    def get_or_create_orderbook(self, token_id: str) -> Orderbook:
        """Get or create orderbook for a token."""
        with self._lock:
            if token_id not in self._orderbooks:
                self._orderbooks[token_id] = Orderbook(token_id=token_id)
            return self._orderbooks[token_id]

    def update_snapshot(
        self,
        token_id: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        sequence: int = 0,
    ):
        """Update orderbook from snapshot."""
        with self._lock:
            orderbook = self.get_or_create_orderbook(token_id)
            orderbook.update_from_snapshot(bids, asks, sequence)

    def apply_delta(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        sequence: int = 0,
    ):
        """Apply incremental update to orderbook."""
        with self._lock:
            orderbook = self.get_or_create_orderbook(token_id)
            orderbook.apply_delta(side, price, size, sequence)

    def get_best_prices(
        self,
        yes_token_id: str,
        no_token_id: str,
    ) -> dict:
        """
        Get best bid/ask for both YES and NO tokens.

        Returns dict with yes_bid, yes_ask, no_bid, no_ask.
        """
        with self._lock:
            yes_book = self._orderbooks.get(yes_token_id)
            no_book = self._orderbooks.get(no_token_id)

            return {
                "yes_bid": yes_book.best_bid if yes_book else None,
                "yes_ask": yes_book.best_ask if yes_book else None,
                "no_bid": no_book.best_bid if no_book else None,
                "no_ask": no_book.best_ask if no_book else None,
            }

    def remove_orderbook(self, token_id: str):
        """Remove orderbook for a token."""
        with self._lock:
            self._orderbooks.pop(token_id, None)

    def clear(self):
        """Clear all orderbooks."""
        with self._lock:
            self._orderbooks.clear()
