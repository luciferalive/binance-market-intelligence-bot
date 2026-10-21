"""Unit tests for the Binance Demo Trading adapter."""
import sys
from types import SimpleNamespace

import pytest

from marketbot.binance_demo import BinanceSpotDemo


class FakeMarket:
    def __init__(self):
        self.markets = {"BTC/USDT": {"symbol": "BTC/USDT", "spot": True}}
        self.demo_enabled = False
        self.orders = []

    def enable_demo_trading(self, enabled):
        self.demo_enabled = enabled

    def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def fetch_balance(self):
        return {"USDT": {"free": 500, "used": 0, "total": 500}}

    def create_order(self, symbol, order_type, side, amount):
        order = {"symbol": symbol, "type": order_type, "side": side, "amount": amount}
        self.orders.append(order)
        return order


@pytest.fixture
def demo(monkeypatch):
    fake = FakeMarket()
    fake_module = SimpleNamespace(binance=lambda config: fake)
    monkeypatch.setitem(sys.modules, "ccxt", fake_module)
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "demo-secret")
    return BinanceSpotDemo(), fake


def test_demo_trading_enabled_at_initialization(demo):
    adapter, exchange = demo
    assert exchange.demo_enabled is True
    assert adapter.account_balance()["USDT"]["free"] == 500


def test_market_order_routes_through_demo_adapter(demo):
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
    with pytest.raises(Exception):
        adapter.market_order("FAKE/USDT", "buy", 1)
    assert exchange.orders == []


def test_old_ccxt_without_demo_support_fails_closed(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "demo-secret")
    fake_module = SimpleNamespace(binance=lambda config: SimpleNamespace(set_sandbox_mode=lambda enabled: None))
    monkeypatch.setitem(sys.modules, "ccxt", fake_module)
    with pytest.raises(RuntimeError, match="lacks Binance Demo Trading support"):
        BinanceSpotDemo()


def test_missing_credentials_fail(monkeypatch):
    monkeypatch.delenv("BINANCE_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_DEMO_API_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="Missing demo credentials"):
        BinanceSpotDemo()
