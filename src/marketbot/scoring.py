"""Explainable opportunity, risk and data-confidence scoring with three different models.

* **Fresh Launches** judges a token that has minutes of history: is the pool deep enough to
  exit, are many *different* wallets buying, is the contract safe? It does not reward momentum,
  because five minutes of price action is mostly noise.
* **Momentum** judges a token with hours of history: acceleration of volume against its own
  earlier rate, breadth of buyers, trend quality, depth, and holder growth.
* **Smart Money** judges whether wallets *you track* are accumulating or distributing.

Rules that hold in every model (and are tested):

1. Every point is itemised, so the UI can show exactly why a score is what it is.
2. **Risk caps the opportunity.** A critical security failure forces the score to zero, and a
   high or unverified risk level cannot score as a candidate, however strong the momentum.
3. **Unknown is not safe.** Missing data scores zero for that component and lowers confidence.
4. Output labels are WATCH / CANDIDATE / HIGH RISK / AVOID / SKIP. There is no "buy".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .pools import PoolRow
from .security import SecurityView

MODES = ("fresh", "momentum", "smart")
MODE_LABELS = {"fresh": "Fresh Launches", "momentum": "Momentum", "smart": "Smart Money"}
CONF_ORDER = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
RISK_CAPS = {"HIGH": 35.0, "UNVERIFIED": 40.0, "MEDIUM": 70.0}
CONF_CAPS = {"UNKNOWN": 30.0, "LOW": 60.0}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ramp(value: float | None, zero_at: float, full_at: float) -> float:
    """0 at ``zero_at`` rising linearly to 1 at ``full_at`` (works for either direction)."""
    if value is None or zero_at == full_at:
        return 0.0
    return clamp((value - zero_at) / (full_at - zero_at))


@dataclass
class Contribution:
    name: str
    points: float
    max: float
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "points": round(self.points, 1), "max": self.max, "detail": self.detail}


@dataclass
class ScoredPool:
    mode: str
    row: PoolRow
    view: SecurityView
    risk_level: str
    risk_score: int
    confidence: str
    confidence_reasons: list[str]
    opportunity: float | None
    opportunity_raw: float | None
    contributions: list[Contribution]
    caps: list[dict]
    label: str
    accel_5m: float | None
    accel_1h: float | None
    active_wallets: int | None
    smart: dict | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        fields = {k: v for k, v in self.view.fields.items()}
        return {
            "key": self.row.key,
            "mode": self.mode,
            "pool": self.row.to_dict(),
            "label": self.label,
            "opportunity": None if self.opportunity is None else round(self.opportunity),
            "opportunity_raw": None if self.opportunity_raw is None else round(self.opportunity_raw),
            "contributions": [c.to_dict() for c in self.contributions],
            "caps": self.caps,
            "risk": {
                "level": self.risk_level, "score": self.risk_score,
                "summary": self.view.report.summary,
                "flags": [{"severity": f.severity, "code": f.code, "message": f.message}
                          for f in self.view.report.flags],
            },
            "confidence": {"level": self.confidence, "reasons": self.confidence_reasons},
            "security": {"available": self.view.available, "fields": fields,
                         "provider": self.view.provider, "fetched_at": self.view.fetched_at},
            "accel_5m": self.accel_5m, "accel_1h": self.accel_1h,
            "active_wallets": self.active_wallets,
            "smart": self.smart, "notes": self.notes,
        }


# ------------------------------------------------------------------ derived metrics

def volume_acceleration(row: PoolRow) -> tuple[float | None, float | None]:
    """(5-minute, 1-hour) volume rate against the token's own earlier rate.

    1.0 means "trading at the same pace as before". Each value is ``None`` when the history
    needed to compare against does not exist yet, instead of a misleading number.
    """
    age = row.age_minutes
    accel_5m = accel_1h = None
    if row.vol_m5 is not None and row.vol_h1 is not None and row.vol_h1 > 0:
        window = min(age, 60.0) if age is not None else 60.0
        if window >= 10:
            accel_5m = (row.vol_m5 / 5.0) / (row.vol_h1 / window)
    if row.vol_h1 is not None and row.vol_h6 is not None:
        horizon = min(age, 360.0) if age is not None else 360.0
        prior_minutes = horizon - 60.0
        prior_volume = row.vol_h6 - row.vol_h1
        if prior_minutes >= 30 and prior_volume > 0:
            accel_1h = (row.vol_h1 / 60.0) / (prior_volume / prior_minutes)
    return accel_5m, accel_1h


def active_wallets(row: PoolRow) -> int | None:
    """Distinct addresses that bought or sold in the last 5 minutes (not verified independent)."""
    if row.buyers_m5 is None or row.sellers_m5 is None:
        return None
    return row.buyers_m5 + row.sellers_m5


def _buy_share(buys: int | None, sells: int | None) -> float | None:
    if buys is None or sells is None or buys + sells == 0:
        return None
    return buys / (buys + sells)


def data_confidence(
    row: PoolRow, view: SecurityView, now: float, corroborated: bool = False
) -> tuple[str, list[str]]:
    """How much the numbers can be trusted, with the reasons. Missing data never raises it."""
    reasons: list[str] = []
    if not view.available:
        return "UNKNOWN", ["Security data is missing, so contract risk cannot be verified."]
    level = 3
    if any(f.code == "incomplete_data" for f in view.report.flags):
        level = min(level, 1)
        reasons.append("The security provider left out key fields.")
    age = row.age_minutes
    if age is None:
        level = min(level, 1)
        reasons.append("Pool creation time is unknown.")
    elif age < 5:
        level = min(level, 1)
        reasons.append(f"Only {age:.0f} minutes of history exist.")
    elif age < 30:
        level = min(level, 2)
        reasons.append(f"Limited history ({age:.0f} minutes).")
    missing = [n for n, v in (("liquidity", row.liquidity_usd), ("1h volume", row.vol_h1),
                              ("buyer counts", row.buyers_m5 if row.buyers_m5 is not None else row.buyers_h1))
               if v is None]
    if len(missing) >= 2:
        level = min(level, 1)
        reasons.append("Missing: " + ", ".join(missing) + ".")
    elif missing:
        level = min(level, 2)
        reasons.append("Missing: " + ", ".join(missing) + ".")
    staleness = now - row.fetched_at if row.fetched_at else None
    if staleness is not None and staleness > 900:
        level = min(level, 1)
        reasons.append(f"Market data is {staleness / 60:.0f} minutes old.")
    elif staleness is not None and staleness > 300:
        level = min(level, 2)
        reasons.append(f"Market data is {staleness / 60:.0f} minutes old.")
    if not corroborated:
        level = min(level, 2)
        reasons.append("Market data comes from a single source.")
    name = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}[level]
    if not reasons:
        reasons.append("Sources agree and the data is recent.")
    return name, reasons


# ------------------------------------------------------------------ the three models

def _security_points(view: SecurityView, maximum: float) -> Contribution:
    if not view.available:
        return Contribution("Security", 0, maximum, "No security data: scored as unsafe, not as safe.")
    factor = 1.0 if view.report.verified else 0.3
    points = maximum * (1 - view.report.score / 100) * factor
    suffix = "" if view.report.verified else " (provider data incomplete, so heavily discounted)"
    return Contribution("Security", points, maximum, f"{view.report.level.title()} risk, score {view.report.score}/100{suffix}.")


def _ownership_points(view: SecurityView, maximum: float = 10.0) -> Contribution:
    fields = view.fields
    top10, creator = fields.get("top10_pct"), fields.get("creator_pct")
    points = 0.0
    parts = []
    if top10 is None:
        parts.append("top-holder split unknown")
    else:
        points += 6.0 if top10 <= 20 else 4.0 if top10 <= 35 else 2.0 if top10 <= 50 else 0.0
        parts.append(f"top 10 wallets hold {top10:.0f}%")
    if creator is None:
        parts.append("deployer share unknown")
    else:
        points += 4.0 if creator <= 2 else 2.0 if creator <= 5 else 0.0
        parts.append(f"deployer holds {creator:.1f}%")
    return Contribution("Ownership", points, maximum, "; ".join(parts) + ".")


def _liquidity_points(row: PoolRow, target: float, maximum: float) -> Contribution:
    if row.liquidity_usd is None:
        return Contribution("Liquidity depth", 0, maximum, "Liquidity unknown.")
    points = maximum * clamp(row.liquidity_usd / target)
    return Contribution("Liquidity depth", points, maximum,
                        f"${row.liquidity_usd:,.0f} against a ${target:,.0f} target.")


def score_fresh(row: PoolRow, view: SecurityView, ctx: dict) -> tuple[float, list[Contribution], list[str]]:
    a5, _ = volume_acceleration(row)
    position = float(ctx.get("position_usd") or 100.0)
    out = [_liquidity_points(row, max(25_000.0, 20 * position), 20.0)]

    buyers = row.buyers_m5
    share = _buy_share(row.buyers_m5, row.sellers_m5)
    if buyers is None:
        out.append(Contribution("Buyer breadth", 0, 20, "Unique buyer counts unavailable."))
    else:
        pts = 12 * clamp(buyers / 25.0) + (8 * ramp(share, 0.35, 0.65) if share is not None else 0.0)
        shown = f"{buyers} different wallets bought in 5 minutes"
        if share is not None:
            shown += f", {share:.0%} of active wallets were buyers"
        out.append(Contribution("Buyer breadth", pts, 20, shown + "."))

    ratio = _buy_share(row.buys_m5, row.sells_m5)
    demand = 7 * ramp(ratio, 0.4, 0.6)
    if a5 is None:
        accel_pts, accel_txt = 0.0, "pace comparison needs 10+ minutes of history"
    elif a5 > 10:
        accel_pts, accel_txt = 2.0, f"volume pace {a5:.1f}x its own average, which is unusually spiky"
    elif a5 >= 1.2:
        accel_pts, accel_txt = 8.0, f"volume pace {a5:.1f}x its own average"
    else:
        accel_pts, accel_txt = 8 * ramp(a5, 0.5, 1.2), f"volume pace {a5:.1f}x its own average"
    out.append(Contribution("Early demand", demand + accel_pts, 15,
                            (f"{ratio:.0%} of 5-minute trades were buys; " if ratio is not None else "trade split unknown; ") + accel_txt + "."))

    chg = row.chg_h1
    if chg is None:
        price_pts, price_txt = 0.0, "price change unknown"
    elif chg <= -20:
        price_pts, price_txt = 0.0, f"down {abs(chg):.0f}% in the last hour"
    elif chg < 0:
        price_pts, price_txt = 4.0, f"down {abs(chg):.0f}% in the last hour"
    elif chg <= 150:
        price_pts, price_txt = 10.0, f"up {chg:.0f}% in the last hour"
    elif chg <= 400:
        price_pts, price_txt = 5.0, f"up {chg:.0f}% already: late entry"
    else:
        price_pts, price_txt = 1.0, f"up {chg:.0f}% already: very late"
    out.append(Contribution("Price discipline", price_pts, 10, price_txt + "."))
    out.append(_security_points(view, 25.0))
    out.append(_ownership_points(view, 10.0))
    return sum(c.points for c in out), out, []


def score_momentum(row: PoolRow, view: SecurityView, ctx: dict) -> tuple[float, list[Contribution], list[str]]:
    _, a1 = volume_acceleration(row)
    notes: list[str] = []
    if a1 is None:
        accel_pts, text = 0.0, "needs a token with 2+ hours of trading to compare against"
    elif a1 > 6:
        accel_pts, text = 15.0, f"hourly volume {a1:.1f}x its earlier pace: possibly a blow-off"
    elif a1 >= 1.5:
        accel_pts, text = 25.0, f"hourly volume {a1:.1f}x its earlier pace"
    else:
        accel_pts, text = 25 * ramp(a1, 0.8, 1.5), f"hourly volume {a1:.1f}x its earlier pace"
    out = [Contribution("Volume acceleration", accel_pts, 25, text + ".")]

    buyers = row.buyers_h1
    out.append(Contribution(
        "Buyer breadth", 15 * clamp((buyers or 0) / 100.0), 15,
        f"{buyers} different wallets bought in the last hour." if buyers is not None else "Unique buyer counts unavailable."))
    share = _buy_share(row.buys_h1, row.sells_h1)
    out.append(Contribution("Buy pressure", 10 * ramp(share, 0.5, 0.58) * 0.5 + (5 if share and share >= 0.58 else 0), 10,
                            f"{share:.0%} of last-hour trades were buys." if share is not None else "Trade split unknown."))

    trend, parts = 0.0, []
    if row.chg_h1 is not None and 0 < row.chg_h1 < 150:
        trend += 5
        parts.append(f"+{row.chg_h1:.0f}% 1h")
    if row.chg_h6 is not None and row.chg_h6 > 0:
        trend += 5
        parts.append(f"+{row.chg_h6:.0f}% 6h")
    if row.chg_h24 is not None and row.chg_h24 < 300:
        trend += 5
        parts.append("not yet parabolic")
    out.append(Contribution("Trend quality", trend, 15, (", ".join(parts) or "no supportive trend") + "."))

    depth = 0.0
    if row.liquidity_usd is not None:
        depth += 8 * clamp(row.liquidity_usd / 100_000.0)
    turnover = (row.vol_h24 / row.liquidity_usd) if row.vol_h24 and row.liquidity_usd else None
    if turnover is not None and 1 <= turnover <= 15:
        depth += 7
    out.append(Contribution("Liquidity and turnover", depth, 15,
                            (f"${row.liquidity_usd:,.0f} liquidity" if row.liquidity_usd is not None else "liquidity unknown")
                            + (f", {turnover:.1f}x daily turnover." if turnover is not None else ".")))

    growth = ctx.get("holder_growth_pct")
    if growth is None:
        out.append(Contribution("Holder growth", 0, 10, "Needs scan history: the dashboard records holder counts as it runs."))
        notes.append("Holder growth builds up as the scanner keeps running.")
    else:
        pts = 10.0 if growth >= 3 else 4.0 if growth >= 0 else 0.0
        out.append(Contribution("Holder growth", pts, 10, f"Holder count {growth:+.1f}% since first seen."))
    out.append(_security_points(view, 10.0))
    return sum(c.points for c in out), out, notes


def score_smart(row: PoolRow, view: SecurityView, ctx: dict) -> tuple[float | None, list[Contribution], list[str]]:
    flow = ctx.get("smart")
    if not flow:
        return None, [Contribution("Tracked wallets", 0, 40, "No tracked-wallet data for this token.")], [
            "Add wallet addresses on the Smart Money page to use this mode."]
    buyers, net, sold = flow.get("tracked_buyers", 0), flow.get("net_usd", 0.0), flow.get("sell_usd", 0.0)
    pts = 25 * clamp(buyers / 3.0) + 15 * clamp(max(net, 0.0) / 5000.0)
    out = [Contribution("Tracked wallets", pts, 40,
                        f"{buyers} tracked wallet(s) bought; net ${net:,.0f} over the recent trades.")]
    if sold > flow.get("buy_usd", 0.0):
        out.append(Contribution("Distribution", -15.0, 0, f"Tracked wallets sold ${sold:,.0f}, more than they bought."))
    out.append(_liquidity_points(row, 25_000.0, 20.0))
    out.append(_security_points(view, 20.0))
    _, a1 = volume_acceleration(row)
    share = _buy_share(row.buys_h1, row.sells_h1)
    light = 10 * ramp(a1, 0.8, 1.5) + 10 * ramp(share, 0.5, 0.6)
    out.append(Contribution("Market confirmation", light, 20, "Volume pace and buy share support the wallet activity."))
    return sum(c.points for c in out), out, []


MODELS = {"fresh": score_fresh, "momentum": score_momentum, "smart": score_smart}


def apply_caps(
    raw: float | None, risk_level: str, confidence: str, age_minutes: float | None, mode: str
) -> tuple[float | None, list[dict]]:
    """Risk and data quality cap the score. Returns the final score and the caps that bound."""
    if raw is None:
        return None, []
    raw = max(0.0, min(100.0, raw))
    if risk_level == "CRITICAL":
        return 0.0, [{"reason": "Critical security failure", "cap": 0}]
    candidates = []
    if risk_level in RISK_CAPS:
        candidates.append((f"{risk_level.title()} risk", RISK_CAPS[risk_level]))
    if confidence in CONF_CAPS:
        candidates.append((f"{confidence.title()} data confidence", CONF_CAPS[confidence]))
    if mode == "fresh" and age_minutes is not None and age_minutes < 5:
        candidates.append(("Under 5 minutes of history", 60.0))
    final = raw
    binding = []
    for reason, cap in candidates:
        if cap < final:
            final = cap
            binding.append({"reason": reason, "cap": cap})
    return final, binding


# A candidate must be decent on every critical dimension, not just strong on average. A clean
# contract can earn many points, but it cannot make a thin pool with almost no buyers a candidate.
GATE_COMPONENTS = {"Liquidity depth", "Buyer breadth", "Liquidity and turnover", "Volume acceleration"}


def weak_spots(contributions: list[Contribution]) -> list[str]:
    return [c.name for c in contributions
            if c.name in GATE_COMPONENTS and c.max and c.points < 0.25 * c.max]


def label_for(final: float | None, risk_level: str, confidence: str, raw: float | None,
              weak: list[str] | None = None) -> str:
    if risk_level == "CRITICAL":
        return "AVOID"
    if final is None:
        return "SKIP"
    if risk_level in {"HIGH", "UNVERIFIED"}:
        return "HIGH RISK" if (raw or 0) >= 55 else "SKIP"
    if final >= 65 and CONF_ORDER.get(confidence, 0) >= CONF_ORDER["MEDIUM"] and not weak:
        return "CANDIDATE"
    return "WATCH" if final >= 45 else "SKIP"


def score_pool(
    mode: str,
    row: PoolRow,
    view: SecurityView,
    ctx: dict | None = None,
    now: float | None = None,
    corroborated: bool = False,
) -> ScoredPool:
    if mode not in MODELS:
        raise ValueError(f"unknown mode {mode!r}")
    ctx = ctx or {}
    now = time.time() if now is None else now
    raw, contributions, notes = MODELS[mode](row, view, ctx)
    confidence, reasons = data_confidence(row, view, now, corroborated)
    final, caps = apply_caps(raw, view.report.level, confidence, row.age_minutes, mode)
    a5, a1 = volume_acceleration(row)
    weak = weak_spots(contributions)
    label = label_for(final, view.report.level, confidence, raw, weak)
    if weak and final is not None and final >= 65 and label == "WATCH":
        notes = notes + ["Held at WATCH: weak " + ", ".join(w.lower() for w in weak) + "."]
    return ScoredPool(
        mode=mode, row=row, view=view, risk_level=view.report.level, risk_score=view.report.score,
        confidence=confidence, confidence_reasons=reasons, opportunity=final, opportunity_raw=raw,
        contributions=contributions, caps=caps, label=label,
        accel_5m=a5, accel_1h=a1, active_wallets=active_wallets(row), smart=ctx.get("smart"), notes=notes,
    )


__all__: list[Any] = ["MODES", "MODE_LABELS", "ScoredPool", "score_pool", "volume_acceleration"]
