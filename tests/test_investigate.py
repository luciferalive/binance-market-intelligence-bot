from gecko_fakes import POOL, TOKEN, WALLET, make_getter, trade

from marketbot.investigate import Investigator, NotFound
from marketbot.store import Store


def make(**kw):
    store = Store(":memory:")
    getter = make_getter(**kw)
    return Investigator(getter, lambda *a, **k: "<rss><channel></channel></rss>", store), store, getter


def test_pool_and_token_address_both_resolve():
    inv, _, _ = make()
    assert inv.resolve("solana", POOL).pool_address == POOL
    assert inv.resolve("solana", TOKEN).token_address == TOKEN


def test_bad_address_is_rejected_before_any_request():
    inv, _, getter = make()
    try:
        inv.resolve("solana", "../etc/passwd")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert getter.calls == []


def test_missing_security_never_looks_safe():
    inv, _, _ = make()
    out = inv.investigate("solana", POOL)
    assert out["security"]["available"] is False
    assert out["overview"]["label"] != "CANDIDATE"
    assert any("security data was not available" in u.lower() for u in out["ai"]["unknown"])
    assert out["ai"]["disclaimer"]


def test_smart_money_matches_tracked_wallet_and_states_limits():
    trades = [trade("buy", 3000, WALLET, 1), trade("buy", 500, "other", 2), trade("sell", 100, WALLET, 3)]
    inv, store, _ = make(trades=trades)
    store.wallet_add(WALLET, "solana", "alpha")
    out = inv.investigate("solana", POOL, {"smart"})
    smart = out["smart"]
    assert smart["state"] == "Tracked wallets accumulating"
    assert smart["tracked_buyers"] == 1 and smart["large_count"] == 1
    assert smart["tracked_trades"][0]["label"] == "alpha"
    assert any("not connected" in s for s in smart["limits"])


def test_no_wallets_tracked_is_stated():
    inv, _, _ = make(trades=[trade("buy", 100, "x", 1)])
    assert inv.investigate("solana", POOL, {"smart"})["smart"]["state"] == "No wallets tracked"


def test_trades_failure_reported_not_hidden():
    inv, _, _ = make(fail=("/trades",))
    smart = inv.investigate("solana", POOL, {"smart"})["smart"]
    assert smart["available"] is False


def test_liquidity_tab_exit_table_and_history():
    inv, store, _ = make()
    out = inv.investigate("solana", POOL, {"liquidity"})
    liq = out["liquidity"]
    assert liq["exit_table"][0]["impact_pct"] > 0
    assert "estimate" in liq["exit_note"].lower()
    assert len(store.snapshots(out["key"])) == 1


def test_news_failure_and_social_not_connected():
    inv, _, _ = make()
    out = inv.investigate("solana", POOL, {"news"})
    assert "Not connected" in out["news"]["social"]


def test_chart_candles():
    inv, _, _ = make()
    out = inv.investigate("solana", POOL, {"chart"})
    assert len(out["chart"]["candles"]) == 30


def test_ai_never_says_buy():
    inv, _, _ = make()
    text = str(inv.investigate("solana", POOL)["ai"]).lower()
    assert "you should buy" not in text and "strong buy" not in text


def test_unknown_pool_raises_not_found():
    inv, _, _ = make()
    inv.getter = lambda *a, **k: {"data": []}
    try:
        inv.resolve("solana", POOL)
        raise AssertionError("expected NotFound")
    except NotFound:
        pass
