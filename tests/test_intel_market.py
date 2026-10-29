"""Tests for pump/dump classification, the read-only portfolio client and the dashboard server."""
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from marketbot import dashboard, net, portfolio, pumpwatch

# ---------------------------------------------------------------- pump watch

def _kline(close, high=None, vol=100_000.0, buy_share=0.5, i=0):
    high = high if high is not None else close * 1.002
    return [i, close, high, close * 0.998, close, 1.0, i, vol, 10, 0.0, vol * buy_share, 0]


def _series(closes, vols=None, shares=None):
    rows = []
    for i, c in enumerate(closes):
        rows.append(_kline(c, vol=(vols[i] if vols else 100_000.0), buy_share=(shares[i] if shares else 0.5), i=i))
    rows.append(_kline(closes[-1], i=len(closes)))  # forming candle, must be ignored
    return rows


def test_quiet_market_is_quiet():
    rows = _series([100.0 + (i % 2) * 0.1 for i in range(50)])
    assert pumpwatch.analyze_klines("AAAUSDT", "AAA", rows).label == "QUIET"


def test_volume_spike_with_buyers_is_pump_starting():
    closes = [100.0 + (i % 2) * 0.4 for i in range(48)] + [100.0, 102.5]
    vols = [100_000.0] * 48 + [100_000.0, 600_000.0]
    shares = [0.5] * 48 + [0.5, 0.75]
    row = pumpwatch.analyze_klines("AAAUSDT", "AAA", _series(closes, vols, shares))
    assert row.label == "PUMP_STARTING"
    assert row.rel_volume > 2.5


def test_first_big_candle_after_a_calm_spell_is_pump_starting_not_extended():
    # Perfectly flat history pins RSI at 100 on the first up candle. That must not hide the
    # early stage of a move behind an "already extended" label.
    closes = [100.0] * 48 + [100.0, 103.0]
    vols = [100_000.0] * 48 + [100_000.0, 600_000.0]
    shares = [0.5] * 48 + [0.5, 0.76]
    row = pumpwatch.analyze_klines("AAAUSDT", "AAA", _series(closes, vols, shares))
    assert row.rsi > 90
    assert row.label == "PUMP_STARTING"


def test_big_24h_run_is_extended_not_a_buy_signal():
    closes = [100.0 + i * 1.2 for i in range(50)]
    row = pumpwatch.analyze_klines("AAAUSDT", "AAA", _series(closes))
    assert row.label in {"PUMP_EXTENDED", "DUMP_RISK"}
    assert row.label != "PUMP_STARTING"


def test_sharp_drop_on_volume_is_dumping():
    closes = [100.0] * 49 + [90.0]
    vols = [100_000.0] * 49 + [500_000.0]
    row = pumpwatch.analyze_klines("AAAUSDT", "AAA", _series(closes, vols))
    assert row.label == "DUMPING"


def test_extended_run_rolling_over_is_dump_risk():
    closes = [100.0 + i * 3.0 for i in range(47)] + [245.0, 235.0, 225.0]
    shares = [0.5] * 47 + [0.4, 0.35, 0.3]
    row = pumpwatch.analyze_klines("AAAUSDT", "AAA", _series(closes, shares=shares))
    assert row.label == "DUMP_RISK"


def test_too_few_candles_returns_none():
    assert pumpwatch.analyze_klines("AAAUSDT", "AAA", _series([100.0] * 10)) is None


def test_scan_pumps_skips_stablecoins_and_leveraged_tokens_but_not_jup():
    tickers = [{"symbol": s, "quoteVolume": "5000000", "priceChangePercent": "1"}
               for s in ("BTCUSDT", "BTCUPUSDT", "USDCUSDT", "JUPUSDT", "PEPEUSDT")]
    asked = []

    def getter(url, params=None, **kw):
        if url.endswith("/ticker/24hr"):
            return tickers
        asked.append(params["symbol"])
        return _series([100.0] * 50)

    rows, _warnings = pumpwatch.scan_pumps(getter, pause=0)
    assert sorted(asked) == ["BTCUSDT", "JUPUSDT", "PEPEUSDT"]
    assert next(r for r in rows if r.base == "PEPE").is_meme is True


