# Binance Spot Demo Trading

This mode uses Binance's **Spot Demo Trading** API and the connected demo
account's balances. It does not create a local pretend wallet. The optional
allocation cap only limits how much quote currency this strategy may commit
to entry positions.

## Credentials

Create API credentials from Binance Demo Trading's API management page. Do not
paste keys into chat or commit them to GitHub. Set them locally.

PowerShell:
```powershell
$env:BINANCE_DEMO_API_KEY="your-demo-api-key"
$env:BINANCE_DEMO_API_SECRET="your-demo-api-secret"
```

macOS/Linux:
```bash
export BINANCE_DEMO_API_KEY="your-demo-api-key"
export BINANCE_DEMO_API_SECRET="your-demo-api-secret"
```

The adapter requires CCXT's `enable_demo_trading(True)` support. It fails
closed if that method is missing and does not fall back to generic sandbox or
live API routing. Upgrade the project's dependencies if your installed CCXT
does not provide Binance Demo Trading support.

## Verify the connection first

```bash
python -m marketbot demo-balance
```

This should display the balances from your Binance Spot Demo account.

## Automatic scanner and demo trading

Start the loop:

```bash
python -m marketbot demo-auto --quote USDT --timeframe 1h --top 50 --interval 300 --max-allocation 1000
```

The bot scans public Spot markets for `LONG_SETUP` signals, checks the connected
account's actual free quote balance, and submits market orders only through
Binance Demo Trading. The default 1,000 USDT is a strategy entry-notional cap,
not a separate wallet or a claim that the exchange account contains 1,000 USDT.
Use `--max-allocation` to change that cap. The bot tracks confirmed fills and
its own positions in `data/binance_demo_trader.sqlite3`.

Stop the loop with Ctrl+C. Order/position history is stored locally in SQLite.

## Important limitations

- This is demo-only. Do not use live API credentials.
- Stop-loss and take-profit checks are polling-based: the bot checks prices on
  each cycle and submits a market sell when a threshold is crossed. These are
  **not exchange-hosted protective orders** and may execute late or at a worse
  price, especially if the process or network is down.
- A market order can partially fill or fail. The bot records confirmed fills
  only; review demo order history and local SQLite records.
- The strategy is experimental and has not been validated for profitability.
- Existing `paper` mode is a separate local SQLite simulation. It is not the
  Binance Demo account.
