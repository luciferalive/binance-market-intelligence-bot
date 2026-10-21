"""Public-data Binance Spot scanner. No private API keys or order execution."""
from __future__ import annotations

import time
from typing import Any

import ccxt
import pandas as pd

STABLE_QUOTES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "USDE"}
LEVERAGED_SUFFIXES = ("UP/USDT", "DOWN/USDT", "BULL/USDT", "BEAR/USDT")


def make_exchange() -> ccxt.binance:
    return ccxt.binance({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "spot"},
    })


def eligible_symbols(exchange: Any, quote: str = "USDT") -> list[str]:
    markets = exchange.load_markets()
    result = []
    for symbol, market in markets.items():
        if not market.get("spot") or not market.get("active", True):
            continue
        if market.get("quote") != quote or not market.get("base"):
            continue
        base = market["base"]
        if base in STABLE_QUOTES or any(symbol.endswith(suffix) for suffix in LEVERAGED_SUFFIXES):
            continue
        result.append(symbol)
    return sorted(set(result))


def scan_markets(
    quote: str = "USDT",
    timeframe: str = "1h",
    top: int = 80,
    limit: int = 160,
    pause_seconds: float = 0.05,
) -> list[dict]:
    """Rank liquid pairs, then score OHLCV. Uses only public endpoints."""
    from .strategy import analyze_candles

    exchange = make_exchange()
    symbols = eligible_symbols(exchange, quote=quote)
    tickers = exchange.fetch_tickers(symbols)
    ranked = []
    for symbol in symbols:
        ticker = tickers.get(symbol, {})
        quote_volume = ticker.get("quoteVolume")
        last = ticker.get("last")
        if quote_volume is None or last is None or float(last) <= 0:
            continue
        ranked.append((symbol, float(quote_volume)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    candidates = ranked[:max(1, top)]
    results: list[dict] = []

    for index, (symbol, quote_volume) in enumerate(candidates, start=1):
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if len(raw) < 65:
                continue
            # Binance returns [timestamp, open, high, low, close, volume].
            # Drop the latest candle because it may still be forming.
            frame = pd.DataFrame(raw[:-1], columns=[
                "timestamp", "open", "high", "low", "close", "volume"
            ])
            signal = analyze_candles(
                symbol=symbol,
                timeframe=timeframe,
                candles=frame,
                quote_volume_24h=quote_volume,
            )
            row = signal.to_dict()
            row["rank_by_24h_volume"] = index
            results.append(row)
        except (ccxt.BaseError, ValueError, TypeError, KeyError) as exc:
            results.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "signal": "DATA_ERROR",
                "score": 0,
                "quote_volume_24h": quote_volume,
                "reasons": str(exc)[:240],
            })
        if pause_seconds:
            time.sleep(pause_seconds)

    results.sort(key=lambda row: (row.get("score", 0), row.get("quote_volume_24h", 0)), reverse=True)
    return results
