"""
Fair value calculation for binary options pricing.

Calculates the "true" probability of BTC finishing above strike.
"""

import math
from dataclasses import dataclass
from typing import Tuple, Optional
from scipy.stats import norm


@dataclass
class FairValueResult:
    """Result of fair value calculation."""
    yes_price: float
    no_price: float
    delta: float  # Sensitivity to price
    gamma: float  # Sensitivity of delta
    time_decay: float  # Theta-like metric


class FairValueModel:
    """
    Calculates the fair probability of BTC finishing above strike.

    For hourly markets, uses a simplified Black-Scholes-like model
    for binary options:

    P(above strike) = 1 - Φ(z)
    z = (strike - current) / (volatility * sqrt(time))

    Where:
    - Φ is the standard normal CDF
    - volatility is in dollar terms (not percentage)
    - time is in hours
    """

    def __init__(
        self,
        volatility_scale: float = 1.0,
        min_probability: float = 0.02,
        max_probability: float = 0.98,
    ):
        """
        Initialize fair value model.

        Args:
            volatility_scale: Multiplier for volatility input
            min_probability: Floor for probability (avoid 0)
            max_probability: Ceiling for probability (avoid 1)
        """
        self.volatility_scale = volatility_scale
        self.min_probability = min_probability
        self.max_probability = max_probability

    def calculate_fair_value(
        self,
        current_price: float,
        strike_price: float,
        time_remaining_seconds: int,
        volatility: float,
    ) -> Tuple[float, float]:
        """
        Calculate fair YES and NO prices.

        Args:
            current_price: Current BTC price
            strike_price: Strike price of the market
            time_remaining_seconds: Seconds until resolution
            volatility: Hourly volatility in dollar terms

        Returns:
            Tuple of (fair_yes_price, fair_no_price)
        """
        # Handle expired markets
        if time_remaining_seconds <= 0:
            if current_price > strike_price:
                return (1.0, 0.0)
            elif current_price < strike_price:
                return (0.0, 1.0)
            else:
                return (0.5, 0.5)

        # Scale volatility
        adjusted_vol = volatility * self.volatility_scale

        if adjusted_vol <= 0:
            # No volatility - deterministic outcome
            if current_price > strike_price:
                return (self.max_probability, self.min_probability)
            elif current_price < strike_price:
                return (self.min_probability, self.max_probability)
            else:
                return (0.5, 0.5)

        # Time factor: scale to hourly volatility
        # sqrt(T) where T is time in hours
        time_hours = time_remaining_seconds / 3600
        time_factor = math.sqrt(time_hours)

        # Adjusted volatility for time period
        period_vol = adjusted_vol * time_factor

        # Z-score: how many standard deviations is strike from current?
        z = (strike_price - current_price) / period_vol

        # Probability of ending above strike
        prob_above = 1 - norm.cdf(z)

        # Clamp to reasonable range
        prob_above = max(self.min_probability, min(self.max_probability, prob_above))

        return (prob_above, 1 - prob_above)

    def calculate_fair_value_detailed(
        self,
        current_price: float,
        strike_price: float,
        time_remaining_seconds: int,
        volatility: float,
    ) -> FairValueResult:
        """
        Calculate fair value with additional risk metrics.

        Returns FairValueResult with greeks-like metrics.
        """
        yes_price, no_price = self.calculate_fair_value(
            current_price, strike_price, time_remaining_seconds, volatility
        )

        # Handle edge cases
        if time_remaining_seconds <= 0 or volatility <= 0:
            return FairValueResult(
                yes_price=yes_price,
                no_price=no_price,
                delta=0.0,
                gamma=0.0,
                time_decay=0.0,
            )

        # Calculate delta (sensitivity to price)
        # Delta is the derivative of prob w.r.t. price
        adjusted_vol = volatility * self.volatility_scale
        time_hours = time_remaining_seconds / 3600
        time_factor = math.sqrt(time_hours)
        period_vol = adjusted_vol * time_factor

        z = (strike_price - current_price) / period_vol

        # Delta = -pdf(z) / period_vol
        delta = -norm.pdf(z) / period_vol

        # Gamma = pdf(z) * z / (period_vol^2)
        gamma = norm.pdf(z) * z / (period_vol ** 2)

        # Time decay (theta-like)
        # How much does probability change per second?
        if time_remaining_seconds > 1:
            yes_future, _ = self.calculate_fair_value(
                current_price, strike_price,
                time_remaining_seconds - 1, volatility
            )
            time_decay = yes_future - yes_price
        else:
            time_decay = 0.0

        return FairValueResult(
            yes_price=yes_price,
            no_price=no_price,
            delta=delta,
            gamma=gamma,
            time_decay=time_decay,
        )


