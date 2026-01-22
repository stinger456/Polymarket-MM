"""
Configuration management for Polymarket Market Maker.

Handles loading configuration from environment variables and YAML files,
and initializes the Polymarket CLOB client.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# Load environment variables from .env file
load_dotenv()


class TradingConfig(BaseModel):
    """Trading parameters configuration."""

    min_edge_threshold: float = Field(
        default=0.02,
        description="Minimum spread to trade (0.02 = 2%)"
    )
    max_position_size: float = Field(
        default=500.0,
        description="Maximum USD per side"
    )
    max_total_exposure: float = Field(
        default=5000.0,
        description="Maximum USD total exposure"
    )
    num_quote_levels: int = Field(
        default=3,
        description="Number of order levels per side"
    )
    level_spacing: float = Field(
        default=0.01,
        description="Price spacing between levels"
    )
    quote_refresh_seconds: float = Field(
        default=2.0,
        description="How often to update quotes"
    )
    base_order_size: float = Field(
        default=50.0,
        description="Base order size in shares"
    )


class RiskConfig(BaseModel):
    """Risk management configuration."""

    max_drawdown_pct: float = Field(
        default=10.0,
        description="Maximum drawdown percentage before stopping"
    )
    max_inventory_skew: float = Field(
        default=0.6,
        description="Maximum inventory imbalance (0-1)"
    )
    min_time_remaining: int = Field(
        default=300,
        description="Minimum seconds remaining to enter a market"
    )
    max_trades_per_minute: int = Field(
        default=30,
        description="Maximum trades per minute rate limit"
    )


class PricingConfig(BaseModel):
    """Pricing model configuration."""

    volatility_window: int = Field(
        default=100,
        description="Number of price samples for volatility calculation"
    )
    volatility_scale: float = Field(
        default=1.0,
        description="Multiplier for volatility estimate"
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO")
    format: str = Field(default="structured")
    file: str = Field(default="logs/bot.log")


class Config(BaseSettings):
    """
    Main configuration class.

    Loads settings from environment variables with POLY_ prefix,
    and can be supplemented with YAML config files.
    """

    # Wallet credentials
    private_key: str = Field(
        default="",
        alias="POLY_PRIVATE_KEY",
        description="Ethereum private key (without 0x prefix)"
    )
    safe_address: str = Field(
        default="",
        alias="POLY_SAFE_ADDRESS",
        description="Polymarket Safe address"
    )

    # Optional API credentials
    api_key: Optional[str] = Field(
        default=None,
        alias="POLY_API_KEY"
    )
    api_secret: Optional[str] = Field(
        default=None,
        alias="POLY_API_SECRET"
    )
    api_passphrase: Optional[str] = Field(
        default=None,
        alias="POLY_API_PASSPHRASE"
    )

    # Endpoints
    clob_url: str = Field(
        default="https://clob.polymarket.com",
        alias="POLY_CLOB_URL"
    )
    gamma_url: str = Field(
        default="https://gamma-api.polymarket.com",
        alias="POLY_GAMMA_URL"
    )
    binance_ws_url: str = Field(
        default="wss://stream.binance.com:9443/ws",
        alias="BINANCE_WS_URL"
    )

    # Operational
    paper_trading: bool = Field(
        default=True,
        alias="PAPER_TRADING"
    )
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL"
    )

    # Trading parameters from env
    min_edge_threshold: float = Field(default=0.02, alias="MIN_EDGE_THRESHOLD")
    max_position_size: float = Field(default=500.0, alias="MAX_POSITION_SIZE")
    max_total_exposure: float = Field(default=5000.0, alias="MAX_TOTAL_EXPOSURE")
    num_quote_levels: int = Field(default=3, alias="NUM_QUOTE_LEVELS")
    level_spacing: float = Field(default=0.01, alias="LEVEL_SPACING")
    quote_refresh_seconds: float = Field(default=2.0, alias="QUOTE_REFRESH_SECONDS")
    base_order_size: float = Field(default=50.0, alias="BASE_ORDER_SIZE")

    # Risk parameters from env
    max_drawdown_pct: float = Field(default=10.0, alias="MAX_DRAWDOWN_PCT")
    max_inventory_skew: float = Field(default=0.6, alias="MAX_INVENTORY_SKEW")
    min_time_remaining: int = Field(default=60, alias="MIN_TIME_REMAINING")
    max_trades_per_minute: int = Field(default=30, alias="MAX_TRADES_PER_MINUTE")

    # Nested configs (populated from YAML)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Chain configuration
    chain_id: int = Field(default=137, description="Polygon chain ID")

    # Signature type: 0=EOA (MetaMask), 1=Email/Magic, 2=Browser proxy (Safe)
    signature_type: int = Field(
        default=1,
        alias="POLY_SIGNATURE_TYPE",
        description="Signature type: 0=EOA, 1=Email/Magic, 2=Browser proxy"
    )

    # Event slug for specific market trading
    event_slug: Optional[str] = Field(
        default=None,
        alias="POLY_EVENT_SLUG",
        description="Event slug to trade (e.g., 'bitcoin-up-or-down-january-22-8pm-et')"
    )

    class Config:
        env_prefix = ""
        populate_by_name = True
        extra = "ignore"

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "Config":
        """
        Load configuration from a YAML file, supplemented by environment variables.

        Environment variables take precedence over YAML values.
        """
        config_data = {}

        yaml_file = Path(yaml_path)
        if yaml_file.exists():
            with open(yaml_file) as f:
                config_data = yaml.safe_load(f) or {}

        # Create nested configs from YAML
        trading_data = config_data.get("trading", {})
        risk_data = config_data.get("risk", {})
        pricing_data = config_data.get("pricing", {})
        logging_data = config_data.get("logging", {})

        return cls(
            trading=TradingConfig(**trading_data),
            risk=RiskConfig(**risk_data),
            pricing=PricingConfig(**pricing_data),
            logging=LoggingConfig(**logging_data),
        )

    def validate_credentials(self) -> bool:
        """Check if required credentials are set."""
        return bool(self.private_key and self.safe_address)

    def get_trading_config(self) -> TradingConfig:
        """
        Get trading config, preferring env vars over YAML values.
        """
        return TradingConfig(
            min_edge_threshold=self.min_edge_threshold,
            max_position_size=self.max_position_size,
            max_total_exposure=self.max_total_exposure,
            num_quote_levels=self.num_quote_levels,
            level_spacing=self.level_spacing,
            quote_refresh_seconds=self.quote_refresh_seconds,
            base_order_size=self.base_order_size,
        )

    def get_risk_config(self) -> RiskConfig:
        """
        Get risk config, preferring env vars over YAML values.
        """
        return RiskConfig(
            max_drawdown_pct=self.max_drawdown_pct,
            max_inventory_skew=self.max_inventory_skew,
            min_time_remaining=self.min_time_remaining,
            max_trades_per_minute=self.max_trades_per_minute,
        )


# Contract addresses (Polygon mainnet)
CONTRACT_ADDRESSES = {
    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "EXCHANGE": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "NEG_RISK_EXCHANGE": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "NEG_RISK_ADAPTER": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

# WebSocket URLs
WS_URLS = {
    "MARKET": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "USER": "wss://ws-subscriptions-clob.polymarket.com/ws/user",
}


def create_clob_client(config: Config):
    """
    Initialize the Polymarket CLOB client using the official py-clob-client.

    Signature types:
    - 0: EOA (MetaMask, hardware wallet)
    - 1: Email/Magic wallet
    - 2: Browser proxy wallet (Polymarket Safe)

    Returns:
        ClobClient: Initialized and authenticated client
    """
    from py_clob_client.client import ClobClient

    if config.paper_trading:
        # For paper trading, return a mock client or read-only client
        return ClobClient(config.clob_url)

    if not config.validate_credentials():
        raise ValueError(
            "Missing required credentials. "
            "Set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS environment variables."
        )

    # Initialize client with configured signature type
    client = ClobClient(
        config.clob_url,
        key=config.private_key,
        chain_id=config.chain_id,
        signature_type=config.signature_type,  # 0=EOA, 1=Email, 2=Browser proxy
        funder=config.safe_address,
    )

    # Set API credentials for authenticated endpoints
    client.set_api_creds(client.create_or_derive_api_creds())

    return client


def create_read_only_client(config: Config):
    """
    Create a read-only CLOB client for market data.

    No authentication required.
    """
    from py_clob_client.client import ClobClient

    return ClobClient(config.clob_url)


def load_config(
    config_path: Optional[str] = None,
    paper_trading: bool = True,
    event_slug: Optional[str] = None,
) -> Config:
    """
    Load configuration from environment and optional YAML file.

    Args:
        config_path: Path to YAML config file
        paper_trading: Override paper trading setting
        event_slug: Event slug to trade

    Returns:
        Config: Loaded configuration
    """
    if config_path:
        config = Config.from_yaml(config_path)
    else:
        config = Config()

    # Override paper trading if specified
    if paper_trading is not None:
        config.paper_trading = paper_trading

    # Override event slug if specified
    if event_slug is not None:
        config.event_slug = event_slug

    return config
