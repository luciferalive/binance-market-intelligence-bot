"""Meme coin discovery: what is trending right now, and why.

Sources
-------
* DexScreener (free, no key): boosted / newly profiled tokens plus live pair data
  (liquidity, volume, buys vs sells, price change, pair age).
* CoinGecko (free, key optional): trending search and the "meme-token" category.

The ``heat_score`` measures *attention and momentum only*. It says nothing about safety;
pair it with ``rugcheck.assess_risk``. A hot coin is often a trap, so heat and risk are
kept as two separate numbers on purpose.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .net import HttpError, get_json, to_float

DEX_URL = "https://api.dexscreener.com"
COINGECKO_URL = "https://api.coingecko.com/api/v3"

_CHAIN_RE = re.compile(r"^[a-z0-9-]{2,30}$")
_PAIR_RE = re.compile(r"^[A-Za-z0-9]{20,100}$")
DEX_BATCH = 30  # DexScreener accepts up to 30 token addresses per call


@dataclass
class DexToken:
    chain: str
    address: str
    symbol: str
    name: str
    pair_address: str = ""
    dex: str = ""
    url: str = ""
    price_usd: float | None = None
    liquidity_usd: float | None = None
    fdv: float | None = None
    market_cap: float | None = None
    volume_24h: float | None = None
    volume_1h: float | None = None
    change_5m: float | None = None
    change_1h: float | None = None
    change_6h: float | None = None
    change_24h: float | None = None
    buys_1h: int = 0
    sells_1h: int = 0
    buys_24h: int = 0
    sells_24h: int = 0
    age_hours: float | None = None
    boosts: int = 0
    heat_score: int = 0
    heat_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_address(chain: str, address: str) -> str:
    """EVM addresses are case-insensitive; Solana (base58) addresses are not."""
    return address if chain == "solana" else address.lower()


def _as_int(value: Any) -> int:
    number = to_float(value, 0.0)
    return int(number or 0)


def _pair_url(chain: str, pair_address: str) -> str:
    """Build the link ourselves; never trust a URL string from a scam token's metadata."""
    if _CHAIN_RE.match(chain or "") and _PAIR_RE.match(pair_address or ""):
        return f"https://dexscreener.com/{chain}/{pair_address}"
    return ""


def parse_pair(pair: dict) -> DexToken | None:
    """Convert one DexScreener pair object into a ``DexToken`` (``None`` if unusable)."""
    base = pair.get("baseToken") or {}
    chain = str(pair.get("chainId") or "")
    address = str(base.get("address") or "")
    if not chain or not address:
        return None
    txns = pair.get("txns") or {}
    h1 = txns.get("h1") or {}
    h24 = txns.get("h24") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    liquidity = pair.get("liquidity") or {}
    created_ms = to_float(pair.get("pairCreatedAt"))
    age_hours = None
    if created_ms:
        now_ms = datetime.now(UTC).timestamp() * 1000
        age_hours = max(0.0, (now_ms - created_ms) / 3_600_000)
    boosts = pair.get("boosts") or {}
    pair_address = str(pair.get("pairAddress") or "")
    return DexToken(
        chain=chain,
        address=address,
        symbol=str(base.get("symbol") or "?")[:24],
        name=str(base.get("name") or "")[:60],
        pair_address=pair_address,
        dex=str(pair.get("dexId") or "")[:30],
        url=_pair_url(chain, pair_address),
        price_usd=to_float(pair.get("priceUsd")),
        liquidity_usd=to_float(liquidity.get("usd")),
        fdv=to_float(pair.get("fdv")),
        market_cap=to_float(pair.get("marketCap")),
        volume_24h=to_float(volume.get("h24")),
        volume_1h=to_float(volume.get("h1")),
        change_5m=to_float(change.get("m5")),
        change_1h=to_float(change.get("h1")),
        change_6h=to_float(change.get("h6")),
        change_24h=to_float(change.get("h24")),
        buys_1h=_as_int(h1.get("buys")),
        sells_1h=_as_int(h1.get("sells")),
        buys_24h=_as_int(h24.get("buys")),
        sells_24h=_as_int(h24.get("sells")),
        age_hours=age_hours,
        boosts=_as_int(boosts.get("active")),
    )


