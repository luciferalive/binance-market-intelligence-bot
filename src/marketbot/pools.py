"""Normalised liquidity-pool model shared by every data source.

Every numeric field is ``None`` when the source did not report it. ``None`` means *unknown*, and
the scanner treats unknown as a reason for lower confidence, never as a passing value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .memecoins import DexToken
from .net import to_float


@dataclass
class PoolRow:
    chain: str
    pool_address: str
    token_address: str
    symbol: str
    name: str
    dex: str = ""
    quote_symbol: str = ""
    price_usd: float | None = None
    fdv_usd: float | None = None
    market_cap_usd: float | None = None
    liquidity_usd: float | None = None
    created_at: str | None = None
    age_minutes: float | None = None
    vol_m5: float | None = None
    vol_m15: float | None = None
    vol_h1: float | None = None
    vol_h6: float | None = None
    vol_h24: float | None = None
    chg_m5: float | None = None
    chg_h1: float | None = None
    chg_h6: float | None = None
    chg_h24: float | None = None
    buys_m5: int | None = None
    sells_m5: int | None = None
    buyers_m5: int | None = None
    sellers_m5: int | None = None
    buys_h1: int | None = None
    sells_h1: int | None = None
    buyers_h1: int | None = None
    sellers_h1: int | None = None
    buys_h24: int | None = None
    sells_h24: int | None = None
    source: str = ""
    fetched_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.pool_address}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["key"] = self.key
        return data

    def to_dex_token(self) -> DexToken:
        """Adapter so the existing rug-check engine can score a pool from any source."""
        return DexToken(
            chain=self.chain, address=self.token_address, symbol=self.symbol, name=self.name,
            pair_address=self.pool_address, dex=self.dex, price_usd=self.price_usd,
            liquidity_usd=self.liquidity_usd, fdv=self.fdv_usd, market_cap=self.market_cap_usd,
            volume_24h=self.vol_h24, volume_1h=self.vol_h1, change_5m=self.chg_m5,
            change_1h=self.chg_h1, change_6h=self.chg_h6, change_24h=self.chg_h24,
            buys_1h=self.buys_h1 or 0, sells_1h=self.sells_h1 or 0,
            buys_24h=self.buys_h24 or 0, sells_24h=self.sells_h24 or 0,
            age_hours=(self.age_minutes / 60) if self.age_minutes is not None else None,
        )


def minutes_since(iso: str | None, now: float | None = None) -> float | None:
    """Minutes between an ISO-8601 timestamp and ``now`` (epoch seconds, default: current time)."""
    if not iso:
        return None
    try:
        created = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    current = now if now is not None else datetime.now(UTC).timestamp()
    return max(0.0, (current - created.timestamp()) / 60)


def from_dex_token(token: DexToken, fetched_at: float = 0.0) -> PoolRow:
    """Fallback conversion from DexScreener data. Unique-buyer counts are not available there."""
    age = (token.age_hours * 60) if token.age_hours is not None else None
    return PoolRow(
        chain=token.chain, pool_address=token.pair_address, token_address=token.address,
        symbol=token.symbol, name=token.name, dex=token.dex, price_usd=token.price_usd,
        fdv_usd=token.fdv, market_cap_usd=token.market_cap, liquidity_usd=token.liquidity_usd,
        age_minutes=age, vol_h1=token.volume_1h, vol_h24=token.volume_24h,
        chg_m5=token.change_5m, chg_h1=token.change_1h, chg_h6=token.change_6h,
        chg_h24=token.change_24h, buys_h1=token.buys_1h, sells_h1=token.sells_1h,
        buys_h24=token.buys_24h, sells_h24=token.sells_24h, source="DexScreener",
        fetched_at=fetched_at,
    )


def as_int(value: Any) -> int | None:
    number = to_float(value)
    return None if number is None else int(number)
