"""
Real-time price feed from Binance WebSocket.
"""

import json
import threading
from typing import Optional, Callable
from datetime import datetime, timezone
import websocket
import structlog

from .volatility import VolatilityCalculator, VolatilityMetrics


logger = structlog.get_logger(__name__)


class BinancePriceFeed:
    """
    Real-time BTC price feed from Binance WebSocket.

    Calculates:
    - Current price
    - Rolling volatility
    - Price momentum
    """

    BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(
        self,
        symbol: str = "btcusdt",
        volatility_window: int = 100,
        volatility_scale: float = 1.0,
    ):
        """
        Initialize price feed.

        Args:
            symbol: Trading pair symbol (lowercase)
            volatility_window: Number of price samples for volatility
            volatility_scale: Multiplier for volatility calculation
        """
        self.symbol = symbol.lower()
        self.volatility_calculator = VolatilityCalculator(
            window_size=volatility_window,
            scale=volatility_scale,
        )

        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: list[Callable[[float], None]] = []
        self._reconnect_delay = 1.0

        self._current_price: Optional[float] = None
        self._last_update: Optional[datetime] = None

    @property
    def current_price(self) -> Optional[float]:
        """Get the current BTC price."""
        return self._current_price

    @property
    def volatility(self) -> Optional[float]:
        """Get current hourly volatility."""
        return self.volatility_calculator.calculate_hourly_vol()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return (
            self._ws is not None
            and self._ws.sock is not None
            and self._ws.sock.connected
        )

    def add_callback(self, callback: Callable[[float], None]):
        """Add callback for price updates."""
        self._callbacks.append(callback)

    def get_volatility_metrics(self) -> Optional[VolatilityMetrics]:
        """
        Get current volatility metrics.

        Returns metrics including:
        - rolling_std: Standard deviation of recent prices
        - realized_vol: Annualized volatility
        - current_price: Latest BTC price
        """
        return self.volatility_calculator.get_metrics()

    def _on_open(self, ws):
        """Handle WebSocket connection open."""
        logger.info("Binance WebSocket connected", symbol=self.symbol)
        self._reconnect_delay = 1.0

    def _on_message(self, ws, message: str):
        """Handle incoming trade message."""
        try:
            data = json.loads(message)
            self._process_trade(data)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from Binance", error=str(e))

    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        logger.error("Binance WebSocket error", error=str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close."""
        logger.warning(
            "Binance WebSocket closed",
            code=close_status_code,
            message=close_msg,
        )

        if self._running:
            self._schedule_reconnect()

    def _process_trade(self, data: dict):
        """Process trade message from Binance."""
        if data.get("e") != "trade":
            return

        try:
            price = float(data.get("p", 0))
            timestamp_ms = data.get("T", 0)
            timestamp = datetime.fromtimestamp(
                timestamp_ms / 1000, tz=timezone.utc
            )

            self._current_price = price
            self._last_update = timestamp

            # Update volatility calculator
            self.volatility_calculator.add_price(price, timestamp)

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(price)
                except Exception as e:
                    logger.error("Price callback error", error=str(e))

        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse trade", error=str(e))

    def _schedule_reconnect(self):
        """Schedule reconnection with exponential backoff."""
        if not self._running:
            return

        logger.info(
            "Scheduling Binance reconnection",
            delay=self._reconnect_delay,
        )

        def reconnect():
            import time
            time.sleep(self._reconnect_delay)
            if self._running:
                self._connect()

        thread = threading.Thread(target=reconnect, daemon=True)
        thread.start()

        self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    def _connect(self):
        """Establish WebSocket connection."""
        stream_url = f"{self.BINANCE_WS_URL}/{self.symbol}@trade"

        self._ws = websocket.WebSocketApp(
            stream_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._thread = threading.Thread(
            target=lambda: self._ws.run_forever(ping_interval=30),
            daemon=True,
        )
        self._thread.start()

    def start(self):
        """Start the price feed."""
        if self._running:
            return

        self._running = True
        self._connect()

    def stop(self):
        """Stop the price feed."""
        self._running = False
        if self._ws:
            self._ws.close()

    def wait_for_price(self, timeout: float = 10.0) -> bool:
        """
        Wait for initial price to be available.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            True if price available, False if timeout
        """
        import time

        start = time.time()
        while time.time() - start < timeout:
            if self._current_price is not None:
                return True
            time.sleep(0.1)
        return False


class MockPriceFeed:
    """
    Mock price feed for paper trading.

    Generates realistic price movements for testing.
    """

    def __init__(
        self,
        initial_price: float = 97500.0,
        volatility: float = 50.0,
    ):
        self.initial_price = initial_price
        self._volatility = volatility
        self._current_price = initial_price
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def current_price(self) -> float:
        return self._current_price

    @property
    def volatility(self) -> float:
        return self._volatility

    def get_volatility_metrics(self) -> VolatilityMetrics:
        return VolatilityMetrics(
            rolling_std=self._volatility,
            realized_vol=self._volatility * 100,  # Annualized
            current_price=self._current_price,
            sample_count=100,
            last_update=datetime.now(timezone.utc),
        )

    def _simulate(self):
        """Simulate price movement."""
        import time
        import random

        while self._running:
            # Random walk
            change = random.gauss(0, self._volatility / 100)
            self._current_price *= (1 + change)
            time.sleep(1.0)

    def start(self):
        """Start mock feed."""
        self._running = True
        self._thread = threading.Thread(target=self._simulate, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop mock feed."""
        self._running = False

    def wait_for_price(self, timeout: float = 10.0) -> bool:
        return True
