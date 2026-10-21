import numpy as np
import pandas as pd

from marketbot.indicators import atr, ema, rsi
from marketbot.strategy import analyze_candles


def sample_candles(n: int = 100) -> pd.DataFrame:
    close = pd.Series(np.linspace(100, 130, n) + np.sin(np.arange(n) / 3))
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(n, 1000.0),
    })


def test_ema_and_rsi_produce_values():
    close = sample_candles()["close"]
    assert ema(close, 20).notna().any()
    assert rsi(close, 14).dropna().between(0, 100).all()


def test_atr_is_positive_after_warmup():
    values = atr(sample_candles(), 14).dropna()
    assert not values.empty
    assert (values > 0).all()


def test_strategy_returns_valid_signal_and_risk_levels():
    frame = sample_candles()
    signal = analyze_candles("TEST/USDT", "1h", frame, quote_volume_24h=1_000_000)
    assert signal.signal in {"LONG_SETUP", "WATCH", "NO_SETUP"}
    assert 0 <= signal.score <= 100
    assert signal.stop_loss < signal.entry < signal.target
