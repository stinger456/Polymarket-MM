"""
Volatility calculation for pricing model.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timezone
import numpy as np


@dataclass
class VolatilityMetrics:
    """Container for volatility calculations."""
    rolling_std: float
    realized_vol: float  # Annualized
    current_price: float
    sample_count: int
    last_update: datetime


class VolatilityCalculator:
    """
    Calculates rolling volatility from price samples.

    Uses standard deviation of log returns for realized volatility.
    """

    # Seconds in a year for annualization
    SECONDS_PER_YEAR = 365.25 * 24 * 3600

    def __init__(
        self,
        window_size: int = 100,
        min_samples: int = 10,
        scale: float = 1.0,
    ):
        """
        Initialize volatility calculator.

        Args:
            window_size: Number of price samples to keep
            min_samples: Minimum samples needed for valid calculation
            scale: Multiplier for volatility (adjust for model fit)
        """
        self.window_size = window_size
        self.min_samples = min_samples
        self.scale = scale
        self._prices: deque = deque(maxlen=window_size)
        self._timestamps: deque = deque(maxlen=window_size)
        self._returns: deque = deque(maxlen=window_size - 1)

    def add_price(self, price: float, timestamp: Optional[datetime] = None):
        """
        Add a new price observation.

        Args:
            price: Current price
            timestamp: Time of observation (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        if price <= 0:
            return

        # Calculate return if we have previous price
        if self._prices:
            prev_price = self._prices[-1]
            if prev_price > 0:
                log_return = math.log(price / prev_price)
                self._returns.append(log_return)

        self._prices.append(price)
        self._timestamps.append(timestamp)

    @property
    def current_price(self) -> Optional[float]:
        """Get the most recent price."""
        return self._prices[-1] if self._prices else None

    @property
    def sample_count(self) -> int:
        """Number of price samples."""
        return len(self._prices)

    def calculate_rolling_std(self) -> Optional[float]:
        """
        Calculate rolling standard deviation of prices.

        This is the simple price volatility (not returns-based).
        """
        if len(self._prices) < self.min_samples:
            return None

        prices = list(self._prices)
        return float(np.std(prices))

    def calculate_realized_vol(self) -> Optional[float]:
        """
        Calculate annualized realized volatility.

        Uses standard deviation of log returns, annualized.
        """
        if len(self._returns) < self.min_samples:
            return None

        returns = list(self._returns)

        # Standard deviation of returns
        returns_std = float(np.std(returns))

        # Estimate average time between samples
        if len(self._timestamps) >= 2:
            total_time = (
                self._timestamps[-1] - self._timestamps[0]
            ).total_seconds()
            avg_interval = total_time / (len(self._timestamps) - 1)
        else:
            avg_interval = 1.0  # Default to 1 second

        # Annualize: vol * sqrt(periods per year)
        periods_per_year = self.SECONDS_PER_YEAR / avg_interval
        annualized_vol = returns_std * math.sqrt(periods_per_year)

        return annualized_vol * self.scale

    def calculate_hourly_vol(self) -> Optional[float]:
        """
        Calculate volatility scaled to one hour.

        This is more relevant for hourly markets.
        """
        realized = self.calculate_realized_vol()
        if realized is None:
            return None

        # Scale from annual to hourly
        # Annual vol = hourly vol * sqrt(hours per year)
        hours_per_year = 365.25 * 24
        hourly_vol = realized / math.sqrt(hours_per_year)

        return hourly_vol

    def get_metrics(self) -> Optional[VolatilityMetrics]:
        """
        Get all volatility metrics.

        Returns None if insufficient data.
        """
        if len(self._prices) < self.min_samples:
            return None

        rolling_std = self.calculate_rolling_std()
        realized_vol = self.calculate_realized_vol()

        if rolling_std is None or realized_vol is None:
            return None

        return VolatilityMetrics(
            rolling_std=rolling_std,
            realized_vol=realized_vol,
            current_price=self._prices[-1],
            sample_count=len(self._prices),
            last_update=self._timestamps[-1] if self._timestamps else datetime.now(timezone.utc),
        )

    def clear(self):
        """Clear all price history."""
        self._prices.clear()
        self._timestamps.clear()
        self._returns.clear()


class ImpliedVolCalculator:
    """
    Calculates implied volatility from market prices.

    Uses option pricing theory to back out implied vol from
    the market's YES/NO prices.
    """

    @staticmethod
    def calculate_implied_vol(
        yes_price: float,
        strike_price: float,
        current_price: float,
        time_remaining_seconds: int,
        iterations: int = 20,
    ) -> Optional[float]:
        """
        Back out implied volatility from market price.

        Uses Newton-Raphson iteration on the fair value formula.
        """
        if time_remaining_seconds <= 0:
            return None

        if not (0.01 < yes_price < 0.99):
            return None

        from scipy.stats import norm

        time_factor = math.sqrt(time_remaining_seconds / 3600)

        # Initial guess
        vol = 100.0  # Starting volatility in $ terms

        for _ in range(iterations):
            if vol <= 0:
                vol = 10.0

            z = (strike_price - current_price) / (vol * time_factor)
            model_prob = 1 - norm.cdf(z)

            # Derivative (vega-like)
            vega = norm.pdf(z) * z / vol

            if abs(vega) < 1e-10:
                break

            # Newton step
            error = model_prob - yes_price
            vol = vol - error / vega

            if abs(error) < 1e-6:
                break

        return max(0, vol)
