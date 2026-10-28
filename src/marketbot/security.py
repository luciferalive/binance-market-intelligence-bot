"""Per-chain security view built on the rug-check engine.

``rugcheck.assess_risk`` produces the risk level and flags. This module adds the *structured
fields* the scanner filters and the Security tab display: sell-simulation result, taxes,
authorities, top-holder and deployer shares, liquidity lock, with the provider and timestamp.

Different chains expose different checks, so the field set adapts: EVM contracts report taxes
and a sell simulation; Solana tokens report mint, freeze and transfer authorities instead.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .memecoins import DexToken
from .net import HttpError, get_json, to_float
from .pools import PoolRow
from .rugcheck import (
    BURN_ADDRESSES,
    RiskReport,
    _dicts,
    _on,
    _pct,
    _status_on,
    assess_risk,
    fetch_security,
    supported_chain,
)

CACHE_TTL = 600.0


@dataclass
class SecurityView:
    available: bool
    report: RiskReport
    fields: dict[str, Any] = field(default_factory=dict)
    holders: list[dict] = field(default_factory=list)
    provider: str = "GoPlus Security"
    fetched_at: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["report"] = self.report.to_dict()
        return data


def _tri(sec: dict, key: str) -> bool | None:
    """True / False when reported, ``None`` when the provider left it out."""
    value = sec.get(key)
    if value is None or str(value).strip() == "":
        return None
    return _on(sec, key)


def _holder_rows(sec: dict, chain: str, token: DexToken | None) -> list[dict]:
    """Top holders with the pool, burn and locked wallets labelled instead of dropped."""
    pools = {str(d.get("pair", d.get("id", ""))).lower() for d in _dicts(sec.get("dex"))}
    if token and token.pair_address:
        pools.add(token.pair_address.lower())
    rows = []
    for holder in _dicts(sec.get("holders")):
        address = str(holder.get("address", holder.get("account", "")))
        percent = _pct(holder.get("percent"))
        if not address or percent is None:
            continue
        low = address.lower()
        if low in BURN_ADDRESSES:
            label = "burn"
        elif low in pools:
            label = "liquidity pool"
        elif _on(holder, "is_locked"):
            label = "locked"
        elif _on(holder, "is_contract"):
            label = "contract"
        else:
            label = ""
        rows.append({"address": address, "percent": percent * 100, "label": label,
                     "tag": str(holder.get("tag") or "")[:40]})
    rows.sort(key=lambda r: r["percent"], reverse=True)
    return rows[:20]


def _top10(holders: list[dict]) -> float | None:
    """Share of supply held by the 10 largest *ordinary* wallets, as a percent."""
    ordinary = [h["percent"] for h in holders if not h["label"]]
    if not holders:
        return None
    return sum(sorted(ordinary, reverse=True)[:10])


def _lp_locked(sec: dict) -> float | None:
    lp = _dicts(sec.get("lp_holders"))
    if not lp:
        return None
    locked = sum(
        (_pct(h.get("percent")) or 0.0)
        for h in lp
        if _on(h, "is_locked") or str(h.get("address", "")).lower() in BURN_ADDRESSES
    )
    return min(locked, 1.0) * 100


def summarize(chain: str, token: DexToken | None, sec: dict | None, now: float | None = None) -> SecurityView:
    now = time.time() if now is None else now
    report = assess_risk(token, sec, chain)
    if not sec:
        return SecurityView(False, report, {"sell_simulation": {"available": False, "passed": None}},
                            fetched_at=now)
    holders = _holder_rows(sec, chain, token)
    fields: dict[str, Any]
    if chain == "solana":
        fields = {
            "mint_authority_active": _status_on(sec.get("mintable")) if "mintable" in sec else None,
            "freeze_authority_active": _status_on(sec.get("freezable")) if "freezable" in sec else None,
            "closable": _status_on(sec.get("closable")) if "closable" in sec else None,
            "balance_mutable": _status_on(sec.get("balance_mutable_authority"))
            if "balance_mutable_authority" in sec else None,
            "transfer_hook": _status_on(sec.get("transfer_hook")) if "transfer_hook" in sec else None,
            "non_transferable": _status_on(sec.get("non_transferable")) if "non_transferable" in sec else None,
            "metadata_mutable": _status_on(sec.get("metadata_mutable")) if "metadata_mutable" in sec else None,
            "holder_count": to_float(sec.get("holder_count")),
            "top10_pct": _top10(holders),
            "creator_pct": None,
            "lp_locked_pct": _lp_locked(sec),
            "sell_simulation": {"available": False, "passed": None,
                                "note": "The security provider does not simulate sells on Solana."},
        }
    else:
        honeypot = _tri(sec, "is_honeypot")
        cannot_sell = _tri(sec, "cannot_sell_all")
        passed: bool | None = None
        if honeypot is not None:
            passed = not honeypot and not bool(cannot_sell)
        fields = {
            "contract_verified": _tri(sec, "is_open_source"),
            "proxy": _tri(sec, "is_proxy"),
            "mintable": _tri(sec, "is_mintable"),
            "hidden_owner": _tri(sec, "hidden_owner"),
            "can_take_back_ownership": _tri(sec, "can_take_back_ownership"),
            "owner_can_change_balance": _tri(sec, "owner_change_balance"),
            "transfer_pausable": _tri(sec, "transfer_pausable"),
            "blacklist": _tri(sec, "is_blacklisted"),
            "tax_modifiable": _tri(sec, "slippage_modifiable"),
            "trading_cooldown": _tri(sec, "trading_cooldown"),
            "buy_tax_pct": (to_float(sec.get("buy_tax")) or 0.0) * 100 if to_float(sec.get("buy_tax")) is not None else None,
            "sell_tax_pct": (to_float(sec.get("sell_tax")) or 0.0) * 100 if to_float(sec.get("sell_tax")) is not None else None,
            "holder_count": to_float(sec.get("holder_count")),
            "top10_pct": _top10(holders),
            "creator_pct": (_pct(sec.get("creator_percent")) or 0.0) * 100 if _pct(sec.get("creator_percent")) is not None else None,
            "owner_pct": (_pct(sec.get("owner_percent")) or 0.0) * 100 if _pct(sec.get("owner_percent")) is not None else None,
            "lp_locked_pct": _lp_locked(sec),
            "sell_simulation": {"available": honeypot is not None, "passed": passed,
                                "note": "Provider simulation of a sell transaction."},
        }
    return SecurityView(True, report, fields, holders, fetched_at=now)


def fetch_views(
    chain: str,
    rows: list[PoolRow],
    getter: Any = None,
    cache: dict | None = None,
    now: float | None = None,
) -> tuple[dict[str, SecurityView], list[str]]:
    """Security views keyed by lower-cased token address. Cached for ten minutes per token."""
    getter = getter or get_json
    now = time.time() if now is None else now
    cache = cache if cache is not None else {}
    warnings: list[str] = []
    views: dict[str, SecurityView] = {}
    pending: list[PoolRow] = []
    for row in rows:
        key = f"{chain}:{row.token_address.lower()}"
        hit = cache.get(key)
        if hit and now - hit.fetched_at < CACHE_TTL:
            views[row.token_address.lower()] = hit
        else:
            pending.append(row)

    security: dict[str, dict] = {}
    if pending and supported_chain(chain):
        try:
            security = fetch_security(chain, [r.token_address for r in pending], getter)
        except HttpError as exc:
            warnings.append(f"Security provider unavailable for {chain}: {exc}")
    for row in pending:
        addr = row.token_address if chain == "solana" else row.token_address.lower()
        sec = security.get(addr)
        try:
            view = summarize(chain, row.to_dex_token(), sec, now)
        except (AttributeError, TypeError, ValueError, KeyError):
            report = RiskReport(60, "UNVERIFIED", False, [], "Could not verify the contract. Treat as high risk.")
            view = SecurityView(False, report, {"sell_simulation": {"available": False, "passed": None}}, fetched_at=now)
            warnings.append(f"Security data for {row.symbol} was in an unexpected format and was not used.")
        # Only cache real answers; an empty reply for a brand-new token may fill in a minute later.
        if view.available:
            cache[f"{chain}:{row.token_address.lower()}"] = view
        views[row.token_address.lower()] = view
    return views, warnings
