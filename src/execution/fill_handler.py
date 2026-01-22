"""
Fill handler for processing order fills.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Callable, List
from enum import Enum
import structlog


logger = structlog.get_logger(__name__)


class FillSide(Enum):
    """Side of the fill."""
    BUY = "BUY"
    SELL = "SELL"


class FillToken(Enum):
    """Token type that was filled."""
    YES = "YES"
    NO = "NO"


@dataclass
class Fill:
    """Represents a single fill."""
    order_id: str
    token_id: str
    token_type: FillToken  # YES or NO
    side: FillSide  # BUY or SELL
    price: float
    size: float
    timestamp: datetime
    market_id: Optional[str] = None
    fee: float = 0.0

    @property
    def notional(self) -> float:
        """Dollar value of fill."""
        return self.price * self.size

    @property
    def is_maker(self) -> bool:
        """Check if this was a maker fill (earned rebate)."""
        return self.fee <= 0


@dataclass
class FillSummary:
    """Summary of fills for P&L calculation."""
    total_yes_bought: float = 0.0
    total_yes_sold: float = 0.0
    total_no_bought: float = 0.0
    total_no_sold: float = 0.0
    total_yes_cost: float = 0.0
    total_no_cost: float = 0.0
    total_fees: float = 0.0
    fill_count: int = 0

    @property
    def net_yes_position(self) -> float:
        """Net YES shares held."""
        return self.total_yes_bought - self.total_yes_sold

    @property
    def net_no_position(self) -> float:
        """Net NO shares held."""
        return self.total_no_bought - self.total_no_sold

    @property
    def total_cost(self) -> float:
        """Total cost including fees."""
        return self.total_yes_cost + self.total_no_cost + self.total_fees

    @property
    def hedged_pairs(self) -> float:
        """Number of fully hedged YES+NO pairs."""
        return min(self.net_yes_position, self.net_no_position)

    @property
    def guaranteed_profit(self) -> float:
        """Guaranteed profit from hedged pairs."""
        return self.hedged_pairs - self.total_cost


class FillHandler:
    """
    Handles order fills and updates positions.

    Processes fills from WebSocket or API and updates:
    - Inventory tracker
    - P&L tracker
    - Risk manager
    """

    def __init__(self):
        self._fills: List[Fill] = []
        self._callbacks: List[Callable[[Fill], None]] = []
        self._summary = FillSummary()

    def add_callback(self, callback: Callable[[Fill], None]):
        """Add callback for fill events."""
        self._callbacks.append(callback)

    def process_fill(
        self,
        order_id: str,
        token_id: str,
        token_type: str,  # "YES" or "NO"
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float,
        timestamp: Optional[datetime] = None,
        market_id: Optional[str] = None,
        fee: float = 0.0,
    ) -> Fill:
        """
        Process a new fill.

        Args:
            order_id: Order that was filled
            token_id: Token ID
            token_type: "YES" or "NO"
            side: "BUY" or "SELL"
            price: Fill price
            size: Fill size
            timestamp: Fill time
            market_id: Market condition ID
            fee: Fee charged

        Returns:
            Fill object
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        fill = Fill(
            order_id=order_id,
            token_id=token_id,
            token_type=FillToken[token_type.upper()],
            side=FillSide[side.upper()],
            price=price,
            size=size,
            timestamp=timestamp,
            market_id=market_id,
            fee=fee,
        )

        self._fills.append(fill)
        self._update_summary(fill)

        logger.info(
            "Fill processed",
            order_id=order_id,
            token_type=token_type,
            side=side,
            price=price,
            size=size,
        )

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(fill)
            except Exception as e:
                logger.error("Fill callback error", error=str(e))

        return fill

    def _update_summary(self, fill: Fill):
        """Update fill summary with new fill."""
        self._summary.fill_count += 1
        self._summary.total_fees += fill.fee

        if fill.token_type == FillToken.YES:
            if fill.side == FillSide.BUY:
                self._summary.total_yes_bought += fill.size
                self._summary.total_yes_cost += fill.notional
            else:
                self._summary.total_yes_sold += fill.size
                self._summary.total_yes_cost -= fill.notional
        else:
            if fill.side == FillSide.BUY:
                self._summary.total_no_bought += fill.size
                self._summary.total_no_cost += fill.notional
            else:
                self._summary.total_no_sold += fill.size
                self._summary.total_no_cost -= fill.notional

    def process_websocket_message(
        self,
        message: dict,
        yes_token_id: str,
        no_token_id: str,
    ) -> Optional[Fill]:
        """
        Process a WebSocket fill message.

        Args:
            message: WebSocket message dict
            yes_token_id: YES token ID for identification
            no_token_id: NO token ID for identification

        Returns:
            Fill if message was a fill, None otherwise
        """
        event_type = message.get("event_type") or message.get("type")

        if event_type != "MATCHED":
            return None

        try:
            order_id = message.get("order_id", "")
            token_id = message.get("asset_id", "")
            side = message.get("side", "BUY")
            price = float(message.get("match_price", 0))
            size = float(message.get("match_size", 0))

            # Determine token type
            if token_id == yes_token_id:
                token_type = "YES"
            elif token_id == no_token_id:
                token_type = "NO"
            else:
                logger.warning("Unknown token ID in fill", token_id=token_id)
                return None

            return self.process_fill(
                order_id=order_id,
                token_id=token_id,
                token_type=token_type,
                side=side,
                price=price,
                size=size,
            )

        except Exception as e:
            logger.error("Failed to process WebSocket fill", error=str(e))
            return None

    @property
    def summary(self) -> FillSummary:
        """Get current fill summary."""
        return self._summary

    @property
    def fills(self) -> List[Fill]:
        """Get all fills."""
        return list(self._fills)

    def get_recent_fills(self, count: int = 10) -> List[Fill]:
        """Get most recent fills."""
        return list(self._fills[-count:])

    def reset(self):
        """Reset fill tracking (e.g., for new market)."""
        self._fills = []
        self._summary = FillSummary()
