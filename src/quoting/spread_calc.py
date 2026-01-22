"""
Spread calculation for market making.
"""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class SpreadParams:
    """Parameters for spread calculation."""
    base_spread: float  # Base half-spread
    volatility_multiplier: float  # Scale spread with volatility
    time_decay_factor: float  # Widen spread as expiry approaches
    inventory_scale: float  # Scale for inventory adjustment


class SpreadCalculator:
    """
    Calculates optimal spreads for market making.

    Spread is dynamically adjusted based on:
    - Base minimum spread (configurable)
    - Current volatility
    - Time to expiry
    - Inventory imbalance
    """

    def __init__(
        self,
        base_half_spread: float = 0.01,
        min_half_spread: float = 0.005,
        max_half_spread: float = 0.10,
        volatility_multiplier: float = 0.1,
        time_decay_factor: float = 0.5,
        inventory_scale: float = 0.02,
    ):
        """
        Initialize spread calculator.

        Args:
            base_half_spread: Default half-spread
            min_half_spread: Minimum allowed half-spread
            max_half_spread: Maximum allowed half-spread
            volatility_multiplier: How much volatility affects spread
            time_decay_factor: How much time remaining affects spread
            inventory_scale: How much inventory affects spread
        """
        self.base_half_spread = base_half_spread
        self.min_half_spread = min_half_spread
        self.max_half_spread = max_half_spread
        self.volatility_multiplier = volatility_multiplier
        self.time_decay_factor = time_decay_factor
        self.inventory_scale = inventory_scale

    def calculate_half_spread(
        self,
        volatility: Optional[float] = None,
        time_remaining_seconds: Optional[int] = None,
        inventory_skew: float = 0.0,
    ) -> float:
        """
        Calculate optimal half-spread.

        Args:
            volatility: Current volatility (dollar terms)
            time_remaining_seconds: Seconds until expiry
            inventory_skew: Inventory imbalance (-1 to 1)

        Returns:
            Half-spread to use for quotes
        """
        half_spread = self.base_half_spread

        # Volatility adjustment
        if volatility is not None and volatility > 0:
            # Higher vol = wider spread
            vol_adjustment = volatility * self.volatility_multiplier / 1000
            half_spread += vol_adjustment

        # Time decay adjustment
        if time_remaining_seconds is not None:
            if time_remaining_seconds < 300:  # Less than 5 minutes
                # Widen spread near expiry
                time_factor = 1 + self.time_decay_factor * (300 - time_remaining_seconds) / 300
                half_spread *= time_factor

        # Inventory adjustment (absolute value affects both sides)
        inv_adjustment = abs(inventory_skew) * self.inventory_scale
        half_spread += inv_adjustment

        # Clamp to range
        return max(self.min_half_spread, min(self.max_half_spread, half_spread))

    def calculate_asymmetric_spread(
        self,
        inventory_skew: float,
        volatility: Optional[float] = None,
        time_remaining_seconds: Optional[int] = None,
    ) -> tuple:
        """
        Calculate asymmetric spread based on inventory.

        When long YES, we want to:
        - Lower YES bids (less eager to buy more)
        - Raise NO bids (more eager to hedge)

        Args:
            inventory_skew: -1 (long NO) to +1 (long YES)
            volatility: Current volatility
            time_remaining_seconds: Time to expiry

        Returns:
            Tuple of (yes_adjustment, no_adjustment)
        """
        base = self.calculate_half_spread(
            volatility, time_remaining_seconds, inventory_skew
        )

        # Skew adjustments
        # Positive skew = long YES = lower YES bid, raise NO bid
        yes_adjustment = -inventory_skew * self.inventory_scale
        no_adjustment = inventory_skew * self.inventory_scale

        return (yes_adjustment, no_adjustment)


class LevelSpacingCalculator:
    """
    Calculates spacing between price levels for multi-level quotes.
    """

    def __init__(
        self,
        base_spacing: float = 0.01,
        spacing_growth: float = 1.5,
    ):
        """
        Initialize level spacing calculator.

        Args:
            base_spacing: Spacing after first level
            spacing_growth: Multiplier for each subsequent level
        """
        self.base_spacing = base_spacing
        self.spacing_growth = spacing_growth

    def get_level_offset(self, level: int) -> float:
        """
        Get price offset for a level.

        Level 0 is the best price, level 1+ are further from mid.

        Args:
            level: Level number (0-indexed)

        Returns:
            Price offset from best level
        """
        if level <= 0:
            return 0.0

        # Exponential growth in spacing
        offset = 0.0
        current_spacing = self.base_spacing
        for _ in range(level):
            offset += current_spacing
            current_spacing *= self.spacing_growth

        return offset

    def get_level_offsets(self, num_levels: int) -> list:
        """
        Get offsets for all levels.

        Args:
            num_levels: Number of levels

        Returns:
            List of offsets [0.0, 0.01, 0.025, ...]
        """
        return [self.get_level_offset(i) for i in range(num_levels)]


class SizeCalculator:
    """
    Calculates order sizes for each level.
    """

    def __init__(
        self,
        base_size: float = 50.0,
        size_decay: float = 0.6,
        min_size: float = 5.0,
    ):
        """
        Initialize size calculator.

        Args:
            base_size: Size at best level
            size_decay: Multiplier for each subsequent level
            min_size: Minimum order size
        """
        self.base_size = base_size
        self.size_decay = size_decay
        self.min_size = min_size

    def get_level_size(self, level: int) -> float:
        """
        Get size for a level.

        Args:
            level: Level number (0-indexed)

        Returns:
            Order size for level
        """
        size = self.base_size * (self.size_decay ** level)
        return max(self.min_size, size)

    def get_level_sizes(self, num_levels: int) -> list:
        """
        Get sizes for all levels.

        Args:
            num_levels: Number of levels

        Returns:
            List of sizes [50, 30, 18, ...]
        """
        return [self.get_level_size(i) for i in range(num_levels)]
