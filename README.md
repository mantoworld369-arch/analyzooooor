# Analyzoooor 🔍

**Token Holder Analytics Tool for Base, BSC & Solana**

Paste a contract address and get detailed analytics on the top 500 holders — wallet balances, buy/sell activity, PnL estimates, diamond hands detection, and more.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Interactive mode
python main.py

# Direct mode
python main.py <CONTRACT_ADDRESS> --chain base
python main.py <CONTRACT_ADDRESS> --chain bsc
python main.py <CONTRACT_ADDRESS> --chain solana

# Custom options
python main.py <CA> --chain base --top 100 --output my_report.csv
```

## What It Outputs

### Terminal (rich table)
- Top 50 holders with: rank, wallet, balance, % supply, USD value, buys, sells, profit status, wallet type

### CSV Export (full data)
- All analyzed holders with every metric
- Entry/exit prices, PnL breakdown, timestamps, diamond hands flag

## Metrics Per Wallet

| Metric | Description |
|--------|-------------|
| Balance | Current token holding |
| % Supply | Percentage of total supply held |
| Holding Value | Current USD value of tokens held |
| Buys / Sells | Number of buy & sell transactions |
| Total Bought / Sold | Token amounts bought and sold |
| Avg Entry Price | Weighted average buy price* |
| Avg Exit Price | Weighted average sell price* |
| Realized PnL | Profit/loss from completed sells* |
| Unrealized PnL | Paper profit/loss on current holdings* |
| Diamond Hands | Wallet has never sold |
| First Buy / Last Activity | Timestamps |

*\*Entry/exit prices and PnL require historical price data. See "Limitations" below.*

## Architecture

```
main.py                  # CLI entry point
├── utils.py             # Chain detection, DexScreener API, formatting
├── output_formatter.py  # Rich tables + CSV export
└── chains/
    ├── base_analyzer.py # Abstract base class
    ├── evm_analyzer.py  # Shared EVM logic (Etherscan-compatible)
    ├── base_chain.py    # Base → Basescan
    ├── bsc_chain.py     # BSC → BscScan
    └── solana_chain.py  # Solana → Solscan
```

## Data Sources (all free, no API key needed)

| Source | Used For |
|--------|----------|
| [DexScreener](https://dexscreener.com) | Current price, token metadata, chain detection |
| [Basescan](https://basescan.org) | Base token transfers, holder reconstruction |
| [BscScan](https://bscscan.com) | BSC token transfers, holder reconstruction |
| [Solscan](https://solscan.io) | Solana holders list, SPL transfers |

## Known Limitations & Upgrade Path

### Current Limitations (free tier)

1. **Entry/Exit Prices**: Computing accurate avg entry/exit prices requires historical price data at each transaction's block/timestamp. Free APIs don't provide this. Currently shows current price as placeholder.

2. **1-Month PnL**: Same issue — needs historical price oracle.

3. **Rate Limits**: Free API tiers limit us to ~4-5 requests/second. Analyzing 500 wallets takes 2-5 minutes.

4. **EVM Holder List**: Etherscan doesn't have a "top holders" endpoint. We reconstruct balances from transfer events, which means we need to fetch ALL transfers for the token. For very popular tokens (millions of transfers), this is slow.

### Upgrade Path (with API keys)

| Upgrade | Benefit |
|---------|---------|
| Basescan/BscScan API key | 5 req/s → much faster analysis |
| Moralis API | Direct holder endpoints + historical balances |
| Helius API (Solana) | Parsed transaction history with swap details |
| Birdeye API | Historical OHLCV data for accurate entry prices |
| DeFiLlama API | Historical TVL and price feeds |
| GMGN API | Pre-computed holder PnL (if available) |

To add an API key, set environment variables:
```bash
export BASESCAN_API_KEY="your_key"
export BSCSCAN_API_KEY="your_key"
export HELIUS_API_KEY="your_key"
```

## Adding API Key Support

The codebase is designed for easy extension. To add API key support:

1. Read the key from env: `os.environ.get("BASESCAN_API_KEY", "")`
2. Append `&apikey=YOUR_KEY` to explorer API calls
3. Reduce `api_delay` to `0.22` (5 req/s with key)

## License

MIT — use it, fork it, improve it.
