"""Unit tests for the sandbox-only Binance adapter."""
import sys
from types import SimpleNamespace

import pytest

from marketbot.binance_demo import BinanceSpotDemo


class FakeExchange:
    def __init__(self, config):
        self.config = config
        self.markets = {"BTC/USDT": {"symbol": "BTC/USDT"}}
        self.sandbox_enabled = False
        self.orders = []

    def set_sandbox_mode(self, enabled):
        self.sandbox_enabled = enabled

    def load_markets(self):
        return self.markets

    def fetch_balance(self):
        return {"USDT": {"free": 500, "used": 0, "total": 500}}

    def create_order(self, symbol, order_type, side, amount):
        order = {"symbol": symbol, "type": order_type, "side": side, "amount": amount}
        self.orders.append(order)
        return order


@pytest.fixture
def demo(monkeypatch):
    fake = FakeExchange({})
    fake_module = SimpleNamespace(binance=lambda config: fake)
    monkeypatch.setitem(sys.modules, "ccxt", fake_module)
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "demo-secret")
    return BinanceSpotDemo(), fake


def test_sandbox_enabled_at_initialization(demo):
    adapter, exchange = demo
    assert exchange.sandbox_enabled is True
    assert adapter.account_balance()["USDT"]["free"] == 500


def test_market_order_routes_through_adapter(demo):
    adapter, exchange = demo
    result = adapter.market_order("BTC/USDT", "buy", 0.01)
    assert result["side"] == "buy"
    assert exchange.orders == [result]


@pytest.mark.parametrize("side,amount", [("hold", 1), ("buy", 0), ("sell", -1)])
def test_rejects_invalid_order(demo, side, amount):
    adapter, exchange = demo
    with pytest.raises(ValueError):
        adapter.market_order("BTC/USDT", side, amount)
    assert exchange.orders == []


def test_rejects_market_not_in_demo(demo):
    adapter, exchange = demo
    with pytest.raises(ValueError, match="not available"):
        adapter.market_order("FAKE/USDT", "buy", 1)
    assert exchange.orders == []


def test_missing_credentials_fail(monkeypatch):
    monkeypatch.delenv("BINANCE_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_DEMO_API_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="Missing demo credentials"):
        BinanceSpotDemo()