def score_heat(token: DexToken) -> tuple[int, list[str]]:
    """Attention/momentum score from 0 to 100 with a plain-language reason for each part."""
    score = 0
    reasons: list[str] = []
    liquidity = token.liquidity_usd or 0.0
    volume = token.volume_24h or 0.0

    if liquidity > 0:
        turnover = volume / liquidity
        if 3 <= turnover < 15:
            score += 20
            reasons.append(f"healthy trading activity ({turnover:.1f}x liquidity traded in 24h)")
        elif 0.5 <= turnover < 3:
            score += 10
            reasons.append(f"moderate trading activity ({turnover:.1f}x liquidity in 24h)")
        elif 15 <= turnover < 40:
            score += 15
            reasons.append(f"very high turnover ({turnover:.0f}x liquidity in 24h)")
        elif turnover >= 40:
            score += 5
            reasons.append(
                f"extreme turnover ({turnover:.0f}x liquidity) - can mean wash trading or a pump in progress"
            )

    m1 = token.change_1h
    if m1 is not None:
        if 3 <= m1 < 40:
            score += 15
            reasons.append(f"up {m1:.1f}% in the last hour")
        elif m1 >= 40:
            score += 8
            reasons.append(f"up {m1:.0f}% in one hour - very fast, often too late to enter")
        elif m1 <= -10:
            score -= 10
            reasons.append(f"down {abs(m1):.1f}% in the last hour")
    m6 = token.change_6h
    if m6 is not None and 5 <= m6 < 150:
        score += 15
        reasons.append(f"up {m6:.0f}% over 6 hours")

    trades_1h = token.buys_1h + token.sells_1h
    if trades_1h >= 20:
        buy_ratio = token.buys_1h / trades_1h
        if buy_ratio >= 0.6:
            score += 15
            reasons.append(f"buyers dominate ({buy_ratio:.0%} of last-hour trades)")
        elif buy_ratio >= 0.52:
            score += 8
            reasons.append(f"slightly more buys than sells ({buy_ratio:.0%})")
        elif buy_ratio <= 0.4:
            score -= 10
            reasons.append(f"sellers dominate ({1 - buy_ratio:.0%} of last-hour trades)")
    if trades_1h >= 200:
        score += 15
        reasons.append(f"{trades_1h} trades in the last hour")
    elif trades_1h >= 50:
        score += 8
        reasons.append(f"{trades_1h} trades in the last hour")

    age = token.age_hours
    if age is not None:
        if age < 1:
            reasons.append("pair is under 1 hour old")
        elif 6 <= age <= 24 * 14:
            score += 10
            reasons.append(f"young pair ({age / 24:.1f} days old) - still in discovery phase")

    if liquidity >= 100_000:
        score += 10
        reasons.append(f"liquidity ${liquidity:,.0f}")
    elif liquidity >= 30_000:
        score += 5
        reasons.append(f"liquidity ${liquidity:,.0f}")

    if token.boosts > 0:
        score += 5
        reasons.append("paid promotion active on DexScreener (attention is bought, not earned)")

    m24 = token.change_24h
    if m24 is not None:
        if m24 >= 300:
            score -= 20
            reasons.append(f"already up {m24:.0f}% in 24h - chasing risk is very high")
        elif m24 >= 100:
            score -= 8
            reasons.append(f"already up {m24:.0f}% in 24h - chasing risk")

    return max(0, min(100, score)), reasons


def _best_pairs(pairs: list[dict]) -> dict[tuple[str, str], dict]:
    """Keep the most liquid pair for every (chain, token)."""
    best: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        base = pair.get("baseToken") or {}
        chain = str(pair.get("chainId") or "")
        address = str(base.get("address") or "")
        if not chain or not address:
            continue
        key = (chain, _norm_address(chain, address))
        liquidity = to_float((pair.get("liquidity") or {}).get("usd"), 0.0) or 0.0
        current = best.get(key)
        current_liquidity = (
            to_float(((current or {}).get("liquidity") or {}).get("usd"), 0.0) or 0.0
        )
        if current is None or liquidity > current_liquidity:
            best[key] = pair
    return best


