"""Simple close-to-close long-only backtest for research signals.

This is deliberately transparent and conservative: signals are evaluated on a
completed candle and a trade is entered at the next candle's open. Stop and
target checks use candle high/low; if both are touched in one candle, stop is
assumed first. Fees are charged on entry and exit. This is not a full exchange
simulator and does not model order-book slippage or partial fills.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .strategy import analyze_candles


@dataclass
class BacktestResult:
    trades: list[dict]
    starting_cash: float
    ending_cash: float
    net_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    fees_paid: float

    def to_dict(self) -> dict:
        return asdict(self)


def run_backtest(
    candles: pd.DataFrame,
    *,
    symbol: str = "TEST/USDT",
    timeframe: str = "1h",
    starting_cash: float = 10_000.0,
    risk_fraction: float = 0.01,
    fee_rate: float = 0.001,
    reward_risk: float = 2.0,
    signal_threshold: int = 70,
    warmup: int = 60,
) -> BacktestResult:
    """Run a single-position backtest on chronological OHLCV candles."""
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(candles.columns):
        raise ValueError(f"candles must include {sorted(required)}")
    if starting_cash <= 0 or not 0 < risk_fraction <= 0.05:
        raise ValueError("starting_cash must be positive and risk_fraction in (0, 0.05]")
    if not 0 <= fee_rate < 0.05:
        raise ValueError("fee_rate must be in [0, 0.05)")
    if reward_risk <= 0 or warmup < 60:
        raise ValueError("reward_risk must be positive and warmup >= 60")

    frame = candles.dropna(subset=list(required)).reset_index(drop=True)
    if len(frame) <= warmup + 2:
        raise ValueError("not enough candles for backtest and warmup")

    cash = float(starting_cash)
    equity_curve = [cash]
    trades: list[dict] = []
    fees_paid = 0.0
    position: dict | None = None

    for i in range(warmup, len(frame) - 1):
        candle = frame.iloc[i]
        if position:
            low, high = float(candle["low"]), float(candle["high"])
            exit_price = None
            exit_reason = None
            if low <= position["stop"]:
                exit_price, exit_reason = position["stop"], "stop"
            elif high >= position["target"]:
                exit_price, exit_reason = position["target"], "target"
            if exit_price is not None:
                gross = (exit_price - position["entry"]) * position["quantity"]
                exit_fee = exit_price * position["quantity"] * fee_rate
                net = gross - exit_fee - position["entry_fee"]
                cash += gross - exit_fee
                fees_paid += exit_fee
                trades.append({
                    **position,
                    "exit_index": i,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "gross_pnl": gross,
                    "net_pnl": net,
                    "exit_fee": exit_fee,
                    "cash_after": cash,
                })
                position = None

        if position is None and i >= warmup:
            history = frame.iloc[: i + 1]
            try:
                signal = analyze_candles(
                    symbol, timeframe, history, reward_risk=reward_risk
                )
            except (ValueError, KeyError, TypeError):
                signal = None
            if signal and signal.signal == "LONG_SETUP" and signal.score >= signal_threshold:
                next_open = float(frame.iloc[i + 1]["open"])
                stop = signal.stop_loss
                if next_open > stop and cash > 0:
                    risk_budget = cash * risk_fraction
                    qty = min(risk_budget / (next_open - stop), cash / (next_open * (1 + fee_rate)))
                    if qty > 0:
                        entry_fee = next_open * qty * fee_rate
                        cash -= entry_fee
                        fees_paid += entry_fee
                        position = {
                            "symbol": symbol,
                            "entry_index": i + 1,
                            "entry": next_open,
                            "quantity": qty,
                            "stop": stop,
                            "target": next_open + reward_risk * (next_open - stop),
                            "entry_fee": entry_fee,
                        }

        mark = cash
        if position:
            mark += (float(candle["close"]) - position["entry"]) * position["quantity"]
        equity_curve.append(mark)

    # Close any remaining position at the final close for a complete report.
    if position:
        last = frame.iloc[-1]
        exit_price = float(last["close"])
        gross = (exit_price - position["entry"]) * position["quantity"]
        exit_fee = exit_price * position["quantity"] * fee_rate
        net = gross - exit_fee - position["entry_fee"]
        cash += gross - exit_fee
        fees_paid += exit_fee
        trades.append({
            **position, "exit_index": len(frame) - 1, "exit_price": exit_price,
            "exit_reason": "end_of_data", "gross_pnl": gross, "net_pnl": net,
            "exit_fee": exit_fee, "cash_after": cash,
        })

    equity_curve.append(cash)
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak)
    winners = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losers = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    gross_win, gross_loss = sum(winners), abs(sum(losers))
    return BacktestResult(
        trades=trades,
        starting_cash=starting_cash,
        ending_cash=cash,
        net_return_pct=(cash / starting_cash - 1) * 100,
        max_drawdown_pct=max_dd * 100,
        win_rate_pct=(len(winners) / len(trades) * 100) if trades else 0.0,
        profit_factor=(gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0),
        fees_paid=fees_paid,
    )
