"""Rug pull and scam-token risk assessment.

Inputs
------
* GoPlus Security token API (free, no key): contract-level checks for EVM chains and a
  best-effort Solana check (honeypot simulation, mint/ownership powers, taxes, holder
  concentration, liquidity-lock status).
* DexScreener pair data: liquidity depth, pair age, liquidity vs. valuation, sell pressure.

Principles
----------
1. **Unknown is not safe.** Missing data raises risk and can never produce a "LOW" label.
2. **Hard failures override the score.** A honeypot or a sell tax above 50% is CRITICAL no
   matter what else looks fine.
3. **LOW does not mean safe.** It means none of the checks we can run found a problem.
   Social-engineering rugs, team dumps from fresh wallets, and brand-new exploits pass these
   checks. Treat the output as a filter that removes obvious traps, not an endorsement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .memecoins import DexToken
from .net import HttpError, get_json, to_float

GOPLUS_URL = "https://api.gopluslabs.io/api/v1"
GOPLUS_BATCH = 10

EVM_CHAIN_IDS = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "base": "8453",
    "avalanche": "43114",
}
BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


@dataclass
class RiskFlag:
    severity: str  # critical | high | medium | info
    code: str
    message: str


@dataclass
class RiskReport:
    score: int
    level: str  # CRITICAL | HIGH | MEDIUM | LOW | UNVERIFIED
    verified: bool
    flags: list[RiskFlag] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def supported_chain(chain: str) -> bool:
    return chain == "solana" or chain in EVM_CHAIN_IDS


def _on(data: dict, key: str) -> bool:
    return str(data.get(key, "0")).strip() in {"1", "true", "True"}


def _present(data: dict, key: str) -> bool:
    """True only when the provider actually gave a value (``null`` and ``""`` mean unknown)."""
    value = data.get(key)
    return value is not None and str(value).strip() != ""


def _dicts(value: Any) -> list[dict]:
    """Keep only dict items of a list; providers occasionally return odd shapes."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


# Fields a verdict cannot be trusted without. If the provider omits one of these, the token is
# UNVERIFIED rather than LOW, because "not reported" is not the same as "checked and fine".
# Findings that alone make a token HIGH risk, regardless of the numeric score.
HIGH_FLOOR_CODES = {
    "sell_tax_high", "hidden_owner", "reclaim_ownership", "lp_unlocked", "mintable", "freezable",
}
# Missing-information findings that keep a token out of LOW (at best MEDIUM).
UNKNOWN_CODES = {"lp_unknown", "holders_unknown", "concentration_unknown"}
CORE_FIELDS_EVM = ("is_honeypot", "sell_tax", "buy_tax", "is_open_source")
CORE_FIELDS_SOLANA = ("mintable", "freezable")


def _status_on(value: Any) -> bool:
    """Solana fields look like ``{"status": "1", "authority": [...]}``."""
    if isinstance(value, dict):
        return str(value.get("status", "0")).strip() in {"1", "true", "True"}
    return str(value).strip() in {"1", "true", "True"}


def _pct(value: Any) -> float | None:
    """GoPlus percentages are fractions encoded as strings (``"0.05"`` = 5%)."""
    number = to_float(value)
    if number is None:
        return None
    return number if number <= 1.0 else number / 100.0


def fetch_security(
    chain: str, addresses: list[str], getter: Any = None
) -> dict[str, dict]:
    """Return ``{address: security_dict}`` for the addresses GoPlus knows about."""
    getter = getter or get_json
    if not supported_chain(chain):
        return {}
    out: dict[str, dict] = {}
    for start in range(0, len(addresses), GOPLUS_BATCH):
        batch = addresses[start:start + GOPLUS_BATCH]
        if chain == "solana":
            url = f"{GOPLUS_URL}/solana/token_security"
        else:
            url = f"{GOPLUS_URL}/token_security/{EVM_CHAIN_IDS[chain]}"
        data = getter(url, params={"contract_addresses": ",".join(batch)})
        result = (data.get("result") if isinstance(data, dict) else None) or {}
        if isinstance(result, dict):
            for address, info in result.items():
                if isinstance(info, dict) and info:
                    out[address if chain == "solana" else address.lower()] = info
    return out


def _add(flags: list[RiskFlag], severity: str, code: str, message: str) -> None:
    flags.append(RiskFlag(severity, code, message))


_POINTS = {"critical": 100, "high": 20, "medium": 10, "info": 0}


