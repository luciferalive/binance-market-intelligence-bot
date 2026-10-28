from marketbot.memepaper import PaperEngine, price_impact_pct
from marketbot.store import Store


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make():
    clock = Clock()
    store = Store(":memory:", clock=clock)
    return PaperEngine(store, clock=clock), store, clock


def buy(eng, clock, usd=500, liquidity=200_000, price=1.0, ts=None, pool="p1", **kw):
    return eng.buy(chain="solana", pool=pool, token="T" * 30, symbol="TST", name="Test", usd=usd, price=price,
                   liquidity=liquidity, price_ts=clock.t if ts is None else ts, **kw)


def test_impact_formula():
    assert price_impact_pct(None, 100) is None
    assert abs(price_impact_pct(200_000, 100_000) - 50.0) < 1e-9


def test_buy_and_sell_round_trip_costs_money():
    eng, _store, clock = make()
    r = buy(eng, clock)
    assert r["ok"], r
    assert eng.cash() < eng.capital()
    out = eng.sell("solana:p1", price=1.0, liquidity=200_000)
    assert out["ok"] and out["pnl"] < 0  # fees and slippage make a flat trade a loss
    assert eng.positions() == []


def test_entry_refusals():
    eng, store, clock = make()
    assert "Size is above" in " ".join(buy(eng, clock, usd=5000)["problems"])
    assert any("stale" in p for p in buy(eng, clock, ts=clock.t - 9999)["problems"])
    assert any("Liquidity" in p for p in buy(eng, clock, liquidity=1000)["problems"])
    assert any("unknown" in p for p in buy(eng, clock, liquidity=None)["problems"])
    store.set_setting("max_slippage_pct", 0.1)
    assert any("slippage" in p for p in buy(eng, clock, liquidity=50_000)["problems"])


def test_halt_blocks_entry_not_exit():
    eng, _store, clock = make()
    assert buy(eng, clock)["ok"]
    eng.halt("test")
    assert not buy(eng, clock, pool="p2")["ok"]
    assert eng.sell("solana:p1", price=1.0, liquidity=200_000)["ok"]
    eng.resume()
    assert buy(eng, clock, pool="p2")["ok"]


def test_stop_loss_take_profit_trailing_time():
    eng, _store, clock = make()
    buy(eng, clock, sl_pct=10)
    assert eng.mark("solana:p1", 0.8, 200_000).startswith("stop-loss")
    buy(eng, clock, pool="p2", tp_pct=50)
    assert eng.mark("solana:p2", 2.0, 400_000).startswith("take-profit")
    buy(eng, clock, pool="p3", trail_pct=20)
    assert eng.mark("solana:p3", 1.5, 200_000) is None
    assert eng.mark("solana:p3", 1.1, 200_000).startswith("trailing")
    buy(eng, clock, pool="p4", max_hold_min=10)
    clock.t += 11 * 60
    assert eng.mark("solana:p4", 1.0, 200_000) == "time stop"


def test_unknown_liquidity_exit_assumes_worst():
    eng, store, clock = make()
    buy(eng, clock)
    store.execute("UPDATE paper_positions SET last_liquidity=NULL")
    out = eng.sell("solana:p1", price=1.0, liquidity=None)
    assert out["ok"] and out["slippage_pct"] == 100.0


def test_daily_loss_limit_and_max_positions():
    eng, store, clock = make()
    store.set_setting("max_open_positions", 1)
    assert buy(eng, clock)["ok"]
    assert any("Maximum open" in p for p in buy(eng, clock, pool="p2")["problems"])
    store.set_setting("max_open_positions", 5)
    store.set_setting("max_daily_loss_pct", 1.0)
    eng.sell("solana:p1", price=0.1, liquidity=200_000)
    assert any("Daily loss" in p for p in buy(eng, clock, pool="p3")["problems"])


def test_summary_and_cash_tracking():
    eng, _store, clock = make()
    s0 = eng.summary()
    assert s0["mode"] == "PAPER" and s0["equity"] == s0["capital"]
    buy(eng, clock)
    s1 = eng.summary()
    assert s1["open_positions"] == 1 and s1["cash"] < s0["cash"]


def test_store_settings_snapshots_watchlist():
    store = Store(":memory:", clock=Clock())
    store.set_setting("max_position_usd", 50)
    assert store.get_setting("max_position_usd") == 50
    try:
        store.set_setting("nope", 1)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert store.add_snapshot("k", 1000, 1, 10, 5)
    assert not store.add_snapshot("k", 1000, 1, 10, 5)  # throttled
    store.watch_add("solana:p", "solana", "p", "tok", "S", "N")
    assert len(store.watchlist()) == 1
    store.watch_remove("solana:p")
    assert store.watchlist() == []
    store.wallet_add("w1", "solana", "whale")
    assert store.wallets("solana")[0]["label"] == "whale"
