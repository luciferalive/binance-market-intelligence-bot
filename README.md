# Binance Market Intelligence Bot

Research-first Binance Spot market scanner that ranks liquid USDT markets using trend, momentum, breakout, volatility, and relative-volume features. It produces explainable **WATCH / LONG_SETUP / NO_SETUP** research signals with proposed stop and target levels.

> **Risk notice:** This is experimental research software, not financial advice or a profit guarantee. It includes a Binance Spot Demo Trading adapter and automatic demo-order loop, but no live-trading command. Signals are hypotheses and the strategy is not validated for profitability..

## Current scope

- Public Binance Spot market data for scanning; no credentials required for research scans.
- Optional private API connection to Binance Spot Demo Trading using demo-specific credentials only.
- Automatic demo trading reads the connected demo account's actual balances and records confirmed fills locally.
- A configurable strategy allocation cap limits entry notional; it is not a fabricated or separate wallet.
- Discovers eligible USDT spot pairs dynamically from exchange metadata.
- Filters leveraged tokens and stablecoin/stablecoin pairs where practical.
- Scores trend, momentum, breakout, and relative volume using closed candles.
- Calculates ATR-based stop distance and a configurable reward/risk target.
- Exports ranked signals to CSV
- Includes a historical-candle backtest CLI with fees and conservative stop/target handling
- Includes position-sizing helpers and portfolio exposure/daily-loss guardrails (not connected to execution).
- Includes unit tests for indicators and signal generation.

## Not included

- Live order execution, futures, margin, leverage, or short selling.
- Exchange-hosted stop-loss/take-profit orders. Demo exits are polling-based and can execute late or at worse prices.
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

## Binance Spot Demo Trading

Create demo API credentials in Binance Spot Demo Trading and set
`BINANCE_DEMO_API_KEY` and `BINANCE_DEMO_API_SECRET` in your local environment.
Never paste them into chat or commit them to Git. Verify the connection and
read the exchange's actual demo balance:

```bash
python -m marketbot demo-balance
```

Run automatic demo trading:

```bash
python -m marketbot demo-auto --quote USDT --timeframe 1h --top 50 --interval 300 --max-allocation 1000
```

The 1,000 USDT default is an entry-notional cap for this strategy, not a
separate wallet. The bot submits orders only after explicitly enabling CCXT's
Binance Demo Trading mode and fails closed if that capability is unavailable.
Stop the loop with Ctrl+C. See [the demo setup and limitations](docs/BINANCE_SPOT_DEMO.md),
especially the warning that protective exits are polling-based rather than
exchange-hosted.

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

The project deliberately has **no live trading path**. Never use live Binance credentials with the demo commands; demo credentials belong only in local environment variables..

## Tests

```bash
pytest
```

## Repository topics

`cryptocurrency`, `binance`, `trading-bot`, `market-scanner`, `technical-analysis`, `python`, `algorithmic-trading`, `risk-management`, `backtesting`

## License

MIT. See [LICENSE](LICENSE).