def _assess_evm(sec: dict, token: DexToken | None, flags: list[RiskFlag]) -> int:
    points = 0

    def risk(sev: str, code: str, msg: str, pts: int | None = None) -> None:
        nonlocal points
        _add(flags, sev, code, msg)
        points += _POINTS[sev] if pts is None else pts

    if _on(sec, "is_honeypot"):
        risk("critical", "honeypot", "Honeypot: the contract blocks selling. You can buy but not sell.")
    elif not _present(sec, "is_honeypot"):
        risk("medium", "honeypot_unknown", "Could not simulate a sell, so honeypot status is unknown.", 15)
    if _on(sec, "cannot_sell_all"):
        risk("critical", "cannot_sell_all", "Contract prevents selling your full balance.")
    if _on(sec, "cannot_buy"):
        risk("critical", "cannot_buy", "Buying is disabled in the contract.")
    if _on(sec, "owner_change_balance"):
        risk("critical", "owner_change_balance", "Owner can change holder balances.")
    if _on(sec, "selfdestruct"):
        risk("critical", "selfdestruct", "Contract can self-destruct.")
    if _on(sec, "hidden_owner") and _on(sec, "can_take_back_ownership"):
        risk("critical", "hidden_owner_takeback", "Hidden owner who can take back ownership after 'renouncing'.")
    elif _on(sec, "hidden_owner"):
        risk("high", "hidden_owner", "Contract has a hidden owner.")
    elif _on(sec, "can_take_back_ownership"):
        risk("high", "reclaim_ownership", "Ownership can be taken back after renouncing.")

    sell_tax = to_float(sec.get("sell_tax"))
    buy_tax = to_float(sec.get("buy_tax"))
    if sell_tax is not None:
        if sell_tax >= 0.5:
            risk("critical", "sell_tax_extreme", f"Sell tax is {sell_tax:.0%}: effectively cannot exit.")
        elif sell_tax > 0.10:
            risk("high", "sell_tax_high", f"Sell tax is {sell_tax:.0%}.")
        elif sell_tax > 0.05:
            risk("medium", "sell_tax", f"Sell tax is {sell_tax:.0%}.")
    if buy_tax is not None:
        if buy_tax > 0.10:
            risk("high", "buy_tax_high", f"Buy tax is {buy_tax:.0%}.")
        elif buy_tax > 0.05:
            risk("medium", "buy_tax", f"Buy tax is {buy_tax:.0%}.")

    if _present(sec, "is_open_source") and not _on(sec, "is_open_source"):
        risk("high", "closed_source", "Contract source code is not verified/open.", 25)
    if _on(sec, "is_mintable"):
        risk("high", "mintable", "Owner can mint new tokens and dilute holders.", 20)
    if _on(sec, "is_proxy"):
        risk("medium", "proxy", "Upgradeable proxy: the logic can be swapped after you buy.", 10)
    if _on(sec, "transfer_pausable"):
        risk("high", "pausable", "Owner can pause all transfers.", 15)
    if _on(sec, "slippage_modifiable"):
        risk("high", "tax_changeable", "Owner can change buy/sell tax at any time.", 15)
    if _on(sec, "is_blacklisted"):
        risk("medium", "blacklist", "Contract can blacklist wallets from trading.", 10)
    if _on(sec, "trading_cooldown"):
        risk("info", "cooldown", "Trading cooldown is enabled.", 5)
    if _on(sec, "external_call"):
        risk("info", "external_call", "Contract makes external calls (can hide extra behaviour).", 5)

    holders = to_float(sec.get("holder_count"))
    if holders is not None:
        if holders < 100:
            risk("high", "few_holders", f"Only {int(holders)} holders.", 15)
        elif holders < 500:
            risk("medium", "low_holders", f"Only {int(holders)} holders.", 7)
    else:
        risk("medium", "holders_unknown", "Holder count was not reported.", 5)

    creator_pct = _pct(sec.get("creator_percent"))
    if creator_pct is not None and creator_pct > 0.10:
        risk("high", "creator_holds", f"Creator wallet holds {creator_pct:.0%} of supply.", 10)

    pair_addresses = {str(d.get("pair", "")).lower() for d in _dicts(sec.get("dex"))}
    if token and token.pair_address:
        pair_addresses.add(token.pair_address.lower())
    if not _dicts(sec.get("holders")):
        risk("medium", "concentration_unknown", "The top-holder list was not reported.", 5)
    top = []
    for holder in _dicts(sec.get("holders")):
        address = str(holder.get("address", "")).lower()
        if address in pair_addresses or address in BURN_ADDRESSES or _on(holder, "is_locked"):
            continue
        percent = _pct(holder.get("percent"))
        if percent is not None:
            top.append(percent)
    top10 = sum(sorted(top, reverse=True)[:10])
    if top10 > 0.5:
        risk("high", "concentrated", f"Top 10 wallets (excluding pool and locks) hold {top10:.0%}.", 20)
    elif top10 > 0.3:
        risk("medium", "concentrated", f"Top 10 wallets (excluding pool and locks) hold {top10:.0%}.", 10)

    lp_holders = _dicts(sec.get("lp_holders"))
    if lp_holders:
        locked = sum(
            _pct(h.get("percent")) or 0.0
            for h in lp_holders
            if _on(h, "is_locked") or str(h.get("address", "")).lower() in BURN_ADDRESSES
        )
        locked = min(locked, 1.0)
        if locked < 0.5:
            risk("high", "lp_unlocked",
                 f"Only {locked:.0%} of liquidity is locked or burned: the pool can be drained.", 20)
        else:
            _add(flags, "info", "lp_locked", f"{locked:.0%} of liquidity is locked or burned.")
    else:
        risk("medium", "lp_unknown", "Liquidity lock status is unknown.", 10)
    return points


