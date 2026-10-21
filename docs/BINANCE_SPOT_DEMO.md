# Binance Spot Demo API setup

This mode is intended for Binance Spot Demo Trading only. It does not use live
account credentials or live order endpoints.

## 1. Create demo credentials

In Binance, open **Demo Trading**, select **Spot**, then use the Demo API
Management page to create demo API credentials. Do not paste credentials into
GitHub, issues, or chat.

Set them in your local shell (PowerShell):

```powershell
$env:BINANCE_DEMO_API_KEY="your-demo-api-key"
$env:BINANCE_DEMO_API_SECRET="your-demo-api-secret"
```

For macOS/Linux:

```bash
export BINANCE_DEMO_API_KEY="your-demo-api-key"
export BINANCE_DEMO_API_SECRET="your-demo-api-secret"
```

## 2. Install and check the demo account

From the project root:

```bash
pip install -e .
marketbot demo-balance
```

The command uses CCXT sandbox mode and displays the demo account's virtual
balances. Demo assets are provided by Binance; this command does not mint or
reset a balance.

## 3. Place a manual demo order

Example: buy 0.001 BTC using the demo Spot account:

```bash
marketbot demo-order BTC/USDT buy 0.001
```

Sell example:

```bash
marketbot demo-order BTC/USDT sell 0.001
```

The amount is in the base asset (BTC in BTC/USDT), not USDT. Orders are
sandbox-only and are rejected if the market is not listed in the demo exchange.

## Current scope and safety

- `demo-balance` reads the demo wallet.
- `demo-order` is a manual sandbox order command.
- The existing `paper` command remains an independent local SQLite simulation.
- This initial demo integration does **not yet automatically turn scanner signals
  into orders**, and the 1,000 USDT strategy allocation/risk controls are not
  wired into demo orders yet. Do not run unattended orders until that layer,
  position tracking, stop logic, and tests are added.
- Never use live Binance API credentials with this project. Keep demo keys in
  local environment variables and out of version control.
