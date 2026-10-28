"""GeckoTerminal client (free, no key): new pools, trending pools, candles and recent swaps.

It is the only free source here that reports **unique buyers and sellers** per time window and
the **exact pool creation time**, which the Fresh Launches model depends on.

The parsers are deliberately tolerant. They skip rows they cannot read instead of failing, and
the callers surface skipped rows as warnings. The response shapes were written from the public
API documentation; if GeckoTerminal changes them, the scanner shows "no data" with a warning
rather than inventing numbers.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .chains import get_chain
from .net import HttpError, get_json, to_float
from .pools import PoolRow, as_int, minutes_since

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}
_ADDRESS = re.compile(r"^[A-Za-z0-9]{20,64}$")
TIMEFRAMES = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
              "1h": ("hour", 1), "4h": ("hour", 4), "1d": ("day", 1)}


def valid_address(address: str) -> bool:
    """Contract / pool addresses are plain alphanumerics. Anything else never reaches a URL."""
    return bool(_ADDRESS.match(address or ""))


def _included_map(payload: dict) -> dict[str, dict]:
    out = {}
    for item in payload.get("included") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item.get("attributes") or {}
    return out


def _rel_id(item: dict, name: str) -> str:
    data = ((item.get("relationships") or {}).get(name) or {}).get("data") or {}
    return str(data.get("id") or "")


def _strip_network(identifier: str) -> str:
    """``solana_7xK...`` -> ``7xK...``"""
    return identifier.split("_", 1)[1] if "_" in identifier else identifier


def _flat(table: Any, key: str) -> float | None:
    """Read ``table[key]`` as a number when ``table`` is a dict (volume and price-change maps)."""
    return to_float(table.get(key)) if isinstance(table, dict) else None


def _window(table: Any, key: str, field: str) -> float | None:
    block = (table or {}).get(key) if isinstance(table, dict) else None
    return to_float(block.get(field)) if isinstance(block, dict) else None


def parse_pool(item: dict, included: dict[str, dict], chain: str, now: float | None = None) -> PoolRow | None:
    attrs = item.get("attributes") or {}
    pool_address = str(attrs.get("address") or _strip_network(str(item.get("id") or "")))
    base_id = _rel_id(item, "base_token")
    base = included.get(base_id, {})
    token_address = str(base.get("address") or _strip_network(base_id))
    if not pool_address or not token_address:
        return None
    quote = included.get(_rel_id(item, "quote_token"), {})
    name = str(base.get("name") or "")
    symbol = str(base.get("symbol") or (attrs.get("name") or "?").split("/")[0].strip())
    vols, chg, txns = attrs.get("volume_usd"), attrs.get("price_change_percentage"), attrs.get("transactions")
    created = attrs.get("pool_created_at")
    return PoolRow(
        chain=chain, pool_address=pool_address, token_address=token_address,
        symbol=symbol[:24], name=name[:60],
        dex=_rel_id(item, "dex")[:30], quote_symbol=str(quote.get("symbol") or "")[:12],
        price_usd=to_float(attrs.get("base_token_price_usd")),
        fdv_usd=to_float(attrs.get("fdv_usd")), market_cap_usd=to_float(attrs.get("market_cap_usd")),
        liquidity_usd=to_float(attrs.get("reserve_in_usd")),
        created_at=created, age_minutes=minutes_since(created, now),
        vol_m5=_flat(vols, "m5"), vol_m15=_flat(vols, "m15"), vol_h1=_flat(vols, "h1"),
        vol_h6=_flat(vols, "h6"), vol_h24=_flat(vols, "h24"),
        chg_m5=_flat(chg, "m5"), chg_h1=_flat(chg, "h1"), chg_h6=_flat(chg, "h6"),
        chg_h24=_flat(chg, "h24"),
        buys_m5=as_int(_window(txns, "m5", "buys")), sells_m5=as_int(_window(txns, "m5", "sells")),
        buyers_m5=as_int(_window(txns, "m5", "buyers")), sellers_m5=as_int(_window(txns, "m5", "sellers")),
        buys_h1=as_int(_window(txns, "h1", "buys")), sells_h1=as_int(_window(txns, "h1", "sells")),
        buyers_h1=as_int(_window(txns, "h1", "buyers")), sellers_h1=as_int(_window(txns, "h1", "sellers")),
        buys_h24=as_int(_window(txns, "h24", "buys")), sells_h24=as_int(_window(txns, "h24", "sells")),
        source="GeckoTerminal", fetched_at=now if now is not None else time.time(),
    )


def parse_pools(payload: Any, chain: str, now: float | None = None) -> tuple[list[PoolRow], int]:
    """Parse a list response. Returns ``(rows, skipped)``."""
    if not isinstance(payload, dict):
        return [], 0
    included = _included_map(payload)
    rows, skipped = [], 0
    for item in payload.get("data") or []:
        try:
            row = parse_pool(item, included, chain, now) if isinstance(item, dict) else None
        except (AttributeError, TypeError, ValueError):
            row = None
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped


def _list(path: str, chain: str, getter: Any, params: dict | None = None) -> tuple[list[PoolRow], int]:
    getter = getter or get_json
    net = get_chain(chain).gecko
    query = {"include": "base_token,quote_token,dex"}
    query.update(params or {})
    payload = getter(f"{BASE}/networks/{net}/{path}", params=query, headers=HEADERS)
    return parse_pools(payload, chain)


def new_pools(chain: str, getter: Any = None, page: int = 1) -> tuple[list[PoolRow], int]:
    """Most recently created pools on a chain."""
    return _list("new_pools", chain, getter, {"page": page})


def trending_pools(chain: str, getter: Any = None, duration: str = "1h") -> tuple[list[PoolRow], int]:
    return _list("trending_pools", chain, getter, {"duration": duration})


def pool(chain: str, pool_address: str, getter: Any = None) -> PoolRow | None:
    if not valid_address(pool_address):
        raise ValueError("invalid pool address")
    getter = getter or get_json
    net = get_chain(chain).gecko
    payload = getter(f"{BASE}/networks/{net}/pools/{pool_address}",
                     params={"include": "base_token,quote_token,dex"}, headers=HEADERS)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None
    return parse_pool(payload["data"], _included_map(payload), chain)


def token_pools(chain: str, token_address: str, getter: Any = None) -> list[PoolRow]:
    """Pools that trade a token, most liquid first. Used to resolve a pasted contract address."""
    if not valid_address(token_address):
        raise ValueError("invalid token address")
    getter = getter or get_json
    net = get_chain(chain).gecko
    payload = getter(f"{BASE}/networks/{net}/tokens/{token_address}/pools",
                     params={"include": "base_token,quote_token,dex", "page": 1}, headers=HEADERS)
    rows, _ = parse_pools(payload, chain)
    rows = [r for r in rows if r.token_address.lower() == token_address.lower()] or rows
    rows.sort(key=lambda r: r.liquidity_usd or 0.0, reverse=True)
    return rows


def ohlcv(
    chain: str, pool_address: str, timeframe: str = "5m", limit: int = 120, getter: Any = None
) -> list[dict]:
    """Candles oldest-first: ``[{"t": epoch_s, "o", "h", "l", "c", "v"}, ...]``."""
    if not valid_address(pool_address):
        raise ValueError("invalid pool address")
    unit, aggregate = TIMEFRAMES.get(timeframe, TIMEFRAMES["5m"])
    getter = getter or get_json
    net = get_chain(chain).gecko
    payload = getter(
        f"{BASE}/networks/{net}/pools/{pool_address}/ohlcv/{unit}",
        params={"aggregate": aggregate, "limit": max(10, min(limit, 500)), "currency": "usd"},
        headers=HEADERS,
    )
    raw = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = []
    for row in raw:
        if isinstance(row, list) and len(row) >= 6:
            values = [to_float(x) for x in row[:6]]
            if None not in values and values[1] is not None and values[1] > 0:
                candles.append({"t": int(values[0]), "o": values[1], "h": values[2],
                                "l": values[3], "c": values[4], "v": values[5]})
    candles.sort(key=lambda c: c["t"])
    return candles


def trades(chain: str, pool_address: str, getter: Any = None, min_usd: float = 0.0) -> list[dict]:
    """Recent swaps, newest first: ``{"time", "kind", "usd", "price_usd", "wallet", "tx"}``."""
    if not valid_address(pool_address):
        raise ValueError("invalid pool address")
    getter = getter or get_json
    net = get_chain(chain).gecko
    params = {"trade_volume_in_usd_greater_than": min_usd} if min_usd else None
    payload = getter(f"{BASE}/networks/{net}/pools/{pool_address}/trades", params=params, headers=HEADERS)
    out = []
    for item in (payload or {}).get("data") or []:
        attrs = (item or {}).get("attributes") or {}
        kind = str(attrs.get("kind") or "").lower()
        usd = to_float(attrs.get("volume_in_usd"))
        if kind not in {"buy", "sell"} or usd is None:
            continue
        price = to_float(attrs.get("price_to_in_usd") if kind == "buy" else attrs.get("price_from_in_usd"))
        out.append({
            "time": attrs.get("block_timestamp"), "kind": kind, "usd": usd, "price_usd": price,
            "wallet": str(attrs.get("tx_from_address") or ""), "tx": str(attrs.get("tx_hash") or ""),
        })
    out.sort(key=lambda t: str(t["time"] or ""), reverse=True)
    return out


__all__ = [
    "HttpError",
    "new_pools",
    "ohlcv",
    "parse_pools",
    "pool",
    "token_pools",
    "trades",
    "trending_pools",
    "valid_address",
]