def _assess_solana(sec: dict, flags: list[RiskFlag]) -> int:
    points = 0

    def risk(sev: str, code: str, msg: str, pts: int | None = None) -> None:
        nonlocal points
        _add(flags, sev, code, msg)
        points += _POINTS[sev] if pts is None else pts

    if _status_on(sec.get("non_transferable")):
        risk("critical", "non_transferable", "Token is non-transferable.")
    if _status_on(sec.get("balance_mutable_authority")):
        risk("critical", "balance_mutable", "An authority can change holder balances.")
    if _status_on(sec.get("freezable")):
        risk("high", "freezable", "A freeze authority can freeze your tokens.", 30)
    if _status_on(sec.get("mintable")):
        risk("high", "mintable", "Mint authority is active: supply can be inflated.", 25)
    if _status_on(sec.get("closable")):
        risk("medium", "closable", "Token accounts can be closed by an authority.", 10)
    if _status_on(sec.get("transfer_hook")):
        risk("medium", "transfer_hook", "Transfers run custom hook code.", 10)
    fee = sec.get("transfer_fee")
    if isinstance(fee, dict):
        rate = to_float(fee.get("current_fee_rate", {}).get("fee_rate")) if isinstance(
            fee.get("current_fee_rate"), dict) else None
        if rate is not None and rate > 5:
            risk("high", "transfer_fee", f"Transfer fee is {rate:.0f}%.", 15)
    if _status_on(sec.get("metadata_mutable")):
        _add(flags, "info", "metadata_mutable", "Token name/image can be changed by an authority.")

    holders = to_float(sec.get("holder_count"))
    if holders is not None:
        if holders < 100:
            risk("high", "few_holders", f"Only {int(holders)} holders.", 15)
        elif holders < 500:
            risk("medium", "low_holders", f"Only {int(holders)} holders.", 7)
    else:
        risk("medium", "holders_unknown", "Holder count was not reported.", 5)

    liquidity_pools = {str(d.get("id", d.get("pair", ""))) for d in _dicts(sec.get("dex"))}
    if not _dicts(sec.get("holders")):
        risk("medium", "concentration_unknown", "The top-holder list was not reported.", 5)
    top = []
    for holder in _dicts(sec.get("holders")):
        account = str(holder.get("account", holder.get("address", "")))
        if account in liquidity_pools or _on(holder, "is_locked"):
            continue
        percent = _pct(holder.get("percent"))
        if percent is not None:
            top.append(percent)
    top10 = sum(sorted(top, reverse=True)[:10])
    if top10 > 0.5:
        risk("high", "concentrated", f"Top 10 wallets hold {top10:.0%}.", 20)
    elif top10 > 0.3:
        risk("medium", "concentrated", f"Top 10 wallets hold {top10:.0%}.", 10)
    return points


def _assess_market(token: DexToken, flags: list[RiskFlag]) -> int:
    points = 0

    def risk(sev: str, code: str, msg: str, pts: int) -> None:
        nonlocal points
        _add(flags, sev, code, msg)
        points += pts

    liquidity = token.liquidity_usd or 0.0
    if liquidity < 10_000:
        risk("high", "thin_liquidity", f"Liquidity is only ${liquidity:,.0f}: easy to dump and hard to exit.", 20)
    elif liquidity < 50_000:
        risk("medium", "low_liquidity", f"Liquidity is ${liquidity:,.0f}: large orders will move the price.", 8)
    age = token.age_hours
    if age is not None:
        if age < 24:
            risk("high", "brand_new", f"Pair is only {age:.0f}h old. Most rugs happen in the first days.", 15)
        elif age < 72:
            risk("medium", "very_new", f"Pair is only {age / 24:.1f} days old.", 7)
    valuation = token.fdv or token.market_cap
    if valuation and liquidity and liquidity / valuation < 0.005:
        risk("medium", "liq_vs_valuation",
             f"Liquidity is {liquidity / valuation:.2%} of valuation: price is easy to manipulate.", 10)
    trades = token.buys_1h + token.sells_1h
    if trades >= 20 and token.sells_1h > 2 * token.buys_1h:
        risk("medium", "sell_pressure", "Sells outnumber buys 2:1 in the last hour.", 5)
    if token.boosts > 0:
        _add(flags, "info", "paid_boost", "Token has paid promotion on DexScreener.")
    return points


