"""Explainable baseline signal scoring; not a validated profitable strategy."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .indicators import atr, ema, rsi


@dataclass
class Signal:
    symbol: str
    timeframe: str
    signal: str
    score: int
    close: float
    entry: float
    stop_loss: float
    target: float
    atr_pct: float
    quote_volume_24h: float
    reasons: str

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_candles(
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,
    quote_volume_24h: float = 0.0,
    breakout_period: int = 20,
    atr_multiple: float = 2.0,
    reward_risk: float = 2.0,
) -> Signal:
    """Analyze OHLCV data. Caller should pass completed candles only."""
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(candles.columns):
        raise ValueError(f"candles must include columns: {sorted(needed)}")
    data = candles.dropna(subset=list(needed)).copy()
    if len(data) < max(60, breakout_period + 2):
        raise ValueError("Not enough candles; provide at least 60 completed candles")

    close = data["close"].astype(float)
    high = data["high"].astype(float)
    volume = data["volume"].astype(float)
    fast = ema(close, 20)
    slow = ema(close, 50)
    momentum = rsi(close, 14)
    volatility = atr(data, 14)
    relative_volume = volume / volume.rolling(20).mean()
    prior_high = high.shift(1).rolling(breakout_period).max()

    # Use the latest row in the supplied data. The scanner drops the active candle.
    price = float(close.iloc[-1])
    atr_value = float(volatility.iloc[-1])
    rsi_value = float(momentum.iloc[-1])
    fast_value = float(fast.iloc[-1])
    slow_value = float(slow.iloc[-1])
    relvol_value = float(relative_volume.iloc[-1])
    prior_high_value = float(prior_high.iloc[-1])

    if not all(pd.notna(x) for x in [
        atr_value, rsi_value, fast_value, slow_value, relvol_value, prior_high_value
    ]) or price <= 0 or atr_value <= 0:
        raise ValueError("Indicators could not be calculated from supplied candles")

    score = 0
    reasons: list[str] = []
    if fast_value > slow_value:
        score += 25
        reasons.append("EMA20 above EMA50")
    if price > fast_value:
        score += 15
        reasons.append("close above EMA20")
    if 52 <= rsi_value <= 70:
        score += 15
        reasons.append(f"RSI momentum supportive ({rsi_value:.1f})")
    elif 45 <= rsi_value < 52:
        score += 5
        reasons.append(f"RSI neutral-to-positive ({rsi_value:.1f})")
    if price > prior_high_value:
        score += 25
        reasons.append(f"breakout above prior {breakout_period}-candle high")
    if relvol_value >= 1.5:
        score += 20
        reasons.append(f"relative volume elevated ({relvol_value:.2f}x)")
    elif relvol_value >= 1.1:
        score += 10
        reasons.append(f"relative volume rising ({relvol_value:.2f}x)")

    atr_pct = atr_value / price * 100
    if atr_pct > 8:
        score -= 15
        reasons.append(f"high volatility penalty ({atr_pct:.1f}% ATR)")
    if rsi_value > 78:
        score -= 20
        reasons.append(f"overheated RSI penalty ({rsi_value:.1f})")

    if score >= 70:
        label = "LONG_SETUP"
    elif score >= 40:
        label = "WATCH"
    else:
        label = "NO_SETUP"

    stop = max(price - atr_multiple * atr_value, price * 0.0001)
    target = price + reward_risk * (price - stop)
    if not reasons:
        reasons.append("no strong bullish conditions detected")
    return Signal(
        symbol=symbol,
        timeframe=timeframe,
        signal=label,
        score=max(0, min(100, int(score))),
        close=price,
        entry=price,
        stop_loss=stop,
        target=target,
        atr_pct=atr_pct,
        quote_volume_24h=float(quote_volume_24h),
        reasons="; ".join(reasons),
    )
