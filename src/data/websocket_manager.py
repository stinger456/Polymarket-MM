"""
WebSocket connection management for Polymarket.

Handles market data and user order update streams.
"""

import json
import asyncio
import threading
from typing import Optional, List, Callable, Any
from dataclasses import dataclass
from enum import Enum
import websocket
import structlog

from .orderbook import OrderbookManager


logger = structlog.get_logger(__name__)


class ChannelType(Enum):
    """WebSocket channel types."""
    MARKET = "market"
    USER = "user"


@dataclass
class WebSocketConfig:
    """WebSocket connection configuration."""
    market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    ping_interval: float = 30.0


class MarketWebSocket:
    """
    WebSocket client for market data (orderbook updates).

    Subscribes to orderbook updates for specified tokens.
    """

    def __init__(
        self,
        orderbook_manager: OrderbookManager,
        config: Optional[WebSocketConfig] = None,
    ):
        self.orderbook_manager = orderbook_manager
        self.config = config or WebSocketConfig()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._subscribed_tokens: List[str] = []
        self._callbacks: List[Callable[[dict], None]] = []
        self._reconnect_delay = self.config.reconnect_delay

    def add_callback(self, callback: Callable[[dict], None]):
        """Add callback for message events."""
        self._callbacks.append(callback)

    def _on_open(self, ws):
        """Handle WebSocket connection open."""
        logger.info("Market WebSocket connected")
        self._reconnect_delay = self.config.reconnect_delay

        # Subscribe to tokens
        if self._subscribed_tokens:
            self._subscribe(self._subscribed_tokens)

    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            self._process_message(data)

            # Call registered callbacks
            for callback in self._callbacks:
                try:
                    callback(data)
                except Exception as e:
                    logger.error("Callback error", error=str(e))

        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON message", error=str(e))

    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        logger.error("Market WebSocket error", error=str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close."""
        logger.warning(
            "Market WebSocket closed",
            code=close_status_code,
            message=close_msg,
        )

        # Attempt reconnection
        if self._running:
            self._schedule_reconnect()

    def _process_message(self, data: dict):
        """Process orderbook update message."""
        event_type = data.get("event_type")
        asset_id = data.get("asset_id")

        if not asset_id:
            return

        if event_type == "book":
            # Full orderbook snapshot
            bids = [
                (float(b.get("price", 0)), float(b.get("size", 0)))
                for b in data.get("bids", [])
            ]
            asks = [
                (float(a.get("price", 0)), float(a.get("size", 0)))
                for a in data.get("asks", [])
            ]
            self.orderbook_manager.update_snapshot(
                asset_id, bids, asks,
                sequence=data.get("timestamp", 0),
            )

        elif event_type == "price_change":
            # Price level update
            changes = data.get("changes", [])
            for change in changes:
                side = "bid" if change.get("side") == "BUY" else "ask"
                price = float(change.get("price", 0))
                size = float(change.get("size", 0))
                self.orderbook_manager.apply_delta(
                    asset_id, side, price, size,
                )

        elif event_type == "tick_size_change":
            logger.info("Tick size changed", asset_id=asset_id, data=data)

    def _subscribe(self, token_ids: List[str]):
        """Send subscription message."""
        if self._ws:
            msg = {
                "assets_ids": token_ids,
                "type": ChannelType.MARKET.value,
            }
            self._ws.send(json.dumps(msg))
            logger.info("Subscribed to tokens", count=len(token_ids))

    def _schedule_reconnect(self):
        """Schedule reconnection with exponential backoff."""
        if not self._running:
            return

        logger.info(
            "Scheduling reconnection",
            delay=self._reconnect_delay,
        )

        def reconnect():
            import time
            time.sleep(self._reconnect_delay)
            if self._running:
                self._connect()

        thread = threading.Thread(target=reconnect, daemon=True)
        thread.start()

        # Increase delay for next attempt
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self.config.max_reconnect_delay,
        )

    def _connect(self):
        """Establish WebSocket connection."""
        self._ws = websocket.WebSocketApp(
            self.config.market_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        # Run in thread
        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(
                ping_interval=self.config.ping_interval,
            ),
            daemon=True,
        )
        self._thread.start()

    def subscribe(self, token_ids: List[str]):
        """
        Subscribe to orderbook updates for tokens.

        Args:
            token_ids: List of token IDs to subscribe to
        """
        self._subscribed_tokens = token_ids

        if self._ws and self._ws.sock and self._ws.sock.connected:
            self._subscribe(token_ids)

    def start(self, token_ids: Optional[List[str]] = None):
        """
        Start WebSocket connection.

        Args:
            token_ids: Optional list of tokens to subscribe to
        """
        if token_ids:
            self._subscribed_tokens = token_ids

        self._running = True
        self._connect()

    def stop(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()


class UserWebSocket:
    """
    WebSocket client for user order updates.

    Requires authentication. Receives:
    - Order placements
    - Order matches (fills)
    - Order cancellations
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        config: Optional[WebSocketConfig] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.config = config or WebSocketConfig()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._market_ids: List[str] = []
        self._callbacks: List[Callable[[dict], None]] = []
        self._reconnect_delay = self.config.reconnect_delay

    def add_callback(self, callback: Callable[[dict], None]):
        """Add callback for order update events."""
        self._callbacks.append(callback)

    def _on_open(self, ws):
        """Handle WebSocket connection open."""
        logger.info("User WebSocket connected")
        self._reconnect_delay = self.config.reconnect_delay

        # Authenticate
        auth_msg = {
            "type": ChannelType.USER.value,
            "auth": {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.api_passphrase,
            },
        }
        if self._market_ids:
            auth_msg["markets"] = self._market_ids

        ws.send(json.dumps(auth_msg))

    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            self._process_message(data)

            for callback in self._callbacks:
                try:
                    callback(data)
                except Exception as e:
                    logger.error("User callback error", error=str(e))

        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON message", error=str(e))

    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        logger.error("User WebSocket error", error=str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close."""
        logger.warning(
            "User WebSocket closed",
            code=close_status_code,
            message=close_msg,
        )

        if self._running:
            self._schedule_reconnect()

    def _process_message(self, data: dict):
        """Process user order update message."""
        event_type = data.get("event_type") or data.get("type")

        if event_type == "PLACEMENT":
            logger.info("Order placed", order_id=data.get("order_id"))

        elif event_type == "MATCHED":
            logger.info(
                "Order matched",
                order_id=data.get("order_id"),
                fill_size=data.get("match_size"),
                fill_price=data.get("match_price"),
            )

        elif event_type == "CANCELLED":
            logger.info("Order cancelled", order_id=data.get("order_id"))

        elif event_type == "MINED":
            logger.debug("Trade mined", tx_hash=data.get("tx_hash"))

        elif event_type == "CONFIRMED":
            logger.debug("Trade confirmed", tx_hash=data.get("tx_hash"))

    def _schedule_reconnect(self):
        """Schedule reconnection with exponential backoff."""
        if not self._running:
            return

        def reconnect():
            import time
            time.sleep(self._reconnect_delay)
            if self._running:
                self._connect()

        thread = threading.Thread(target=reconnect, daemon=True)
        thread.start()

        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self.config.max_reconnect_delay,
        )

    def _connect(self):
        """Establish WebSocket connection."""
        self._ws = websocket.WebSocketApp(
            self.config.user_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(
                ping_interval=self.config.ping_interval,
            ),
            daemon=True,
        )
        self._thread.start()

    def subscribe_markets(self, market_ids: List[str]):
        """Set markets to filter order updates."""
        self._market_ids = market_ids

    def start(self, market_ids: Optional[List[str]] = None):
        """Start WebSocket connection."""
        if market_ids:
            self._market_ids = market_ids

        self._running = True
        self._connect()

    def stop(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()


class WebSocketManager:
    """
    Manages all WebSocket connections.

    Provides unified interface for market and user data streams.
    """

    def __init__(
        self,
        orderbook_manager: OrderbookManager,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        config: Optional[WebSocketConfig] = None,
    ):
        self.orderbook_manager = orderbook_manager
        self.config = config or WebSocketConfig()

        self.market_ws = MarketWebSocket(orderbook_manager, config)

        self.user_ws: Optional[UserWebSocket] = None
        if api_key and api_secret and api_passphrase:
            self.user_ws = UserWebSocket(
                api_key, api_secret, api_passphrase, config,
            )

    def start_market_stream(self, token_ids: List[str]):
        """Start market data stream for tokens."""
        self.market_ws.start(token_ids)

    def start_user_stream(self, market_ids: Optional[List[str]] = None):
        """Start user order update stream."""
        if self.user_ws:
            self.user_ws.start(market_ids)
        else:
            logger.warning("User WebSocket not configured")

    def stop(self):
        """Stop all WebSocket connections."""
        self.market_ws.stop()
        if self.user_ws:
            self.user_ws.stop()

    def add_market_callback(self, callback: Callable[[dict], None]):
        """Add callback for market data updates."""
        self.market_ws.add_callback(callback)

    def add_user_callback(self, callback: Callable[[dict], None]):
        """Add callback for user order updates."""
        if self.user_ws:
            self.user_ws.add_callback(callback)