def assess_risk(token: DexToken | None, security: dict | None, chain: str) -> RiskReport:
    """Combine contract checks and market checks into a single report."""
    flags: list[RiskFlag] = []
    points = 0
    verified = bool(security)

    if security:
        points += _assess_solana(security, flags) if chain == "solana" else _assess_evm(security, token, flags)
        core = CORE_FIELDS_SOLANA if chain == "solana" else CORE_FIELDS_EVM
        missing = [name for name in core if not _present(security, name)]
        if missing:
            verified = False
            _add(flags, "medium", "incomplete_data",
                 "The security provider did not report: " + ", ".join(missing) + ". "
                 "Those risks were not checked.")
            points += 10
    else:
        reason = (
            f"No contract security data available for chain '{chain}'."
            if not supported_chain(chain)
            else "Security provider returned no data for this token."
        )
        _add(flags, "high", "no_security_data", reason + " Contract risks could not be checked.")
        points += 25
    if token is not None:
        points += _assess_market(token, flags)

    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    flags.sort(key=lambda f: order[f.severity])
    score = max(0, min(100, points))
    critical = any(f.severity == "critical" for f in flags)
    high_flags = [f for f in flags if f.severity == "high"]
    # A single serious flag must not be averaged away by clean fields elsewhere: a 49% sell tax
    # or drainable liquidity is a problem however good the rest of the token looks.
    forces_high = len(high_flags) >= 2 or any(f.code in HIGH_FLOOR_CODES for f in high_flags)

    if critical:
        level = "CRITICAL"
    elif score >= 60 or forces_high:
        level = "HIGH"
    elif not verified:
        level = "UNVERIFIED"
    elif score >= 30 or high_flags or any(f.code in UNKNOWN_CODES for f in flags):
        level = "MEDIUM"
    else:
        level = "LOW"
    if critical:
        score = max(score, 90)

    headline = {
        "CRITICAL": "Do not buy: a hard red flag was found.",
        "HIGH": "High risk of loss or rug pull.",
        "UNVERIFIED": "Could not verify the contract. Treat as high risk.",
        "MEDIUM": "Some warning signs. Size any position as a possible total loss.",
        "LOW": "No red flags in the checks we can run. This is not a safety guarantee.",
    }[level]
    return RiskReport(score=score, level=level, verified=verified, flags=flags, summary=headline)


def check_tokens(
    tokens: list[DexToken], getter: Any = None
) -> tuple[dict[tuple[str, str], RiskReport], list[str]]:
    """Run security lookups in batches per chain and return reports keyed by (chain, address)."""
    getter = getter or get_json
    warnings: list[str] = []
    by_chain: dict[str, list[DexToken]] = {}
    for token in tokens:
        by_chain.setdefault(token.chain, []).append(token)
    reports: dict[tuple[str, str], RiskReport] = {}
    for chain, group in by_chain.items():
        security: dict[str, dict] = {}
        if supported_chain(chain):
            try:
                security = fetch_security(chain, [t.address for t in group], getter)
            except HttpError as exc:
                warnings.append(f"Security lookup failed for {chain}: {exc}")
        for token in group:
            key_address = token.address if chain == "solana" else token.address.lower()
            try:
                reports[(chain, key_address)] = assess_risk(token, security.get(key_address), chain)
            except (AttributeError, TypeError, ValueError, KeyError) as exc:
                # One odd provider record must not take the whole list down. Fail closed.
                warnings.append(f"Could not interpret security data for {token.symbol}: {exc}")
                reports[(chain, key_address)] = RiskReport(
                    score=60, level="UNVERIFIED", verified=False,
                    flags=[RiskFlag("high", "unreadable_data",
                                    "Security data came back in an unexpected format, so it was not used.")],
                    summary="Could not verify the contract. Treat as high risk.",
                )
    return reports, warnings


def verdict(heat_score: int, risk_level: str) -> str:
    """One plain label per coin. Deliberately never says BUY."""
    if risk_level == "CRITICAL":
        return "AVOID"
    if risk_level in {"HIGH", "UNVERIFIED"}:
        return "HIGH_RISK" if heat_score >= 40 else "SKIP"
    return "WATCH" if heat_score >= 45 else "SKIP"
