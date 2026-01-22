"""
WebSocket orderbook tracking for sports markets.

Maintains real-time orderbook state for subscribed tokens.
"""
import json
import threading
from typing import Dict, Callable, Optional, List
from dataclasses import dataclass, field
import structlog

try:
    from websocket import WebSocketApp
except ImportError:
    WebSocketApp = None

logger = structlog.get_logger()


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class Orderbook:
    """Local orderbook for a single token."""

    token_id: str
    bids: List[Dict] = field(default_factory=list)  # [{"price": "0.50", "size": "100"}, ...]
    asks: List[Dict] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        """Get best bid price."""
        if self.bids:
            return float(self.bids[0].get("price", 0))
        return None

    @property
    def best_ask(self) -> Optional[float]:
        """Get best ask price."""
        if self.asks:
            return float(self.asks[0].get("price", 0))
        return None

    @property
    def best_bid_size(self) -> Optional[float]:
        """Get size at best bid."""
        if self.bids:
            return float(self.bids[0].get("size", 0))
        return None

    @property
    def best_ask_size(self) -> Optional[float]:
        """Get size at best ask."""
        if self.asks:
            return float(self.asks[0].get("size", 0))
        return None

    @property
    def mid(self) -> Optional[float]:
        """Get mid price."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        """Get spread (ask - bid)."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        """Get spread as percentage of mid."""
        if self.spread is not None and self.mid is not None and self.mid > 0:
            return self.spread / self.mid
        return None

    def get_depth(self, side: str, levels: int = 5) -> List[tuple]:
        """Get depth at N levels."""
        book = self.bids if side.lower() == "bid" else self.asks
        result = []
        for level in book[:levels]:
            result.append((
                float(level.get("price", 0)),
                float(level.get("size", 0))
            ))
        return result


class OrderbookManager:
    """
    Manages WebSocket connections for real-time orderbook updates.

    Subscribes to multiple tokens and maintains local orderbook state.
    """

    def __init__(self, on_update: Optional[Callable] = None):
        """
        Initialize orderbook manager.

        Args:
            on_update: Callback function called on each orderbook update.
                       Signature: on_update(token_id: str, book: Orderbook)
        """
        self.orderbooks: Dict[str, Orderbook] = {}
        self.on_update = on_update
        self._ws: Optional[WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._subscribed_tokens: List[str] = []
        self._connected = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5

    def subscribe(self, token_ids: List[str]) -> bool:
        """
        Subscribe to orderbook updates for tokens.

        Args:
            token_ids: List of token IDs to subscribe to

        Returns:
            True if subscription started successfully
        """
        if WebSocketApp is None:
            logger.error("websocket_not_installed", message="pip install websocket-client")
            return False

        self._subscribed_tokens = token_ids

        for token_id in token_ids:
            self.orderbooks[token_id] = Orderbook(token_id=token_id)

        logger.info("subscribing_to_orderbooks", count=len(token_ids))
        return self._connect()

    def _connect(self) -> bool:
        """Connect to WebSocket."""
        try:
            self._ws = WebSocketApp(
                WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            self._thread = threading.Thread(target=self._ws.run_forever)
            self._thread.daemon = True
            self._thread.start()
            return True

        except Exception as e:
            logger.error("websocket_connect_error", error=str(e))
            return False

    def _on_open(self, ws):
        """Send subscription message on connection."""
        self._connected = True
        self._reconnect_attempts = 0

        # Subscribe to all tokens
        msg = {
            "assets_ids": self._subscribed_tokens,
            "type": "market"
        }
        ws.send(json.dumps(msg))
        logger.info("orderbook_subscribed", tokens=len(self._subscribed_tokens))

    def _on_message(self, ws, message):
        """Handle incoming orderbook updates."""
        try:
            data = json.loads(message)
            event_type = data.get("event_type")
            asset_id = data.get("asset_id")

            if asset_id not in self.orderbooks:
                return

            book = self.orderbooks[asset_id]

            if event_type == "book":
                # Full book snapshot
                book.bids = data.get("bids", [])
                book.asks = data.get("asks", [])
                book.timestamp = data.get("timestamp", 0)

            elif event_type == "price_change":
                # Incremental update
                changes = data.get("changes", [])
                for change in changes:
                    side = change.get("side")
                    price = change.get("price")
                    size = change.get("size")

                    if side == "BUY":
                        self._update_level(book.bids, price, size)
                    elif side == "SELL":
                        self._update_level(book.asks, price, size)

            if self.on_update:
                self.on_update(asset_id, book)

        except Exception as e:
            logger.error("orderbook_message_error", error=str(e))

    def _update_level(self, levels: List[Dict], price: str, size: str):
        """Update a single level in the book."""
        size_float = float(size)

        # Find existing level
        for i, level in enumerate(levels):
            if level.get("price") == price:
                if size_float == 0:
                    # Remove level
                    levels.pop(i)
                else:
                    # Update level
                    level["size"] = size
                return

        # Add new level if size > 0
        if size_float > 0:
            levels.append({"price": price, "size": size})
            # Re-sort (bids descending, asks ascending)
            levels.sort(key=lambda x: float(x.get("price", 0)), reverse=True)

    def _on_error(self, ws, error):
        """Handle WebSocket errors."""
        logger.error("orderbook_ws_error", error=str(error))

    def _on_close(self, ws, code, reason):
        """Handle WebSocket close."""
        self._connected = False
        logger.info("orderbook_ws_closed", code=code, reason=reason)

        # Attempt reconnection
        if self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            logger.info(
                "orderbook_reconnecting",
                attempt=self._reconnect_attempts,
                max_attempts=self._max_reconnect_attempts
            )
            import time
            time.sleep(2 ** self._reconnect_attempts)  # Exponential backoff
            self._connect()

    def get_orderbook(self, token_id: str) -> Optional[Orderbook]:
        """Get orderbook for a token."""
        return self.orderbooks.get(token_id)

    def get_all_orderbooks(self) -> Dict[str, Orderbook]:
        """Get all orderbooks."""
        return self.orderbooks.copy()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    def close(self):
        """Close WebSocket connection."""
        if self._ws:
            self._ws.close()
            self._connected = False
            logger.info("orderbook_manager_closed")


def create_mock_orderbook(
    token_id: str,
    mid_price: float = 0.5,
    spread: float = 0.02,
    depth: int = 5,
    size_per_level: float = 100.0,
) -> Orderbook:
    """
    Create a mock orderbook for testing.

    Args:
        token_id: Token ID
        mid_price: Mid price
        spread: Spread around mid
        depth: Number of levels per side
        size_per_level: Size at each level

    Returns:
        Mock Orderbook object
    """
    half_spread = spread / 2
    level_spacing = 0.01

    bids = []
    asks = []

    for i in range(depth):
        bid_price = mid_price - half_spread - (i * level_spacing)
        ask_price = mid_price + half_spread + (i * level_spacing)

        bids.append({
            "price": f"{bid_price:.4f}",
            "size": f"{size_per_level:.2f}"
        })
        asks.append({
            "price": f"{ask_price:.4f}",
            "size": f"{size_per_level:.2f}"
        })

    return Orderbook(
        token_id=token_id,
        bids=bids,
        asks=asks,
    )
