"""Token discovery: fetch candidates, apply filters, enrich with security, score by mode.

Pipeline for one scan of one chain in one mode:

1. **Candidates.** Fresh Launches reads the newest pools; Momentum reads trending pools; Smart
   Money reads both. A source failure becomes a warning, and DexScreener is the fallback.
2. **Market filters** (age, liquidity, volume, buyers...) run first, so the expensive security
   lookups only happen for rows that could still pass.
3. **Enrichment.** Security views come from the cache or the provider, in batches.
4. **Scoring** with the mode's own model, then risk/ownership/momentum filters, then ranking.

A filter on a value the source did not report *excludes* the row: unknown never passes a
threshold you set.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import geckoterminal as gt
from . import memecoins, scoring, security
from .cache import TTLCache
from .net import HttpError, get_json, to_float
from .pools import PoolRow, from_dex_token

AGE_BUCKETS = {
    "lt5": (0.0, 5.0), "5_15": (5.0, 15.0), "15_60": (15.0, 60.0),
    "1_6h": (60.0, 360.0), "gt6h": (360.0, None),
}
CONF_RANK = {"low": 1, "medium": 2, "high": 3}
MAX_ENRICH = 30
LIQUIDITY_FLOOR = 2_000.0
ROWS_TTL = 90.0

# Filters the spec asks for that need data this build does not have. They are reported back so
# the UI can show them as "not connected" instead of silently ignoring them.
UNSUPPORTED = {
    "clusters": "Wallet-cluster detection needs funding-flow data (not connected).",
    "social": "Social-attention growth needs a social data provider (not connected).",
}


def _num(query: dict, key: str, low: float, high: float) -> float | None:
    values = query.get(key) or []
    number = to_float(values[0]) if values else None
    return None if number is None else max(low, min(high, number))


def _flag(query: dict, key: str) -> bool:
    values = query.get(key) or []
    return bool(values) and str(values[0]).lower() in {"1", "true", "on", "yes"}


@dataclass
class Filters:
    age_bucket: str | None = None
    age_min: float | None = None
    age_max: float | None = None
    liq_min: float | None = None
    liq_max: float | None = None
    position_usd: float | None = None
    liq_pos_mult: float | None = None
    vol5_min: float | None = None
    buyers5_min: int | None = None
    accel_min: float | None = None
    wallets_min: int | None = None
    risk_max: int | None = None
    conf_min: str | None = None
    exclude_critical: bool = False
    exclude_failed_sell: bool = False
    top10_max: float | None = None
    deployer_max: float | None = None
    chg_min: float | None = None
    breakout: bool = False
    holders_up: bool = False
    smart_only: bool = False
    unsupported: list[str] = field(default_factory=list)

    @classmethod
    def from_query(cls, query: dict[str, list[str]]) -> Filters:
        bucket = (query.get("age") or [""])[0]
        conf = (query.get("conf_min") or [""])[0].lower()
        buyers = _num(query, "buyers5_min", 0, 10_000)
        wallets = _num(query, "wallets_min", 0, 10_000)
        risk = _num(query, "risk_max", 0, 100)
        f = cls(
            age_bucket=bucket if bucket in AGE_BUCKETS else None,
            age_min=_num(query, "age_min", 0, 10_000_000), age_max=_num(query, "age_max", 0, 10_000_000),
            liq_min=_num(query, "liq_min", 0, 1e12), liq_max=_num(query, "liq_max", 0, 1e12),
            position_usd=_num(query, "position_usd", 1, 1e9), liq_pos_mult=_num(query, "liq_pos_mult", 0, 10_000),
            vol5_min=_num(query, "vol5_min", 0, 1e12),
            buyers5_min=None if buyers is None else int(buyers),
            accel_min=_num(query, "accel_min", 0, 1000),
            wallets_min=None if wallets is None else int(wallets),
            risk_max=None if risk is None else int(risk),
            conf_min=conf if conf in CONF_RANK else None,
            exclude_critical=_flag(query, "excl_critical"), exclude_failed_sell=_flag(query, "excl_failed_sell"),
            top10_max=_num(query, "top10_max", 0, 100), deployer_max=_num(query, "deployer_max", 0, 100),
            chg_min=_num(query, "chg_min", -100, 1e6), breakout=_flag(query, "breakout"),
            holders_up=_flag(query, "holders_up"), smart_only=_flag(query, "smart"),
        )
        f.unsupported = [k for k in UNSUPPORTED if _flag(query, k)]
        return f

    def effective_age_range(self) -> tuple[float | None, float | None]:
        low, high = self.age_min, self.age_max
        if self.age_bucket:
            b_low, b_high = AGE_BUCKETS[self.age_bucket]
            low = b_low if low is None else max(low, b_low)
            high = b_high if high is None else (high if b_high is None else min(high, b_high))
        return low, high

    def active(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False, []) and k != "unsupported"}


def passes_market(row: PoolRow, f: Filters) -> bool:
    """Filters that only need market data. Unknown values fail a threshold the user set."""
    low, high = f.effective_age_range()
    if low is not None or high is not None:
        if row.age_minutes is None:
            return False
        if low is not None and row.age_minutes < low:
            return False
        if high is not None and row.age_minutes >= high:
            return False
    liq = row.liquidity_usd
    required_liq = f.liq_min
    if f.liq_pos_mult is not None and f.position_usd is not None:
        need = f.liq_pos_mult * f.position_usd
        required_liq = need if required_liq is None else max(required_liq, need)
    if required_liq is not None and (liq is None or liq < required_liq):
        return False
    if f.liq_max is not None and (liq is None or liq > f.liq_max):
        return False
    if f.vol5_min is not None and (row.vol_m5 is None or row.vol_m5 < f.vol5_min):
        return False
    if f.buyers5_min is not None and (row.buyers_m5 is None or row.buyers_m5 < f.buyers5_min):
        return False
    wallets = scoring.active_wallets(row)
    if f.wallets_min is not None and (wallets is None or wallets < f.wallets_min):
        return False
    if f.chg_min is not None and (row.chg_h1 is None or row.chg_h1 < f.chg_min):
        return False
    a5, a1 = scoring.volume_acceleration(row)
    best = max([x for x in (a5, a1) if x is not None], default=None)
    if f.accel_min is not None and (best is None or best < f.accel_min):
        return False
    return not (f.breakout and (best is None or best < 2.0 or (row.chg_h1 or 0) <= 0))


def passes_scored(item: scoring.ScoredPool, f: Filters, holder_growth: float | None) -> bool:
    """Filters that need the security view or the score."""
    if f.exclude_critical and item.risk_level == "CRITICAL":
        return False
    if f.risk_max is not None and item.risk_score > f.risk_max:
        return False
    if f.conf_min is not None and scoring.CONF_ORDER[item.confidence] < CONF_RANK[f.conf_min]:
        return False
    sim = item.view.fields.get("sell_simulation") or {}
    if f.exclude_failed_sell and sim.get("passed") is False:
        return False
    top10 = item.view.fields.get("top10_pct")
    if f.top10_max is not None and (top10 is None or top10 > f.top10_max):
        return False
    deployer = item.view.fields.get("creator_pct")
    if f.deployer_max is not None and (deployer is None or deployer > f.deployer_max):
        return False
    if f.holders_up and (holder_growth is None or holder_growth <= 0):
        return False
    return not (f.smart_only and not (item.smart and item.smart.get("tracked_buyers", 0) > 0))


class Scanner:
    """Runs scans. All network access goes through ``getter`` (the source gateway)."""

    def __init__(
        self,
        getter: Callable[..., Any] | None = None,
        history: Callable[[str], dict | None] | None = None,
        record: Callable[[scoring.ScoredPool], None] | None = None,
        smart_flow: Callable[[str, list[PoolRow]], dict[str, dict]] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.getter = getter or get_json
        self.history = history
        self.record = record
        self.smart_flow = smart_flow
        self.clock = clock
        self._rows = TTLCache()
        self._security_cache: dict = {}

    # -- candidates --------------------------------------------------------------
    def _fetch(self, kind: str, chain: str) -> tuple[list[PoolRow], list[str]]:
        warnings: list[str] = []
        rows: list[PoolRow] = []
        try:
            if kind == "new":
                for page in (1, 2):
                    got, skipped = gt.new_pools(chain, self.getter, page=page)
                    rows += got
                    if skipped:
                        warnings.append(f"{skipped} new-pool rows could not be read and were skipped.")
            else:
                for duration in ("1h", "24h"):
                    got, skipped = gt.trending_pools(chain, self.getter, duration=duration)
                    rows += got
                    if skipped:
                        warnings.append(f"{skipped} trending rows could not be read and were skipped.")
        except (HttpError, ValueError) as exc:
            warnings.append(f"GeckoTerminal {kind} pools unavailable for {chain}: {exc}")
        if not rows:
            warnings.append("Using DexScreener as a fallback: unique-buyer counts are not available from it.")
            try:
                tokens, more = memecoins.fetch_dex_candidates(self.getter, chains={chain}, min_liquidity=1_000, min_volume_24h=1_000)
                rows = [from_dex_token(t, self.clock()) for t in tokens]
                warnings += more
            except HttpError as exc:
                warnings.append(f"DexScreener fallback failed: {exc}")
        seen: dict[str, PoolRow] = {}
        for row in rows:
            seen.setdefault(row.key, row)
        return list(seen.values()), warnings

    def candidates(self, chain: str, mode: str) -> tuple[list[PoolRow], list[str], float]:
        kinds = {"fresh": ["new"], "momentum": ["trending"], "smart": ["new", "trending"]}[mode]
        rows: dict[str, PoolRow] = {}
        warnings: list[str] = []
        for kind in kinds:
            got, warn = self._rows.get(f"{chain}:{kind}", ROWS_TTL, lambda k=kind: self._fetch(k, chain))
            for row in got:
                rows.setdefault(row.key, row)
            warnings += warn
        age = max((self._rows.age(f"{chain}:{k}") or 0.0) for k in kinds)
        return list(rows.values()), list(dict.fromkeys(warnings)), age

    # -- scan --------------------------------------------------------------------
    def scan(self, chain: str, mode: str, filters: Filters | None = None, limit: int = 40) -> dict:
        if mode not in scoring.MODES:
            raise ValueError(f"unknown mode {mode!r}")
        filters = filters or Filters()
        now = self.clock()
        rows, warnings, rows_age = self.candidates(chain, mode)
        fetched = len(rows)
        rows = [r for r in rows if (r.liquidity_usd or 0) >= LIQUIDITY_FLOOR and passes_market(r, filters)]
        after_market = len(rows)
        if mode == "fresh":
            rows.sort(key=lambda r: (r.age_minutes if r.age_minutes is not None else 1e12))
        else:
            rows.sort(key=lambda r: r.vol_h1 or 0.0, reverse=True)
        rows = rows[:MAX_ENRICH]

        views, sec_warnings = security.fetch_views(chain, rows, self.getter, self._security_cache, now)
        warnings += sec_warnings
        flows: dict[str, dict] = {}
        if mode == "smart" and self.smart_flow and rows:
            try:
                flows = self.smart_flow(chain, rows[:8])
            except HttpError as exc:
                warnings.append(f"Wallet activity unavailable: {exc}")

        scored: list[scoring.ScoredPool] = []
        for row in rows:
            view = views[row.token_address.lower()]
            hist = self.history(row.key) if self.history else None
            growth = hist.get("holder_growth_pct") if hist else None
            ctx = {"position_usd": filters.position_usd, "holder_growth_pct": growth, "smart": flows.get(row.key)}
            item = scoring.score_pool(mode, row, view, ctx, now)
            if self.record:
                self.record(item)
            if passes_scored(item, filters, growth):
                scored.append(item)
        scored.sort(key=lambda s: (-1 if s.opportunity is None else s.opportunity, s.row.liquidity_usd or 0), reverse=True)

        return {
            "mode": mode, "chain": chain, "rows": [s.to_dict() for s in scored[:limit]],
            "counts": {"fetched": fetched, "after_market_filters": after_market,
                       "scored": len(rows), "returned": min(len(scored), limit)},
            "filters": filters.active(),
            "unsupported_filters": {k: UNSUPPORTED[k] for k in filters.unsupported},
            "warnings": warnings,
            "freshness": {"market_data_age_s": rows_age, "as_of": now},
        }
