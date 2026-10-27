# Binance Market Intelligence Bot

**A research-first Binance Spot scanner with a connected-account demo trading mode.**

Scan a changing universe of Binance Spot pairs, rank setups using technical signals, and optionally route demo orders through your **Binance Spot Demo account**. The exchange account is the source of truth for balances. No pretend wallet, no live-trading command.

> [!WARNING]
> Experimental software, not financial advice. Signals are rules-based hypotheses, not predictions or a claim of profitability. Demo exits are polling-based, not exchange-hosted protective orders. The bot can miss an exit or receive a worse price if it is offline or markets move quickly.

## Market Desk dashboard (new)

One local web page with four tabs: your **Binance portfolio**, **trending meme coins with rug pull checks**, a **pump and dump watch** on Binance Spot, and a **token checker** you can paste any contract address into.

```bash
pip install -e ".[dev]"
python -m marketbot dashboard          # then open http://127.0.0.1:8765
```

| Tab | What you get |
| --- | --- |
| Portfolio | Balances, total value, 24h change, allocation, average cost, realized and unrealized PnL, concentration warnings |
| Meme coins | New and promoted tokens (DexScreener) ranked by **heat**, each with a **rug risk** level, a plain-language reason list, and a one-click news and company-interest lookup. Also CoinGecko trending and established memes, marked "on Binance" or not |
| Pump & dump watch | Binance USDT markets labelled **Pump starting / Building / Already extended / Dump risk / Dumping** from volume, buying share, RSI and distance from the peak |
| Check a token | Name, ticker or contract address in, risk checks and evidence out |

**How to read it.** *Heat* measures attention and momentum only. *Rug risk* comes from contract checks (honeypot simulation, mint and ownership powers, taxes, liquidity locks, holder concentration) plus market checks (thin liquidity, brand-new pair). A coin can be both hot and a trap, so they are two separate numbers. Missing data raises risk and never produces "Low". Even "Low" means only that none of the checks we can run found a problem. Nothing here predicts a pump, and the project never says "buy".

**Portfolio setup (read-only).**

1. In Binance open **Account > API Management**, create a key, and enable **Enable Reading only**. Keep trading, withdrawals and transfers off, and restrict the key to your IP.
2. Copy `.env.example` to `.env` and set `BINANCE_API_KEY` and `BINANCE_API_SECRET`. `.env` is git-ignored.
3. Restart the dashboard.

The portfolio client can only send GET requests and has no order or withdrawal code at all. On every load it asks Binance what the key may do and **refuses to run if withdrawals are enabled**. The dashboard only listens on `127.0.0.1`, because it has no login.

PnL uses average cost from your Binance spot trade history (USDT, USDC and FDUSD pairs). Coins that arrived by deposit, Earn, Convert or airdrop have no trade record, so the table shows how much of each position the history explains.

Terminal versions of each tab:

```bash
python -m marketbot memes --top 15
python -m marketbot check PEPE            # or a contract address
python -m marketbot pumps --memes-only
python -m marketbot news "Bonk"
python -m marketbot portfolio
```

Data sources are free and need no key: DexScreener, GoPlus Security, CoinGecko (an optional free key in `.env` raises its rate limit), Google News RSS and Binance public endpoints. Each panel is cached for a few minutes to stay inside free rate limits.

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
- News is keyword-tagged headlines only: no social sentiment, no wallet-level on-chain forensics (for example fresh-wallet team dumps), and no token fundamental analysis.
- Rug checks remove obvious traps. They cannot catch social-engineering rugs, brand-new exploits, or insiders who sell slowly. Meme coins can go to zero even when every check passes.
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
