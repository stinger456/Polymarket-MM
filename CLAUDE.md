# CLAUDE.md - AI Assistant Guide for Polymarket MM

This file provides context for AI assistants working on this codebase.

## Project Overview

This is a **Polymarket Hourly BTC Market Maker** - a trading bot that provides liquidity on Polymarket's hourly Bitcoin prediction markets. The bot quotes both YES and NO sides simultaneously to capture spreads with guaranteed profit when both sides fill.

### Business Logic

1. Polymarket offers hourly markets: "Will BTC be above $X at Y:00?"
2. Each market has YES and NO tokens that should sum to $1.00
3. The bot posts bids on BOTH sides below $1.00 total
4. When both fill, the bot is fully hedged with guaranteed profit
5. Example: Buy YES at $0.48 + NO at $0.47 = $0.95 cost, $1.00 payout = $0.05 profit

## Architecture

```
src/
├── main.py              # Entry point, CLI parsing
├── orchestrator.py      # Main controller, trading loop
├── config.py            # Configuration management, ClobClient init
│
├── market/              # Market discovery
│   ├── discovery.py     # Find hourly BTC markets via Gamma API
│   ├── market_state.py  # Market and MarketState data classes
│   └── gamma_client.py  # Gamma API client
│
├── pricing/             # Fair value calculation
│   ├── fair_value.py    # Binary options pricing model
│   ├── volatility.py    # Volatility calculation
│   └── price_feed.py    # Binance WebSocket price feed
│
├── quoting/             # Quote generation
│   ├── quote_engine.py  # Generate bid/ask quotes
│   ├── inventory.py     # Inventory management
│   └── spread_calc.py   # Spread calculations
│
├── execution/           # Order management
│   ├── order_manager.py # Order lifecycle management
│   ├── clob_client.py   # Polymarket CLOB API wrapper
│   └── fill_handler.py  # Process fills
│
├── risk/                # Risk management
│   ├── risk_manager.py  # Risk limit enforcement
│   ├── position_tracker.py # Track positions per market
│   └── pnl.py           # P&L calculation
│
├── data/                # Market data
│   ├── websocket_manager.py # WebSocket connections
│   └── orderbook.py     # Local orderbook tracking
│
└── utils/               # Utilities
    ├── logging.py       # Structured logging
    ├── metrics.py       # Performance metrics
    └── helpers.py       # Helper functions
```

## Key Technologies

- **py-clob-client**: Official Polymarket Python client - DO NOT build custom API clients
- **asyncio**: Async event loop for the main trading loop
- **websocket-client**: WebSocket connections for real-time data
- **httpx**: HTTP client for Gamma API
- **structlog**: Structured logging
- **scipy**: Statistical functions for pricing model

## Development Workflow

### Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with credentials
```

### Running

```bash
# Paper trading (recommended)
python -m src.main --paper

# Live trading
python -m src.main --live

# With custom config
python -m src.main --config config/production.yaml
```

### Testing

```bash
pytest                           # Run all tests
pytest tests/test_fair_value.py  # Run specific test
pytest --cov=src                 # With coverage
```

### Code Style

- Use `black` for formatting (line length 100)
- Use `isort` for import sorting
- Use type hints
- Follow existing patterns in codebase

## Important Conventions

### 1. Use Official py-clob-client

**CRITICAL**: Always use the official Polymarket client. Never build custom signing or API code.

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
```

### 2. Signature Types

- `signature_type=2` for Polymarket proxy wallet (most common)
- `signature_type=0` for MetaMask/hardware wallet
- `signature_type=1` for email/Magic wallet

### 3. API Credentials

Always set API credentials before trading:

```python
client.set_api_creds(client.create_or_derive_api_creds())
```

### 4. Be a Maker

- Post limit orders (GTC), don't cross the spread
- Maker orders earn rebates, taker orders pay fees
- Use `OrderType.GTC` for resting limit orders

### 5. Price Conventions

