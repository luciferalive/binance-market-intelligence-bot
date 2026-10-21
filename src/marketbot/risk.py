"""Risk sizing and guardrails for research and paper-trading workflows.

This module intentionally does not place orders. It computes quantities and
checks proposed trades so execution code can remain behind a separate safety
boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    risk_fraction: float = 0.01
    max_position_fraction: float = 0.20
    max_open_positions: int = 5
    max_daily_loss_fraction: float = 0.03
    max_total_exposure_fraction: float = 0.70

    def __post_init__(self) -> None:
        if not 0 < self.risk_fraction <= 0.05:
            raise ValueError("risk_fraction must be in (0, 0.05]")
        if not 0 < self.max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        if not 0 < self.max_daily_loss_fraction <= 1:
            raise ValueError("max_daily_loss_fraction must be in (0, 1]")
        if not 0 < self.max_total_exposure_fraction <= 1:
            raise ValueError("max_total_exposure_fraction must be in (0, 1]")


@dataclass(frozen=True)
class PositionPlan:
    quantity: float
    notional: float
    risk_amount: float
    stop_distance: float


def size_position(
    equity: float,
    entry: float,
    stop: float,
    limits: RiskLimits | None = None,
    available_cash: float | None = None,
) -> PositionPlan:
    """Size a long spot position from stop distance and equity risk.

    Quantity is capped by max position value and, when provided, available cash.
    Values are unrounded; exchange precision/minimum-notional filters must be
    applied by a future execution adapter.
    """
    if equity <= 0 or entry <= 0 or stop <= 0:
        raise ValueError("equity, entry and stop must be positive")
    if stop >= entry:
        raise ValueError("long spot stop must be below entry")
    if available_cash is not None and available_cash < 0:
        raise ValueError("available_cash cannot be negative")

    limits = limits or RiskLimits()
    risk_budget = equity * limits.risk_fraction
    stop_distance = entry - stop
    qty_by_risk = risk_budget / stop_distance
    max_notional = equity * limits.max_position_fraction
    if available_cash is not None:
        max_notional = min(max_notional, available_cash)
    quantity = min(qty_by_risk, max_notional / entry)
    notional = quantity * entry
    return PositionPlan(
        quantity=quantity,
        notional=notional,
        risk_amount=quantity * stop_distance,
        stop_distance=stop_distance,
    )


def check_portfolio_limits(
    *,
    open_positions: int,
    equity: float,
    current_exposure: float,
    proposed_notional: float,
    daily_pnl: float,
    limits: RiskLimits = RiskLimits(),
) -> tuple[bool, str]:
    """Return (allowed, reason) for a proposed additional long position."""
    limits = limits or RiskLimits()
    if equity <= 0:
        return False, "equity must be positive"
    if open_positions >= limits.max_open_positions:
        return False, "maximum open positions reached"
    if daily_pnl <= -(equity * limits.max_daily_loss_fraction):
        return False, "daily loss limit reached"
    cap = equity * limits.max_total_exposure_fraction
    if current_exposure + proposed_notional > cap:
        return False, "total exposure limit would be exceeded"
    if proposed_notional <= 0:
        return False, "proposed notional must be positive"
    return True, "within configured portfolio limits"
