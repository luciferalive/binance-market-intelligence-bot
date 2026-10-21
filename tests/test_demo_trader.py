from marketbot.demo_trader import BinanceDemoTrader


class FakeExchange:
    def __init__(self):
        self.markets = {
            "BTC/USDT": {
                "symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "spot": True,
                "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
            }
        }
        self.free = {"USDT": 500.0}
        self.total = {"USDT": 500.0}
        self.orders = []

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.5f}"

    def fetch_tickers(self, symbols):
        return {symbol: {"last": 100.0} for symbol in symbols}

    def create_order(self, symbol, order_type, side, amount):
        amount = float(amount)
        order = {"id": str(len(self.orders) + 1), "symbol": symbol, "side": side,
                 "filled": amount, "average": 100.0, "status": "closed"}
        self.orders.append(order)
        if side == "buy":
            self.free["USDT"] -= amount * 100
            self.total["USDT"] -= amount * 100
            self.free["BTC"] = self.free.get("BTC", 0) + amount
            self.total["BTC"] = self.total.get("BTC", 0) + amount
        else:
            self.free["BTC"] -= amount
            self.total["BTC"] -= amount
            self.free["USDT"] += amount * 100
            self.total["USDT"] += amount * 100
        return order

    def fetch_balance(self):
        return {"free": dict(self.free), "total": dict(self.total)}


class FakeAdapter:
    def __init__(self):
        self.exchange = FakeExchange()

    def account_balance(self):
        return self.exchange.fetch_balance()

    def market_order(self, symbol, side, amount):
        return self.exchange.create_order(symbol, "market", side, amount)


def test_demo_auto_uses_connected_balance_and_persists_filled_position(tmp_path):
    adapter = FakeAdapter()
    rows = [{
        "symbol": "BTC/USDT", "signal": "LONG_SETUP", "close": 100,
        "stop_loss": 95, "target": 110,
    }]
    trader = BinanceDemoTrader(
        db_path=str(tmp_path / "demo.sqlite3"),
        exchange=adapter,
        scanner=lambda **kwargs: rows,
        max_allocation=100,
        top=1,
    )
    trader.cycle()
    positions = trader._positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC/USDT"
    assert positions[0]["quantity"] > 0
    assert adapter.exchange.orders[0]["side"] == "buy"


def test_demo_auto_respects_allocation_cap(tmp_path):
    adapter = FakeAdapter()
    rows = [{
        "symbol": "BTC/USDT", "signal": "LONG_SETUP", "close": 100,
        "stop_loss": 95, "target": 110,
    }]
    trader = BinanceDemoTrader(
        db_path=str(tmp_path / "demo.sqlite3"),
        exchange=adapter,
        scanner=lambda **kwargs: rows,
        max_allocation=50,
        top=1,
    )
    trader.cycle()
    position = trader._positions()[0]
    assert position["entry_notional"] <= 50
