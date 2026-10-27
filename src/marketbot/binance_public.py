"""Public (no API key) Binance Spot REST helpers built on the stdlib HTTP layer."""
from __future__ import annotations

import re
from typing import Any

from .net import get_json, to_float

BASE_URL = "https://api.binance.com"

STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "USD1", "PYUSD", "EURI", "AEUR",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
_MULTIPLIER_PREFIX = re.compile(r"^(1000000|10000|1000|1M)(?=[A-Z])")


def normalize_base(base: str) -> str:
    """``1000SATS`` -> ``SATS``, ``1MBABYDOGE`` -> ``BABYDOGE``; ``1INCH`` is left alone."""
    return _MULTIPLIER_PREFIX.sub("", base.upper())


def split_usdt(symbol: str) -> str | None:
    """Return the base asset of a ``XXXUSDT`` symbol, else ``None``."""
    if symbol.endswith("USDT") and len(symbol) > 4:
        return symbol[:-4]
    return None


def leveraged_bases(bases: set[str]) -> set[str]:
    """Identify legacy leveraged tokens (``BTCUP``, ``ETHDOWN``).

    A base only counts as leveraged when stripping the suffix leaves *another listed base*.
    A bare ``endswith("UP")`` check wrongly removes real coins such as JUP or SYRUP.
    """
    found = set()
    for base in bases:
        for suffix in LEVERAGED_SUFFIXES:
            if base.endswith(suffix) and base[: -len(suffix)] in bases:
                found.add(base)
    return found


def ticker_prices(getter: Any = None) -> dict[str, float]:
    """All spot prices: ``{"BTCUSDT": 67000.0, ...}``."""
    getter = getter or get_json
    data = getter(f"{BASE_URL}/api/v3/ticker/price")
    prices: dict[str, float] = {}
    for row in data or []:
        price = to_float(row.get("price"))
        if row.get("symbol") and price is not None and price > 0:
            prices[row["symbol"]] = price
    return prices


def ticker_24h(getter: Any = None) -> dict[str, dict]:
    """All 24h tickers keyed by symbol (price, change %, quote volume)."""
    getter = getter or get_json
    data = getter(f"{BASE_URL}/api/v3/ticker/24hr")
    return {row["symbol"]: row for row in data or [] if row.get("symbol")}


def klines(symbol: str, interval: str = "1h", limit: int = 100, getter: Any = None) -> list[list]:
    """Candles: ``[openTime, open, high, low, close, volume, closeTime, quoteVolume, trades,
    takerBuyBase, takerBuyQuote, ignore]``. The last row is the still-forming candle."""
    getter = getter or get_json
    return getter(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    ) or []


def server_time_ms(getter: Any = None) -> int:
    getter = getter or get_json
    return int(getter(f"{BASE_URL}/api/v3/time")["serverTime"])