def test_scan_pumps_collects_failures_as_warnings():
    def getter(url, params=None, **kw):
        if url.endswith("/ticker/24hr"):
            return [{"symbol": "AAAUSDT", "quoteVolume": "5000000", "priceChangePercent": "1"}]
        raise net.HttpError("HTTP 418", status=418)

    rows, warnings = pumpwatch.scan_pumps(getter, pause=0)
    assert rows == [] and any("AAAUSDT" in w for w in warnings)


# ---------------------------------------------------------------- portfolio

class FakeBinance:
    """Pretends to be Binance. Records every request so tests can check what was sent."""

    def __init__(self, restrictions=None, trades=None, balances=None, restrictions_error=False):
        self.calls = []
        self.restrictions = restrictions if restrictions is not None else {
            "enableReading": True, "enableWithdrawals": False, "enableSpotAndMarginTrading": False,
            "enableMargin": False, "enableFutures": False, "enableInternalTransfer": False,
            "permitsUniversalTransfer": False, "ipRestrict": True,
        }
        self.restrictions_error = restrictions_error
        self.trades = trades or {}
        self.balances = balances or [
            {"asset": "BTC", "free": "0.5", "locked": "0"},
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "LDBTC", "free": "0.5", "locked": "0"},
            {"asset": "PEPE", "free": "1000000", "locked": "0"},
            {"asset": "WEIRD", "free": "5", "locked": "0"},
        ]

    def __call__(self, url, params=None, headers=None, **kw):
        self.calls.append((url, params, headers))
        path = urllib.parse.urlparse(url).path
        if path == "/api/v3/time":
            return {"serverTime": 1_700_000_000_000}
        if path == "/api/v3/ticker/price":
            return [{"symbol": "BTCUSDT", "price": "60000"}, {"symbol": "PEPEUSDT", "price": "0.00001"},
                    {"symbol": "BNBUSDT", "price": "500"}]
        if path == "/api/v3/ticker/24hr":
            return [{"symbol": "BTCUSDT", "priceChangePercent": "2.0", "quoteVolume": "1"},
                    {"symbol": "PEPEUSDT", "priceChangePercent": "-10.0", "quoteVolume": "1"}]
        if path == "/sapi/v1/account/apiRestrictions":
            if self.restrictions_error:
                raise net.HttpError("HTTP 401", status=401)
            return self.restrictions
        if path == "/api/v3/account":
            return {"balances": self.balances}
        if path == "/api/v3/myTrades":
            symbol = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["symbol"][0]
            return self.trades.get(symbol, [])
        raise AssertionError(path)


def _client(fake):
    return portfolio.BinanceReadOnlyClient("KEY123", "SECRET456", getter=fake, clock=lambda: 1_700_000_000.0)


def test_client_has_no_way_to_trade_or_withdraw():
    names = {n for n in dir(portfolio.BinanceReadOnlyClient) if not n.startswith("_")}
    assert names == {"api_restrictions", "account", "my_trades", "prices", "tickers_24h"}
    assert "SECRET456" not in repr(_client(FakeBinance()))


