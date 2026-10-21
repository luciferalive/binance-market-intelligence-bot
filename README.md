# Binance Market Intelligence Bot

Research-first Binance Spot market scanner that ranks liquid USDT markets using trend, momentum, breakout, volatility, and relative-volume features. It produces explainable **WATCH / LONG_SETUP / NO_SETUP** research signals with proposed stop and target levels.

> **Risk notice:** This project is experimental research software, not financial advice and not a profit guarantee. It does not place orders, connect to private account endpoints, or require API keys. Signals are hypotheses that must be validated with realistic backtests and paper trading.

## Current scope

- Public Binance Spot market data only; no credentials required.
- Discovers eligible USDT spot pairs dynamically from exchange metadata.
- Filters leveraged tokens and stablecoin/stablecoin pairs where practical.
- Scores trend, momentum, breakout, and relative volume using closed candles.
- Calculates ATR-based stop distance and a configurable reward/risk target.
- Exports ranked signals to CSV.
- Includes unit tests for indicators and signal generation.

## Not included yet

- Live order execution or private account connection.
- Futures, margin, leverage, or short selling.
- News/social sentiment, on-chain data, or token fundamental analysis.
- Guaranteed rug-pull, pump, or loss prediction.
- A claim of profitability. The baseline strategy is not validated.

## Requirements

- Python 3.11+
- Internet connection to query Binance public market-data endpoints

## Install

```bash
git clone https://github.com/luciferalive/binance-market-intelligence-bot.git
cd binance-market-intelligence-bot
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Run a scan

```bash
python -m marketbot scan --quote USDT --timeframe 1h --top 80 --output reports/signals.csv
```

For a quicker smoke test, scan fewer markets:

```bash
python -m marketbot scan --quote USDT --timeframe 1h --top 20 --output signals.csv
```

The scanner ranks pairs by 24-hour quote volume before fetching candle history, to limit public API usage. Results are printed and written to CSV. Binance public endpoints have rate limits; do not run aggressive polling loops.

## Signal logic (baseline, not a recommendation)

The score combines:
- Trend alignment: EMA 20 versus EMA 50 and closing price versus EMA 20.
- Momentum: RSI range and recent returns.
- Breakout: close relative to the prior rolling high, excluding the current candle.
- Relative volume: current closed-candle volume compared with a rolling average.
- Volatility: ATR as a percentage of price, used to estimate risk levels.

A high score means the features match the coded rules. It is **not** a calibrated probability that a trade will win. Signals use closed candles to avoid acting on an unfinished candle. Stop/target prices are estimates; gaps, fees, slippage, and execution constraints can change realized outcomes.

## Risk principles for future execution work

Before enabling any real trading, add and test:
1. Walk-forward and out-of-sample backtesting across multiple market regimes.
2. Trading fees, spread, slippage, latency, and exchange filters in simulation.
3. Position sizing from a fixed fraction of equity and stop distance.
4. Limits on daily loss, total open risk, correlated exposure, and simultaneous positions.
5. Persistent order/position state, reconciliation, retries, and a kill switch.
6. Paper trading over a meaningful period and review of drawdown and risk-adjusted returns.
7. API keys with withdrawals disabled; never commit secrets to Git.

The initial release deliberately has **no live trading path**. Do not add private API credentials to this project.

## Tests

```bash
pytest
```

## Repository topics

`cryptocurrency`, `binance`, `trading-bot`, `market-scanner`, `technical-analysis`, `python`, `algorithmic-trading`, `risk-management`, `backtesting`

## License

MIT. See [LICENSE](LICENSE).
