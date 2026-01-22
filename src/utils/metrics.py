"""
Performance metrics collection.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import deque
import threading


@dataclass
class TradeMetrics:
    """Metrics for a single trade."""
    timestamp: datetime
    token_type: str
    side: str
    price: float
    size: float
    is_maker: bool
    latency_ms: float = 0.0


@dataclass
class SessionMetrics:
    """Aggregated session metrics."""
    start_time: datetime
    end_time: Optional[datetime] = None
    total_trades: int = 0
    maker_trades: int = 0
    taker_trades: int = 0
    total_volume: float = 0.0
    yes_volume: float = 0.0
    no_volume: float = 0.0
    total_pnl: float = 0.0
    hedged_pairs: float = 0.0
    guaranteed_profit: float = 0.0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    quotes_sent: int = 0
    orders_placed: int = 0
    orders_cancelled: int = 0
    fills_received: int = 0


class MetricsCollector:
    """
    Collects and aggregates trading metrics.

    Thread-safe for use with async operations.
    """

    def __init__(self, window_size: int = 1000):
        """
        Initialize metrics collector.

        Args:
            window_size: Number of trades to keep in memory
        """
        self._trades: deque = deque(maxlen=window_size)
        self._latencies: deque = deque(maxlen=window_size)
        self._lock = threading.Lock()

        self._session_start = datetime.now(timezone.utc)
        self._total_trades = 0
        self._maker_trades = 0
        self._total_volume = 0.0
        self._yes_volume = 0.0
        self._no_volume = 0.0
        self._quotes_sent = 0
        self._orders_placed = 0
        self._orders_cancelled = 0
        self._fills_received = 0

    def record_trade(
        self,
        token_type: str,
        side: str,
        price: float,
        size: float,
        is_maker: bool = True,
        latency_ms: float = 0.0,
    ):
        """
        Record a trade.

        Args:
            token_type: "YES" or "NO"
            side: "BUY" or "SELL"
            price: Trade price
            size: Trade size
            is_maker: Was this a maker trade
            latency_ms: Order-to-fill latency
        """
        trade = TradeMetrics(
            timestamp=datetime.now(timezone.utc),
            token_type=token_type,
            side=side,
            price=price,
            size=size,
            is_maker=is_maker,
            latency_ms=latency_ms,
        )

        with self._lock:
            self._trades.append(trade)
            self._total_trades += 1
            if is_maker:
                self._maker_trades += 1

            volume = price * size
            self._total_volume += volume
            if token_type.upper() == "YES":
                self._yes_volume += volume
            else:
                self._no_volume += volume

            if latency_ms > 0:
                self._latencies.append(latency_ms)
            self._fills_received += 1

    def record_quote(self):
        """Record a quote update."""
        with self._lock:
            self._quotes_sent += 1

    def record_order_placed(self):
        """Record an order placement."""
        with self._lock:
            self._orders_placed += 1

    def record_order_cancelled(self):
        """Record an order cancellation."""
        with self._lock:
            self._orders_cancelled += 1

    def get_session_metrics(
        self,
        pnl: float = 0.0,
        hedged_pairs: float = 0.0,
        guaranteed_profit: float = 0.0,
    ) -> SessionMetrics:
        """
        Get aggregated session metrics.

        Args:
            pnl: Current P&L
            hedged_pairs: Current hedged pairs
            guaranteed_profit: Current guaranteed profit

        Returns:
            SessionMetrics with current state
        """
        with self._lock:
            avg_latency = 0.0
            max_latency = 0.0
            if self._latencies:
                avg_latency = sum(self._latencies) / len(self._latencies)
                max_latency = max(self._latencies)

            return SessionMetrics(
                start_time=self._session_start,
                end_time=datetime.now(timezone.utc),
                total_trades=self._total_trades,
                maker_trades=self._maker_trades,
                taker_trades=self._total_trades - self._maker_trades,
                total_volume=self._total_volume,
                yes_volume=self._yes_volume,
                no_volume=self._no_volume,
                total_pnl=pnl,
                hedged_pairs=hedged_pairs,
                guaranteed_profit=guaranteed_profit,
                avg_latency_ms=avg_latency,
                max_latency_ms=max_latency,
                quotes_sent=self._quotes_sent,
                orders_placed=self._orders_placed,
                orders_cancelled=self._orders_cancelled,
                fills_received=self._fills_received,
            )

    def get_recent_trades(self, count: int = 10) -> List[TradeMetrics]:
        """Get recent trades."""
        with self._lock:
            return list(self._trades)[-count:]

    def reset(self):
        """Reset metrics for new session."""
        with self._lock:
            self._trades.clear()
            self._latencies.clear()
            self._session_start = datetime.now(timezone.utc)
            self._total_trades = 0
            self._maker_trades = 0
            self._total_volume = 0.0
            self._yes_volume = 0.0
            self._no_volume = 0.0
            self._quotes_sent = 0
            self._orders_placed = 0
            self._orders_cancelled = 0
            self._fills_received = 0