def fetch_dex_candidates(
    getter: Any = None,
    *,
    max_tokens: int = 60,
    min_liquidity: float = 20_000.0,
    min_volume_24h: float = 50_000.0,
    chains: set[str] | None = None,
) -> tuple[list[DexToken], list[str]]:
    """Discover currently promoted/new tokens and rank them by heat.

    Returns ``(tokens, warnings)``. A failing source becomes a warning, not an exception.
    """
    getter = getter or get_json
    warnings: list[str] = []
    seeds: dict[tuple[str, str], str] = {}
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1"):
        try:
            items = getter(f"{DEX_URL}{path}")
        except HttpError as exc:
            warnings.append(f"DexScreener {path} unavailable: {exc}")
            continue
        for item in items if isinstance(items, list) else []:
            chain = str(item.get("chainId") or "")
            address = str(item.get("tokenAddress") or "")
            if not chain or not address or (chains and chain not in chains):
                continue
            seeds.setdefault((chain, _norm_address(chain, address)), address)
            if len(seeds) >= max_tokens:
                break

    by_chain: dict[str, list[str]] = {}
    for (chain, _), address in seeds.items():
        by_chain.setdefault(chain, []).append(address)

    pairs: list[dict] = []
    for chain, addresses in by_chain.items():
        for start in range(0, len(addresses), DEX_BATCH):
            batch = addresses[start:start + DEX_BATCH]
            try:
                data = getter(f"{DEX_URL}/tokens/v1/{chain}/{','.join(batch)}")
            except HttpError as exc:
                warnings.append(f"DexScreener pair lookup failed for {chain}: {exc}")
                continue
            pairs.extend(data if isinstance(data, list) else [])

    tokens: list[DexToken] = []
    for pair in _best_pairs(pairs).values():
        token = parse_pair(pair)
        if token is None:
            continue
        if (token.liquidity_usd or 0) < min_liquidity or (token.volume_24h or 0) < min_volume_24h:
            continue
        token.heat_score, token.heat_reasons = score_heat(token)
        tokens.append(token)
    tokens.sort(key=lambda t: (t.heat_score, t.volume_24h or 0), reverse=True)
    return tokens, warnings


def search_tokens(query: str, getter: Any = None, limit: int = 10) -> list[DexToken]:
    """Look up tokens by symbol, name or address and return the most liquid pair of each."""
    getter = getter or get_json
    data = getter(f"{DEX_URL}/latest/dex/search", params={"q": query})
    pairs = (data or {}).get("pairs") or []
    tokens = []
    for pair in _best_pairs(pairs).values():
        token = parse_pair(pair)
        if token is not None:
            token.heat_score, token.heat_reasons = score_heat(token)
            tokens.append(token)
    tokens.sort(key=lambda t: t.liquidity_usd or 0, reverse=True)
    return tokens[:limit]


def _coingecko_headers(api_key: str | None) -> dict[str, str]:
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def fetch_coingecko_trending(getter: Any = None, api_key: str | None = None) -> list[dict]:
    """CoinGecko's trending search list, normalised to flat dictionaries."""
    getter = getter or get_json
    data = getter(f"{COINGECKO_URL}/search/trending", headers=_coingecko_headers(api_key))
    rows = []
    for entry in (data or {}).get("coins", []):
        item = entry.get("item") or {}
        extra = item.get("data") or {}
        pct = extra.get("price_change_percentage_24h") or {}
        rows.append({
            "id": item.get("id"),
            "symbol": str(item.get("symbol") or "").upper(),
            "name": str(item.get("name") or ""),
            "market_cap_rank": item.get("market_cap_rank"),
            "price_usd": to_float(extra.get("price")),
            "change_24h": to_float(pct.get("usd")) if isinstance(pct, dict) else to_float(pct),
            "market_cap": to_float(extra.get("market_cap")),
            "volume_24h": to_float(extra.get("total_volume")),
        })
    return rows


def fetch_coingecko_memes(
    getter: Any = None, api_key: str | None = None, per_page: int = 50
) -> list[dict]:
    """Largest meme tokens by 24h volume from CoinGecko's ``meme-token`` category."""
    getter = getter or get_json
    data = getter(
        f"{COINGECKO_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "category": "meme-token",
            "order": "volume_desc",
            "per_page": per_page,
            "page": 1,
            "price_change_percentage": "1h,24h,7d",
        },
        headers=_coingecko_headers(api_key),
    )
    rows = []
    for coin in data or []:
        rows.append({
            "id": coin.get("id"),
            "symbol": str(coin.get("symbol") or "").upper(),
            "name": str(coin.get("name") or ""),
            "market_cap_rank": coin.get("market_cap_rank"),
            "price_usd": to_float(coin.get("current_price")),
            "market_cap": to_float(coin.get("market_cap")),
            "volume_24h": to_float(coin.get("total_volume")),
            "change_1h": to_float(coin.get("price_change_percentage_1h_in_currency")),
            "change_24h": to_float(coin.get("price_change_percentage_24h_in_currency"))
            or to_float(coin.get("price_change_percentage_24h")),
            "change_7d": to_float(coin.get("price_change_percentage_7d_in_currency")),
        })
    return rows


def mark_binance_listed(rows: list[dict], binance_bases: set[str]) -> list[dict]:
    """Add ``on_binance`` so you can tell which coins you could actually buy on Binance Spot."""
    for row in rows:
        row["on_binance"] = str(row.get("symbol", "")).upper() in binance_bases
    return rows
