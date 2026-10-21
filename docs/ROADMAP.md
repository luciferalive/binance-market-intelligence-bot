# Roadmap and safety gates

## Current project boundary

This repository is a Binance Spot market research/scanning project. The scan command uses public market data. The backtest command consumes historical OHLCV CSV data. Risk helpers calculate hypothetical position sizes and reject proposals that exceed configured portfolio guardrails.

There is no authenticated exchange connection, no live order placement, no account balance access, and no autonomous trading loop in this version.

## Upgrade sequence

1. **Data reliability**
   - Validate timestamps, duplicates, missing candles, market status, exchange precision and minimum-notional metadata.
   - Add a local OHLCV cache and explicit API retry/backoff.
   - Record scan provenance, data timestamp and strategy version with each signal.

2. **Research and strategy evaluation**
   - Add benchmark-relative strength and multiple timeframe context.
   - Separate feature calculation from strategy decisions.
   - Use walk-forward testing and out-of-sample holdouts.
   - Report fees, slippage assumptions, exposure, expectancy, profit factor and drawdown.
   - Compare against simple buy-and-hold and cash baselines; avoid selecting parameters from the test period.

3. **Paper trading**
   - Create a persistent simulated portfolio with order/position state.
   - Simulate exchange filters, fees, spread, latency, partial fills and rejected orders.
   - Reconcile every simulated trade and make runs reproducible.
   - Require a meaningful period of paper-trading evidence before considering live execution.

4. **Exchange adapter (future, disabled by default)**
   - Isolate private API access in a separate adapter.
   - Use least-privilege API keys with withdrawals disabled.
   - Require explicit user opt-in, dry-run default, symbol allowlists and order-size caps.
   - Add idempotent client order IDs, reconciliation, timeout handling and a global kill switch.
   - Never commit secrets; load them from environment variables or a secret manager.

## Known limitations

- A backtest is a model, not proof of future returns.
- OHLC candles cannot reveal exact intrabar order. This backtest assumes stop-loss before target if both are touched in one candle.
- Market gaps, spread, slippage, exchange outages and partial fills can worsen results.
- A technical signal cannot reliably identify or guarantee avoidance of rug pulls or sudden crashes.
- The strategy score is a rule score, not a calibrated win probability.
