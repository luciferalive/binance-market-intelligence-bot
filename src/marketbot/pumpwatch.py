"""Pump and dump watch for Binance Spot USDT pairs.

Uses public Binance data only: 24h tickers to pick the active universe, then hourly candles
to measure relative volume, short-term returns, taker buy pressure (how much of the volume
was market *buys*), RSI and distance from the recent peak.

Labels (heuristics, not predictions):

* PUMP_STARTING  - volume well above normal, price rising, buyers aggressive, not yet extended.
* BUILDING       - quieter version of the above; worth watching.
* PUMP_EXTENDED - already moved a lot (24h or RSI). Late entries are where most people lose.
* DUMP_RISK      - extended move now losing steam: sellers appearing or price off the peak.
* DUMPING        - sharp drop on heavy volume.
* QUIET          - nothing unusual.

Nothing here can tell whether a move will continue. It tells you what is happening now.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from . import binance_public as bp
from .indicators import rsi
from .net import HttpError, get_json, to_float

FALLBACK_MEME_BASES = {
    "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BOME", "MEME", "TURBO", "NEIRO", "PNUT",
    "ACT", "POPCAT", "MOODENG", "GOAT", "TRUMP", "SATS", "BABYDOGE", "CAT", "DOGS", "HMSTR",
    "1MBABYDOGE", "PEOPLE", "CHEEMS", "SLERF", "MEW", "BRETT", "MOG", "SPX", "FARTCOIN",
}
LABEL_PRIORITY = {
    "PUMP_STARTING": 0, "DUMPING": 1, "DUMP_RISK": 2, "BUILDING": 3, "PUMP_EXTENDED": 4, "QUIET": 5,
}
MIN_CANDLES = 30


@dataclass
class PumpRow:
    symbol: str
    base: str
    is_meme: bool
    price: float
    change_24h_pct: float
    quote_volume_24h: float
    ret_1h_pct: float
    ret_4h_pct: float
    rel_volume: float
    taker_buy_ratio: float
    rsi: float
    drawdown_from_peak_pct: float
    label: str
    intensity: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def analyze_klines(
    symbol: str, base: str, raw: list[list], ticker: dict | None = None, is_meme: bool = False
) -> PumpRow | None:
    """Classify one market from raw Binance klines. The forming candle is ignored."""
    rows = raw[:-1]
    if len(rows) < MIN_CANDLES:
        return None
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    quote_volume = [float(r[7]) for r in rows]
    taker_buy_quote = [float(r[10]) for r in rows]
    if closes[-1] <= 0 or closes[-5] <= 0 or closes[-25] <= 0:
        return None

    ret_1h = closes[-1] / closes[-2] - 1
    ret_4h = closes[-1] / closes[-5] - 1
    ret_24h = closes[-1] / closes[-25] - 1
    baseline = _mean(quote_volume[-25:-1])
    rel_volume = quote_volume[-1] / baseline if baseline > 0 else 0.0
    recent_volume = sum(quote_volume[-4:])
    taker = sum(taker_buy_quote[-4:]) / recent_volume if recent_volume > 0 else 0.5
    peak = max(highs[-24:])
    drawdown = 1 - closes[-1] / peak if peak > 0 else 0.0
    rsi_now = float(rsi(pd.Series(closes), 14).iloc[-1])

    reasons: list[str] = []
    if ret_1h <= -0.08 and rel_volume >= 2:
        label = "DUMPING"
        reasons.append(f"fell {abs(ret_1h):.1%} in the last hour on {rel_volume:.1f}x normal volume")
    elif (ret_24h >= 0.20 or (rsi_now >= 75 and ret_24h >= 0.10)) and (
        drawdown >= 0.06 or ret_1h <= -0.03
    ) and (taker < 0.48 or rel_volume >= 2):
        label = "DUMP_RISK"
        reasons.append(f"ran up {ret_24h:.0%} in 24h and is now {drawdown:.1%} off its peak")
        if taker < 0.48:
            reasons.append(f"sellers are taking over (only {taker:.0%} of recent volume was buying)")
    elif ret_24h >= 0.25 or (rsi_now >= 80 and ret_24h >= 0.10):
        # RSI alone is not enough: one big candle after a calm spell spikes RSI, and that is
        # the early stage of a move, which PUMP_STARTING below should still be able to catch.
        label = "PUMP_EXTENDED"
        reasons.append(f"already up {ret_24h:.0%} in 24h (RSI {rsi_now:.0f}): late entries are risky")
    elif rel_volume >= 2.5 and 0.015 <= ret_1h <= 0.12 and taker >= 0.54:
        label = "PUMP_STARTING"
        reasons.append(f"volume {rel_volume:.1f}x normal with price up {ret_1h:.1%} in the last hour")
        reasons.append(f"buyers aggressive ({taker:.0%} of recent volume was buying)")
    elif rel_volume >= 1.8 and ret_4h >= 0.03 and taker >= 0.52:
        label = "BUILDING"
        reasons.append(f"volume {rel_volume:.1f}x normal and price up {ret_4h:.1%} over 4 hours")
    else:
        label = "QUIET"
        reasons.append("no unusual volume or price action")

    intensity = int(min(100, rel_volume * 15 + abs(ret_1h) * 400 + abs(ret_4h) * 150))
    change_24h = to_float((ticker or {}).get("priceChangePercent"), ret_24h * 100)
    return PumpRow(
        symbol=symbol,
        base=base,
        is_meme=is_meme,
        price=closes[-1],
        change_24h_pct=float(change_24h),
        quote_volume_24h=float(to_float((ticker or {}).get("quoteVolume"), sum(quote_volume[-24:]))),
        ret_1h_pct=ret_1h * 100,
        ret_4h_pct=ret_4h * 100,
        rel_volume=rel_volume,
        taker_buy_ratio=taker,
        rsi=rsi_now,
        drawdown_from_peak_pct=drawdown * 100,
        label=label,
        intensity=intensity,
        reasons=reasons,
    )


def scan_pumps(
    getter: Any = None,
    *,
    interval: str = "1h",
    min_quote_volume: float = 1_000_000.0,
    universe: int = 100,
    meme_bases: set[str] | None = None,
    pause: float = 0.05,
) -> tuple[list[PumpRow], list[str]]:
    """Scan the most active Binance USDT markets (plus known meme coins)."""
    getter = getter or get_json
    memes = {bp.normalize_base(b) for b in (meme_bases or FALLBACK_MEME_BASES)}
    tickers = bp.ticker_24h(getter)
    bases = {b for s in tickers if (b := bp.split_usdt(s))}
    excluded = bp.leveraged_bases(bases) | bp.STABLE_BASES

    active: list[tuple[str, str, float]] = []
    for symbol, row in tickers.items():
        base = bp.split_usdt(symbol)
        volume = to_float(row.get("quoteVolume"), 0.0) or 0.0
        if not base or base in excluded or volume <= 0:
            continue
        is_meme = bp.normalize_base(base) in memes
        if volume >= min_quote_volume or (is_meme and volume >= min_quote_volume / 4):
            active.append((symbol, base, volume))
    active.sort(key=lambda item: item[2], reverse=True)
    selected = active[:universe]
    selected_symbols = {s for s, _, _ in selected}
    selected += [item for item in active[universe:] if bp.normalize_base(item[1]) in memes
                 and item[0] not in selected_symbols]

    rows: list[PumpRow] = []
    warnings: list[str] = []
    failures = 0
    for symbol, base, _ in selected:
        try:
            raw = bp.klines(symbol, interval=interval, limit=60, getter=getter)
            row = analyze_klines(
                symbol, base, raw, ticker=tickers.get(symbol),
                is_meme=bp.normalize_base(base) in memes,
            )
            if row:
                rows.append(row)
        except (HttpError, ValueError, TypeError, IndexError) as exc:
            failures += 1
            if failures <= 3:
                warnings.append(f"{symbol}: {exc}")
        if pause:
            time.sleep(pause)
    if failures > 3:
        warnings.append(f"{failures} markets failed in total")
    rows.sort(key=lambda r: (LABEL_PRIORITY.get(r.label, 9), -r.intensity))
    return rows, warnings