- Prices are 0.00 to 1.00 (probability/dollar)
- YES + NO should sum to ~$1.00
- Minimum tick size is usually 0.01

### 6. Token IDs

- Markets have `condition_id` (market identifier)
- Each outcome has `token_id` (YES token, NO token)
- Use token IDs for trading, condition IDs for market lookup

## Key Files to Understand

1. **`src/config.py`**: Configuration and ClobClient initialization
2. **`src/orchestrator.py`**: Main trading loop and component coordination
3. **`src/pricing/fair_value.py`**: Binary options pricing model
4. **`src/quoting/quote_engine.py`**: Quote generation with inventory adjustment
5. **`src/execution/order_manager.py`**: Order lifecycle management
6. **`src/risk/risk_manager.py`**: Risk limit enforcement

## Common Tasks

### Adding a New Feature

1. Identify which module(s) need changes
2. Follow existing patterns in that module
3. Add tests in `tests/`
4. Update configuration if needed
5. Test in paper mode first

### Modifying Pricing Logic

Edit `src/pricing/fair_value.py`:
- `FairValueModel.calculate_fair_value()` for probability calculation
- Uses normal distribution CDF for binary option pricing
- Inputs: current_price, strike_price, time_remaining, volatility

### Modifying Quote Logic

Edit `src/quoting/quote_engine.py`:
- `QuoteEngine.generate_quotes()` for quote generation
- Inventory adjustment in `spread_calc.py`
- Size decay in `SizeCalculator`

### Modifying Risk Limits

Edit `src/risk/risk_manager.py`:
- `RiskLimits` dataclass for limit configuration
- `can_trade()` for trading permission checks
- `can_open_position()` for position-specific checks

### Adding New API Endpoints

Use the official client when possible. For Gamma API:
```python
# In src/market/gamma_client.py
async def new_endpoint(self, ...):
    return await self._request("GET", "/endpoint", params={...})
```

## API Reference

### Polymarket APIs

- **CLOB API** (`https://clob.polymarket.com`): Trading, orders, orderbook
- **Gamma API** (`https://gamma-api.polymarket.com`): Market discovery, metadata
- **Market WebSocket** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`): Orderbook updates
- **User WebSocket** (`wss://ws-subscriptions-clob.polymarket.com/ws/user`): Order/fill updates

### Contract Addresses (Polygon)

```
USDC: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
CTF: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
Exchange: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
Chain ID: 137
```

## Environment Variables

Required for live trading:
- `POLY_PRIVATE_KEY`: Ethereum private key (no 0x prefix)
- `POLY_SAFE_ADDRESS`: Polymarket Safe address

Optional:
- `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`: For authenticated endpoints
- `MIN_EDGE_THRESHOLD`: Minimum spread to trade (default: 0.02)
- `MAX_POSITION_SIZE`: Max USD per side (default: 500)
- `PAPER_TRADING`: Enable paper trading (default: true)

## Testing Guidelines

- Unit tests for pricing, quoting, and risk logic
- Mock the CLOB client for order manager tests
- Test edge cases (expired markets, extreme prices, zero volatility)
- Always test in paper mode before live deployment

## Deployment

### Docker

```bash
docker-compose up -d
docker-compose logs -f bot
```

### Manual

```bash
python -m src.main --live
```

## Troubleshooting

### "Missing credentials" error
- Set `POLY_PRIVATE_KEY` and `POLY_SAFE_ADDRESS` in `.env`

### WebSocket disconnects
- Automatic reconnection with exponential backoff
- Check network connectivity

### Orders not filling
- Check if prices are competitive (compare to orderbook)
- Verify minimum order size requirements
- Check API rate limits

### "No market found" warning
- Hourly markets may not exist during off-hours
- Wait for next market to be created

## Safety Notes

- **ALWAYS** start with `PAPER_TRADING=true`
- **NEVER** commit credentials to git
- Risk only what you can afford to lose
- Monitor the bot during initial live trading
