import pytest

from marketbot.risk import RiskLimits, check_portfolio_limits, size_position


def test_position_size_respects_risk_budget_and_cash_cap():
    plan = size_position(10_000, 100, 95, RiskLimits(risk_fraction=0.01), available_cash=500)
    assert plan.notional <= 500
    assert plan.risk_amount <= 100
    assert plan.quantity > 0


def test_invalid_long_stop_rejected():
    with pytest.raises(ValueError):
        size_position(1000, 100, 100)


def test_portfolio_daily_loss_guard():
    allowed, reason = check_portfolio_limits(
        open_positions=0, equity=1000, current_exposure=0,
        proposed_notional=100, daily_pnl=-31,
        limits=RiskLimits(max_daily_loss_fraction=0.03),
    )
    assert not allowed
    assert "daily loss" in reason
