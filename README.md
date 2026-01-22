# Polymarket Hourly BTC Market Maker

A production-grade market making bot for Polymarket's hourly Bitcoin UP/DOWN prediction markets.

## Overview

This bot provides liquidity on both YES and NO sides of Polymarket's hourly BTC prediction markets, capturing the spread when both orders fill for guaranteed profit.

### How It Works

1. Polymarket has hourly markets: "Will BTC be above $X at Y:00?"
2. Each market has YES tokens and NO tokens
3. YES + NO should equal $1.00, but retail mispricing creates spreads
4. We quote BOTH sides - when both fill, we're hedged with guaranteed profit

### Example Trade

```
Market: "BTC above $97,500 at 3:00 PM?"

Our quotes:
  YES bid at $0.48
  NO bid at $0.47

If both fill:
  Total cost: $0.48 + $0.47 = $0.95
  Guaranteed payout: $1.00 (one side WILL win)
  Profit: $0.05 (5.3% return)
```

## Installation

### Prerequisites

- Python 3.11+
- Polymarket account with funds
- API credentials from Polymarket

### Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/polymarket-hourly-mm.git
cd polymarket-hourly-mm

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Configuration

Edit `.env` with your credentials:

```bash
# Required for live trading
POLY_PRIVATE_KEY=your_private_key_here
POLY_SAFE_ADDRESS=your_safe_address_here

# Trading parameters
MIN_EDGE_THRESHOLD=0.02
MAX_POSITION_SIZE=500
MAX_TOTAL_EXPOSURE=5000

# Start in paper mode
PAPER_TRADING=true
```

## Usage

### Paper Trading (Recommended for Testing)

```bash
python -m src.main --paper
```

### Live Trading

```bash
python -m src.main --live
```

### With Custom Config

```bash
python -m src.main --config config/production.yaml --live
```

### Docker

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f bot

# Stop
docker-compose down
```

## Architecture

```
src/
├── main.py              # Entry point
├── orchestrator.py      # Main controller
├── config.py            # Configuration
├── market/              # Market discovery
├── pricing/             # Fair value models
├── quoting/             # Quote generation
├── execution/           # Order management
├── risk/                # Risk management
├── data/                # WebSocket/orderbook
└── utils/               # Helpers
```

## Key Components

- **MarketDiscovery**: Finds active hourly BTC markets via Gamma API
- **PricingEngine**: Calculates fair value using volatility-based model
- **QuoteEngine**: Generates bid/ask quotes with inventory adjustment
- **OrderManager**: Places and manages orders via py-clob-client
- **RiskManager**: Enforces position limits and drawdown controls
- **Orchestrator**: Coordinates all components in the main loop

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_fair_value.py
```

## Risk Management

The bot enforces multiple risk limits:

- **Position Size**: Maximum USD per trade
- **Total Exposure**: Maximum total USD in positions
- **Drawdown**: Stop trading if losses exceed threshold
- **Inventory Skew**: Maximum imbalance before aggressive hedging
- **Time Remaining**: Don't enter near market expiry
- **Rate Limiting**: Maximum trades per minute

## Strategy Logic

1. Every 2 seconds:
   - Get current BTC price from Binance
   - Calculate fair value using volatility model
   - Check if edge >= minimum threshold
   - Generate quotes around fair value
   - Adjust for inventory skew
   - Place/update orders

2. On fill:
   - Update inventory
   - Check if hedged
   - Calculate guaranteed profit

3. On market expiry:
   - Cancel all orders
   - Rotate to next hourly market

## Important Notes

- **ALWAYS** start with paper trading
- **NEVER** risk more than you can afford to lose
- This is experimental software - use at your own risk
- Market making involves financial risk

## Dependencies

- `py-clob-client`: Official Polymarket Python client
- `httpx`: HTTP client for API requests
- `websocket-client`: WebSocket connections
- `scipy`: Statistical functions for pricing
- `structlog`: Structured logging

## License

MIT License - see LICENSE file for details.

## Disclaimer

This software is for educational purposes only. Trading prediction markets involves significant financial risk. The authors are not responsible for any losses incurred through use of this software.
