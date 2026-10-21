# Binance Market Intelligence Bot

**A research-first Binance Spot scanner with a connected-account demo trading mode.**

Scan a changing universe of Binance Spot pairs, rank setups using technical signals, and optionally route demo orders through your **Binance Spot Demo account**. The exchange account is the source of truth for balances. No pretend wallet, no live-trading command.

> [!WARNING]
> Experimental software, not financial advice. Signals are rules-based hypotheses, not predictions or a claim of profitability. Demo exits are polling-based, not exchange-hosted protective orders. The bot can miss an exit or receive a worse price if it is offline or markets move quickly.

## What it does

| Feature | Details |
| --- | --- |
| Market discovery | Finds eligible Binance Spot quote pairs dynamically |
| Signal analysis | Trend, momentum, breakout, relative volume, and ATR-based risk levels |
| Research output | Ranked **WATCH / LONG_SETUP / NO_SETUP** signals and CSV export |
| Backtesting | Historical candles with fees and conservative stop/target handling |
| Binance Demo | Reads connected demo balances and can submit demo orders |
| Risk controls | Strategy allocation cap and position tracking for demo execution |

## Quick start

Requires **Python 3.11+** and internet access for Binance public market data.

```bash
git clone https://github.com/luciferalive/binance-market-intelligence-bot.git
cd binance-market-intelligence-bot
python -m venv .venv
```

Activate the environment:

**Windows PowerShell**
```powershell
.venv\\Scripts\\Activate.ps1
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

Install:
```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Scan the market

Scan USDT pairs on the 1-hour timeframe and save ranked results:
```bash
python -m marketbot scan --quote USDT --timeframe 1h --top 80 --output reports/signals.csv
```

Smaller smoke test:
```bash
python -m marketbot scan --quote USDT --timeframe 1h --top 20 --output signals.csv
```

The scanner prioritizes markets by 24-hour quote volume before fetching candles to reduce API load. Binance public endpoints have rate limits; avoid aggressive polling.

## Connect Binance Spot Demo

1. Create **Binance Spot Demo Trading** API credentials in Binance.
2. Set them locally as environment variables. Do not paste keys into chat, commit them, or use live-account keys.

**Windows PowerShell**
```powershell
$env:BINANCE_DEMO_API_KEY="your_demo_key"
$env:BINANCE_DEMO_API_SECRET="your_demo_secret"
```

**macOS / Linux**
```bash
export BINANCE_DEMO_API_KEY="your_demo_key"
export BINANCE_DEMO_API_SECRET="your_demo_secret"
```

Check the connection and inspect the balance returned by the connected demo account:
```bash
python -m marketbot demo-balance
```

Start the automatic demo loop:
```bash
python -m marketbot demo-auto --quote USDT --timeframe 1h --top 50 --interval 300 --max-allocation 1000
```

Stop it with **Ctrl+C**.

### Allocation is a cap, not a wallet

`--max-allocation 1000` limits the strategy's entry notional. It does **not** create or claim a separate 1,000 USDT balance. The bot reads the connected Binance Demo account's actual balances and submits demo orders through that account. Your demo account must have sufficient virtual funds and supported market permissions.

Read [Binance Spot Demo details and limitations](docs/BINANCE_SPOT_DEMO.md) before running the loop. Demo trading is not live trading, and the polling-based exit logic is not a guaranteed stop-loss.

## How signals are calculated

The baseline score uses:
- **Trend:** EMA 20/50 alignment and price relative to EMA 20
- **Momentum:** RSI range and recent returns
- **Breakout:** close relative to the previous rolling high (current candle excluded)
- **Relative volume:** closed-candle volume against a rolling average
- **Volatility:** ATR as a percentage of price for estimated risk levels

Signals are based on closed candles to avoid using unfinished candle values. A high score means the market matches the coded rules; it is not a calibrated win probability. Fees, spreads, slippage, gaps, and exchange constraints can materially change results.

## Commands

| Command | Purpose |
| --- | --- |
| `scan` | Scan and rank market signals |
| `backtest` | Run historical strategy evaluation |
| `paper` / `paper-status` | Use the project's separate local SQLite paper simulator |
| `demo-balance` | Read balance from connected Binance Spot Demo account |
| `demo-order SYMBOL SIDE AMOUNT` | Manually submit a demo order |
| `demo-auto` | Run automatic demo signal/order loop |

The local `paper` simulator and Binance Demo account are distinct. See CLI help for current options:
```bash
python -m marketbot --help
python -m marketbot demo-auto --help
```

## Safety and current limitations

- No live order execution, futures, margin, leverage, or short selling.
- Demo stop/target monitoring is polling-based; no exchange-hosted protective orders.
- No news/social sentiment, on-chain data, or token fundamental analysis.
- No guaranteed pump, rug-pull, or loss prediction.
- Strategy profitability has not been established.

Before considering any real-money system, require out-of-sample and walk-forward testing across market regimes; realistic fees/spread/slippage; fixed-risk position sizing; daily-loss, open-risk, and correlation limits; robust order reconciliation/retries; a kill switch; and extended demo evaluation. Keep API keys withdrawal-disabled and out of source control.

## Tests

Run the suite:
```bash
pytest
```

## License

MIT. See [LICENSE](LICENSE).
