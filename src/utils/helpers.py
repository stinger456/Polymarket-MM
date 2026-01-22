"""
Utility helper functions.
"""

import re
from typing import Optional, Union
from decimal import Decimal, ROUND_DOWN


def parse_strike_price(text: str) -> Optional[float]:
    """
    Extract strike price from market question text.

    Examples:
        "Will BTC be above $97,500 at 3:00 PM?" -> 97500.0
        "Bitcoin price above 98000 USD" -> 98000.0

    Args:
        text: Market question or description

    Returns:
        Strike price or None if not found
    """
    patterns = [
        r"\$?([\d,]+(?:\.\d+)?)",
        r"([\d,]+)\s*(?:usd|dollars?)?",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                price = float(match.replace(",", ""))
                # Sanity check for BTC price range
                if 10000 < price < 500000:
                    return price
            except ValueError:
                continue

    return None


def format_price(price: float, decimals: int = 4) -> str:
    """
    Format price for display.

    Args:
        price: Price value
        decimals: Number of decimal places

    Returns:
        Formatted price string
    """
    return f"{price:.{decimals}f}"


def format_usd(amount: float) -> str:
    """
    Format USD amount for display.

    Args:
        amount: Dollar amount

    Returns:
        Formatted string like "$1,234.56"
    """
    return f"${amount:,.2f}"


def format_percent(value: float) -> str:
    """
    Format percentage for display.

    Args:
        value: Value (0.05 = 5%)

    Returns:
        Formatted string like "5.00%"
    """
    return f"{value * 100:.2f}%"


def safe_float(value: Union[str, float, int, None], default: float = 0.0) -> float:
    """
    Safely convert value to float.

    Args:
        value: Value to convert
        default: Default if conversion fails

    Returns:
        Float value
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Union[str, float, int, None], default: int = 0) -> int:
    """
    Safely convert value to int.

    Args:
        value: Value to convert
        default: Default if conversion fails

    Returns:
        Integer value
    """
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def round_price(price: float, tick_size: float = 0.01) -> float:
    """
    Round price to nearest tick.

    Args:
        price: Price to round
        tick_size: Minimum tick size

    Returns:
        Rounded price
    """
    decimal_price = Decimal(str(price))
    decimal_tick = Decimal(str(tick_size))
    rounded = (decimal_price / decimal_tick).quantize(
        Decimal('1'), rounding=ROUND_DOWN
    ) * decimal_tick
    return float(rounded)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp value to range.

    Args:
        value: Value to clamp
        min_val: Minimum allowed
        max_val: Maximum allowed

    Returns:
        Clamped value
    """
    return max(min_val, min(max_val, value))


def time_remaining_str(seconds: int) -> str:
    """
    Format time remaining as human-readable string.

    Args:
        seconds: Seconds remaining

    Returns:
        String like "5m 30s" or "1h 15m"
    """
    if seconds < 0:
        return "expired"

    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    secs = seconds % 60

    if minutes < 60:
        return f"{minutes}m {secs}s"

    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
