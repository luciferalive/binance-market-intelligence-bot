import numpy as np
import pandas as pd
import pytest

from marketbot.backtest import run_backtest


def candles(n=130):
    close = np.linspace(100, 150, n) + np.sin(np.arange(n) / 3)
    return pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(n, 1000.0),
    })


def test_backtest_returns_metrics():
    result = run_backtest(candles(), starting_cash=1000, warmup=60)
    assert result.starting_cash == 1000
    assert result.ending_cash > 0
    assert result.max_drawdown_pct >= 0
    assert result.fees_paid >= 0


def test_backtest_rejects_bad_fee():
    with pytest.raises(ValueError):
        run_backtest(candles(), fee_rate=0.1)