class EdgeCalculator:
    """
    Calculates trading edge (expected profit).
    """

    @staticmethod
    def calculate_edge(
        fair_yes: float,
        fair_no: float,
        market_yes_bid: float,
        market_yes_ask: float,
        market_no_bid: float,
        market_no_ask: float,
    ) -> dict:
        """
        Calculate edge on each side.

        Edge is the difference between fair value and market price,
        expressed as expected profit.

        Positive edge on bid = market bid is above fair value (sell opportunity)
        Positive edge on ask = market ask is below fair value (buy opportunity)

        Returns dict with edges for each order type.
        """
        return {
            # Buying YES - we buy at ask, edge is fair - ask
            "buy_yes_edge": fair_yes - market_yes_ask,

            # Selling YES - we sell at bid, edge is bid - fair
            "sell_yes_edge": market_yes_bid - fair_yes,

            # Buying NO - we buy at ask, edge is fair - ask
            "buy_no_edge": fair_no - market_no_ask,

            # Selling NO - we sell at bid, edge is bid - fair
            "sell_no_edge": market_no_bid - fair_no,

            # Combined edge from making both sides
            # If we buy both YES and NO, total cost = ask_yes + ask_no
            # Guaranteed payout = $1.00
            # Edge = 1.00 - (ask_yes + ask_no)
            "arbitrage_edge": 1.0 - (market_yes_ask + market_no_ask),

            # Market making edge (post bids on both sides)
            # If both fill: cost = bid_yes + bid_no, payout = $1
            # But this is wrong - we're BIDDING so we're BUYING
            # Let me recalculate...
            # If we post a YES bid at X and it fills, we own YES at X
            # If we post a NO bid at Y and it fills, we own NO at Y
            # Total cost: X + Y
            # Guaranteed payout: $1.00
            # Edge = 1.00 - X - Y
            # But we want to post bids BELOW fair value to have edge
            # So our edge comes from the spread we capture
        }

    @staticmethod
    def calculate_mm_edge(
        our_yes_bid: float,
        our_no_bid: float,
    ) -> float:
        """
        Calculate market making edge if both sides fill.

        Args:
            our_yes_bid: Our bid price for YES
            our_no_bid: Our bid price for NO

        Returns:
            Expected profit if both fill
        """
        total_cost = our_yes_bid + our_no_bid
        guaranteed_payout = 1.0
        return guaranteed_payout - total_cost


class PricingEngine:
    """
    Combined pricing engine for market making.

    Integrates fair value model with market data.
    """

    def __init__(
        self,
        fair_value_model: Optional[FairValueModel] = None,
    ):
        self.fair_value_model = fair_value_model or FairValueModel()
        self.edge_calculator = EdgeCalculator()

    def calculate_fair_value(
        self,
        current_price: float,
        strike_price: float,
        time_remaining_seconds: int,
        volatility: float,
    ) -> Tuple[float, float]:
        """Calculate fair YES/NO prices."""
        return self.fair_value_model.calculate_fair_value(
            current_price, strike_price, time_remaining_seconds, volatility
        )

    def calculate_fair_value_detailed(
        self,
        current_price: float,
        strike_price: float,
        time_remaining_seconds: int,
        volatility: float,
    ) -> FairValueResult:
        """Calculate fair value with greeks."""
        return self.fair_value_model.calculate_fair_value_detailed(
            current_price, strike_price, time_remaining_seconds, volatility
        )

    def has_edge(
        self,
        fair_yes: float,
        fair_no: float,
        market_yes_ask: float,
        market_no_ask: float,
        min_edge: float = 0.02,
    ) -> bool:
        """
        Check if there's enough edge to trade.

        For market making, we need:
        fair_yes + fair_no ≈ 1.0
        market_yes_ask + market_no_ask > 1.0 + min_edge

        This means we can post bids below the asks and capture spread.
        """
        # Combined market prices
        combined_ask = market_yes_ask + market_no_ask

        # If asks sum to less than 1, there's arbitrage opportunity
        if combined_ask < 1.0:
            return True

        # For market making, we want spread room
        # Our cost if both fills at mid = (market_yes_ask + market_no_ask) / 2 approx
        # Actually we bid below the ask, so we need spread
        spread_yes = market_yes_ask - fair_yes
        spread_no = market_no_ask - fair_no

        return spread_yes > min_edge / 2 and spread_no > min_edge / 2