def test_signed_requests_are_correctly_signed_and_use_key_header():
    fake = FakeBinance()
    _client(fake).account()
    url, _, headers = next(c for c in fake.calls if "/api/v3/account" in c[0])
    assert headers == {"X-MBX-APIKEY": "KEY123"}
    query, signature = url.split("?", 1)[1].rsplit("&signature=", 1)
    expected = hmac.new(b"SECRET456", query.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
    assert "omitZeroBalances=true" in query and "timestamp=" in query


def test_key_with_withdrawals_is_refused():
    fake = FakeBinance(restrictions={"enableReading": True, "enableWithdrawals": True})
    try:
        portfolio.build_snapshot(_client(fake))
        raise AssertionError("expected UnsafeKeyError")
    except portfolio.UnsafeKeyError as exc:
        assert "WITHDRAWALS" in str(exc)
    assert not any("/api/v3/account" in c[0] for c in fake.calls), "balances must not be read"


def test_unverifiable_key_is_refused_unless_overridden():
    fake = FakeBinance(restrictions_error=True)
    try:
        portfolio.build_snapshot(_client(fake))
        raise AssertionError("expected UnsafeKeyError")
    except portfolio.UnsafeKeyError:
        pass
    snap = portfolio.build_snapshot(_client(FakeBinance(restrictions_error=True)), with_pnl=False,
                                    allow_unverified_key=True)
    assert any("could not be verified" in w for w in snap["warnings"])


def test_permission_warnings():
    fake = FakeBinance(restrictions={"enableReading": True, "enableWithdrawals": False,
                                     "enableSpotAndMarginTrading": True, "ipRestrict": False})
    snap = portfolio.build_snapshot(_client(fake), with_pnl=False)
    text = " ".join(snap["warnings"])
    assert "place spot/margin trades" in text and "not restricted to your IP" in text


def test_snapshot_values_merges_earn_and_reports_unpriced():
    snap = portfolio.build_snapshot(_client(FakeBinance()), with_pnl=False)
    by_asset = {h["asset"]: h for h in snap["holdings"]}
    assert abs(by_asset["BTC"]["quantity"] - 1.0) < 1e-9  # BTC + LDBTC (Simple Earn)
    assert abs(by_asset["BTC"]["value_usd"] - 60_000.0) < 1e-6
    assert abs(snap["total_usd"] - (60_000.0 + 1_000.0 + 10.0)) < 1e-6
    assert [u["asset"] for u in snap["unvalued"]] == ["WEIRD"]
    assert abs(sum(h["weight_pct"] for h in snap["holdings"]) - 100.0) < 1e-6
    assert any("BTC is" in f for f in snap["flags"])  # concentration warning (84%)


def test_cost_basis_average_cost_and_realized_pnl():
    trades = [
        {"id": 1, "time": 1, "qty": "2", "quoteQty": "200", "isBuyer": True, "commission": "0", "commissionAsset": "BTC"},
        {"id": 2, "time": 2, "qty": "2", "quoteQty": "400", "isBuyer": True, "commission": "0", "commissionAsset": "BTC"},
        {"id": 3, "time": 3, "qty": "1", "quoteQty": "300", "isBuyer": False, "commission": "0", "commissionAsset": "USDT"},
    ]
    out = portfolio.compute_cost_basis(trades, "BTC")
    assert abs(out["avg_cost"] - 150.0) < 1e-9
    assert abs(out["tracked_qty"] - 3.0) < 1e-9
    assert abs(out["cost"] - 450.0) < 1e-9
    assert abs(out["realized_pnl"] - 150.0) < 1e-9  # sold 1 at 300, cost 150


def test_cost_basis_subtracts_fee_paid_in_the_coin_and_handles_oversell():
    trades = [
        {"id": 1, "time": 1, "qty": "1", "quoteQty": "100", "isBuyer": True, "commission": "0.001", "commissionAsset": "BTC"},
        {"id": 2, "time": 2, "qty": "5", "quoteQty": "600", "isBuyer": False, "commission": "0", "commissionAsset": "USDT"},
    ]
    out = portfolio.compute_cost_basis(trades, "BTC")
    assert out["tracked_qty"] == 0.0 and out["cost"] == 0.0
    assert out["avg_cost"] is None


def test_pnl_reports_coverage_when_history_explains_only_part_of_the_position():
    trades = {"BTCUSDT": [{"id": 1, "time": 1, "qty": "0.25", "quoteQty": "10000", "isBuyer": True,
                           "commission": "0", "commissionAsset": "BTC"}]}
    snap = portfolio.build_snapshot(_client(FakeBinance(trades=trades)))
    btc = next(h for h in snap["holdings"] if h["asset"] == "BTC")
    assert abs(btc["basis_coverage_pct"] - 25.0) < 1e-6
    assert abs(btc["unrealized_pnl"] - (0.25 * 60_000 - 10_000)) < 1e-6
    pepe = next(h for h in snap["holdings"] if h["asset"] == "PEPE")
    assert pepe["unrealized_pnl"] is None and pepe["basis_coverage_pct"] == 0.0


def test_usd_price_routes():
    prices = {"BTCUSDT": 60000.0, "ETHBTC": 0.05}
    assert portfolio.usd_price("USDT", prices) == (1.0, None)
    assert portfolio.usd_price("ETH", prices)[0] == 3000.0
    assert portfolio.usd_price("NOPE", prices) == (None, None)


# ---------------------------------------------------------------- dashboard server

def test_jsonable_removes_nan_and_inf():
    out = dashboard.jsonable({"a": float("nan"), "b": [float("inf"), 1.0], "c": {"d": float("-inf")}})
    assert json.dumps(out, allow_nan=False) == '{"a": null, "b": [null, 1.0], "c": {"d": null}}'


def test_cache_shares_one_upstream_call():
    clock = [0.0]
    cache = dashboard.TTLCache(clock=lambda: clock[0])
    calls = []
    for _ in range(3):
        cache.get("k", 60, lambda: calls.append(1) or "v")
    assert len(calls) == 1
    clock[0] = 61.0
    cache.get("k", 60, lambda: calls.append(1) or "v")
    assert len(calls) == 2


def test_server_refuses_non_loopback_bind():
    try:
        dashboard.create_server(dashboard.Services(), host="0.0.0.0", port=0)
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert "127.0.0.1" in str(exc)


def _serve(services):
    server = dashboard.create_server(services, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _fetch(url, headers=None, method="GET"):
    request = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_dashboard_end_to_end_with_fake_network():
    fake = FakeBinance()

    def getter(url, params=None, headers=None, **kw):
        if "dexscreener" in url or "gopluslabs" in url or "coingecko" in url:
            if "token-boosts/top" in url:
                return [{"chainId": "base", "tokenAddress": "0xAbC0000000000000000000000000000000000001"}]
            if "token-boosts/latest" in url or "token-profiles" in url:
                return []
            if "/tokens/v1/base/" in url:
                return [{
                    "chainId": "base", "dexId": "uniswap", "pairAddress": "0x" + "p" * 40,
                    "baseToken": {"address": "0xAbC0000000000000000000000000000000000001", "symbol": "<img src=x onerror=alert(1)>", "name": "Evil"},
                    "priceUsd": "0.5", "liquidity": {"usd": 400000}, "volume": {"h24": 900000, "h1": 50000},
                    "priceChange": {"h1": 8, "h6": 20, "h24": 30}, "txns": {"h1": {"buys": 90, "sells": 40}},
                    "fdv": 9000000}]
            if "token_security" in url:
                return {"result": {"0xabc0000000000000000000000000000000000001": {"is_honeypot": "1"}}}
            if "coins/markets" in url:
                return [{"id": "pepe", "symbol": "pepe", "name": "Pepe", "current_price": 1e-5, "total_volume": 5e8,
                         "price_change_percentage_24h_in_currency": 3.0}]
            if "search/trending" in url:
                return {"coins": [{"item": {"id": "x", "symbol": "x", "name": "X", "data": {}}}]}
            raise net.HttpError("unexpected " + url)
        return fake(url, params=params, headers=headers)

    services = dashboard.Services(getter=getter, credentials=("KEY123", "SECRET456"))
    server, base = _serve(services)
    try:
        status, headers, body = _fetch(base + "/")
        assert status == 200 and b"MemeScope" in body
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        for name in ("app.js", "charts.js", "legacy.js", "views.js", "views2.js", "boot.js"):
            script = _fetch(base + "/" + name)[2]
            assert script and b"innerHTML" not in script and b"insertAdjacentHTML" not in script and b"eval(" not in script

        status, _, body = _fetch(base + "/api/memes")
        data = json.loads(body)
        assert status == 200
        token = data["tokens"][0]
        assert token["risk"]["level"] == "CRITICAL" and token["verdict"] == "AVOID"
        assert data["established"][0]["on_binance"] is True  # PEPEUSDT is in the fake Binance prices

        status, _, body = _fetch(base + "/api/portfolio")
        snap = json.loads(body)
        assert status == 200 and snap["configured"] is True and snap["total_usd"] > 0

        assert _fetch(base + "/api/news?q=" + urllib.parse.quote("<script>"))[0] == 400
        assert _fetch(base + "/api/nope")[0] == 404
        assert _fetch(base + "/", headers={"Host": "evil.example.com"})[0] == 403
        assert _fetch(base + "/api/portfolio", headers={"Host": "evil.example.com:8765"})[0] == 403
        assert _fetch(base + "/api/status", method="POST")[0] in {403, 405, 501}
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_portfolio_unconfigured_and_unsafe_key():
    server, base = _serve(dashboard.Services(getter=FakeBinance(), credentials=None))
    try:
        assert json.loads(_fetch(base + "/api/portfolio")[2]) == {"configured": False}
    finally:
        server.shutdown()
        server.server_close()
    unsafe = FakeBinance(restrictions={"enableReading": True, "enableWithdrawals": True})
    server, base = _serve(dashboard.Services(getter=unsafe, credentials=("K", "S")))
    try:
        status, _, body = _fetch(base + "/api/portfolio")
        assert status == 403 and json.loads(body)["unsafe_key"] is True
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- review regressions: portfolio

def _buy(i, qty, quote, t=None, fee="0", fee_asset="USDT"):
    return {"id": i, "time": t or i, "qty": str(qty), "quoteQty": str(quote), "isBuyer": True,
            "commission": fee, "commissionAsset": fee_asset}


def _sell(i, qty, quote, t=None, fee="0", fee_asset="USDT"):
    row = _buy(i, qty, quote, t, fee, fee_asset)
    row["isBuyer"] = False
    return row


def _snapshot_for(balances, trades, prices_extra=None):
    fake = FakeBinance(balances=balances, trades=trades)
    return portfolio.build_snapshot(_client(fake)), fake


def test_unrealized_pnl_is_not_invented_when_part_of_the_position_left_the_account():
    # Bought 10 at $100 (cost 1000), 5 were withdrawn, price still ~$100: PnL must be ~0.
    trades = {"BTCUSDT": [_buy(1, 10, 600_000)]}  # bought 10 BTC at 60,000
    balances = [{"asset": "BTC", "free": "5", "locked": "0"}]
    snap, _ = _snapshot_for(balances, trades)
    btc = snap["holdings"][0]
    assert abs(btc["avg_cost"] - 60_000.0) < 1e-6
    assert abs(btc["unrealized_pnl"]) < 1e-6
    assert abs(btc["basis_coverage_pct"] - 100.0) < 1e-6


def test_buy_on_one_quote_and_sell_on_another_net_against_each_other():
    trades = {"BTCUSDT": [_buy(1, 1, 50_000, t=1)], "BTCUSDC": [_sell(1, 1, 60_000, t=2)]}
    fake = FakeBinance(balances=[{"asset": "BTC", "free": "0.0001", "locked": "0"}], trades=trades)

    original = fake.__call__.__func__

    def with_usdc_price(self, url, params=None, headers=None, **kw):
        path = urllib.parse.urlparse(url).path
        if path == "/api/v3/ticker/price":
            return [{"symbol": "BTCUSDT", "price": "60000"}, {"symbol": "BTCUSDC", "price": "60000"}]
        return original(self, url, params=params, headers=headers, **kw)

    fake.__class__ = type("F", (FakeBinance,), {"__call__": with_usdc_price})
    btc = portfolio.build_snapshot(_client(fake))["holdings"][0]
    assert abs(btc["realized_pnl"] - 10_000.0) < 1e-6
    assert btc["avg_cost"] is None or btc["avg_cost"] < 1e-3 or btc["basis_coverage_pct"] == 0.0


def test_bnb_fee_on_a_bnb_trade_is_not_double_counted():
    trades = [_buy(1, 10, 6000, fee="0.01", fee_asset="BNB")]
    out = portfolio.compute_cost_basis(trades, "BNB", bnb_usdt=600.0)
    assert abs(out["tracked_qty"] - 9.99) < 1e-9
    assert abs(out["cost"] - 6000.0) < 1e-9


def test_malformed_trade_rows_are_skipped_not_fatal():
    trades = [{"id": 1, "time": 1, "qty": "1", "isBuyer": True}, _buy(2, 1, 100), "garbage", None]
    out = portfolio.compute_cost_basis(trades, "BTC")
    assert abs(out["cost"] - 100.0) < 1e-9


def test_a_minus_100_percent_day_does_not_break_the_snapshot():
    class Crash(FakeBinance):
        def __call__(self, url, params=None, headers=None, **kw):
            if urllib.parse.urlparse(url).path == "/api/v3/ticker/24hr":
                return [{"symbol": "PEPEUSDT", "priceChangePercent": "-100.000", "quoteVolume": "1"}]
            return super().__call__(url, params=params, headers=headers, **kw)

    snap = portfolio.build_snapshot(_client(Crash()), with_pnl=False)
    pepe = next(h for h in snap["holdings"] if h["asset"] == "PEPE")
    assert pepe["change_24h_pct"] is None


def test_key_reply_without_a_withdrawal_field_is_not_treated_as_safe():
    fake = FakeBinance(restrictions={"enableReading": True})
    try:
        portfolio.build_snapshot(_client(fake), with_pnl=False)
        raise AssertionError("expected UnsafeKeyError")
    except portfolio.UnsafeKeyError:
        pass
    assert not any("/api/v3/account" in c[0] for c in fake.calls)


def test_clock_sync_failure_is_not_cached_and_minus_1021_resyncs_once():
    state = {"time_calls": 0, "account_calls": 0}

    def getter(url, params=None, headers=None, **kw):
        path = urllib.parse.urlparse(url).path
        if path == "/api/v3/time":
            state["time_calls"] += 1
            if state["time_calls"] == 1:
                raise net.HttpError("down")
            return {"serverTime": 1_700_000_000_000}
        state["account_calls"] += 1
        if state["account_calls"] == 1:
            raise net.HttpError('HTTP 400: {"code":-1021,"msg":"Timestamp outside recvWindow"}', status=400)
        return {"ok": True}

    client = portfolio.BinanceReadOnlyClient("K", "S", getter=getter, clock=lambda: 1_700_000_000.0)
    assert client.account() == {"ok": True}
    assert state["time_calls"] == 2  # first sync failed and was retried, not remembered as offset 0


def test_signed_calls_never_follow_redirects_and_public_ones_may():
    seen = []

    def getter(url, params=None, headers=None, **kw):
        seen.append((urllib.parse.urlparse(url).path, headers, kw))
        return {"serverTime": 1} if url.endswith("/time") else {}

    client = portfolio.BinanceReadOnlyClient("K", "S", getter=getter, clock=lambda: 1.0)
    client.account()
    signed = next(s for s in seen if s[0] == "/api/v3/account")
    assert signed[2].get("follow_redirects") is False and signed[1] == {"X-MBX-APIKEY": "K"}


def test_trade_history_is_read_from_the_start_and_truncation_is_reported():
    seen_from_ids = []

    class Paged(FakeBinance):
        def __call__(self, url, params=None, headers=None, **kw):
            if urllib.parse.urlparse(url).path == "/api/v3/myTrades":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                from_id = int(q["fromId"][0])
                seen_from_ids.append(from_id)
                return [_buy(from_id + i, 1, 1) for i in range(1000)]  # always a full page
            return super().__call__(url, params=params, headers=headers, **kw)

    fake = Paged(balances=[{"asset": "BTC", "free": "1", "locked": "0"}])
    snap = portfolio.build_snapshot(_client(fake))
    assert seen_from_ids[0] == 0 and seen_from_ids[1] == 1000
    assert any("very long trade history" in w for w in snap["warnings"])


# ---------------------------------------------------------------- review regressions: dashboard

def test_cache_is_bounded():
    cache = dashboard.TTLCache(max_entries=5)
    for i in range(50):
        cache.get(f"k{i}", 60, lambda: "v")
    assert len(cache._data) <= 6 and len(cache._locks) <= 6


def test_cross_site_requests_to_the_api_are_rejected():
    server, base = _serve(dashboard.Services(getter=FakeBinance(), credentials=None))
    try:
        assert _fetch(base + "/api/status", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert _fetch(base + "/api/status", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200
        assert _fetch(base + "/api/status")[0] == 200  # non-browser clients send no such header
    finally:
        server.shutdown()
        server.server_close()
