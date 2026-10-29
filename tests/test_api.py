import json
import threading
import urllib.error
import urllib.request

from gecko_fakes import POOL, make_getter

from marketbot import dashboard


def serve():
    services = dashboard.Services(getter=make_getter(), text_getter=lambda *a, **k: "<rss><channel/></rss>")
    server = dashboard.create_server(services, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return services, f"http://127.0.0.1:{server.server_address[1]}"


def call(base, path, method="GET", body=None, token=None, headers=None, raw=None):
    h = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    if token:
        h["X-Desk-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def token(base):
    return call(base, "/api/session")[1]["token"]


def test_post_requires_token_origin_json():
    _, base = serve()
    assert call(base, "/api/halt", "POST", {})[0] == 403
    assert call(base, "/api/halt", "POST", {}, token="wrong")[0] == 403
    t = token(base)
    assert call(base, "/api/halt", "POST", {}, token=t, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(base, "/api/halt", "POST", token=t, raw=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    assert call(base, "/api/halt", "POST", token=t, raw=b"{bad", headers={"Content-Type": "application/json"})[0] == 400
    assert call(base, "/api/halt", "POST", token=t, raw=b"[1]", headers={"Content-Type": "application/json"})[0] == 400
    assert call(base, "/api/halt", "POST", {}, token=t, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(base, "/api/halt", "POST", {}, token=t, headers={"Host": "evil.example"})[0] == 403


def test_halt_resume_and_meta():
    _, base = serve()
    t = token(base)
    assert call(base, "/api/halt", "POST", {"reason": "test"}, token=t)[1]["halted"] is True
    meta = call(base, "/api/meta")[1]
    assert meta["halted"] and meta["mode"] == "PAPER" and "Not implemented" in meta["live_trading"]
    assert call(base, "/api/resume", "POST", {}, token=t)[1]["halted"] is False


def test_settings_are_bounded_and_allowlisted():
    _, base = serve()
    t = token(base)
    assert call(base, "/api/settings", "POST", {"max_slippage_pct": 0}, token=t)[0] == 400
    assert call(base, "/api/settings", "POST", {"halted": False}, token=t)[0] == 400
    assert call(base, "/api/settings", "POST", {"chain": "dogechain"}, token=t)[0] == 400
    ok = call(base, "/api/settings", "POST", {"max_position_usd": 50}, token=t)
    assert ok[0] == 200 and ok[1]["settings"]["max_position_usd"] == 50


def test_scan_and_investigate_validation():
    _, base = serve()
    status, data = call(base, "/api/scan?chain=solana&mode=fresh")
    assert status == 200 and data["counts"]["fetched"] >= 1
    assert call(base, "/api/scan?chain=nope")[0] == 400
    assert call(base, "/api/scan?mode=nope")[0] == 400
    assert call(base, "/api/investigate?chain=solana&id=../x")[0] == 400
    status, inv = call(base, f"/api/investigate?chain=solana&id={POOL}")
    assert status == 200 and inv["ai"]["disclaimer"]


def test_watchlist_wallet_and_paper_flow():
    _, base = serve()
    t = token(base)
    assert call(base, "/api/watchlist/add", "POST", {"chain": "solana", "id": POOL}, token=t)[0] == 200
    assert len(call(base, "/api/watchlist")[1]["items"]) == 1
    assert call(base, "/api/wallets/add", "POST", {"chain": "solana", "address": "bad addr"}, token=t)[0] == 400
    buy = call(base, "/api/paper/buy", "POST", {"chain": "solana", "pool": POOL, "usd": 100}, token=t)[1]
    assert "ok" in buy
    if buy["ok"]:
        assert len(call(base, "/api/paper")[1]["positions"]) == 1
        assert call(base, "/api/paper/sell", "POST", {"key": f"solana:{POOL}"}, token=t)[1]["ok"]
    call(base, "/api/halt", "POST", {}, token=t)
    refused = call(base, "/api/paper/buy", "POST", {"chain": "solana", "pool": POOL, "usd": 100}, token=t)[1]
    assert refused["ok"] is False


def test_alert_test_without_channels_and_health():
    _, base = serve()
    t = token(base)
    assert call(base, "/api/alerts/test", "POST", {}, token=t)[1]["ok"] is False
    assert call(base, "/api/health")[1]["status"] in {"OK", "DEGRADED"}
    assert call(base, "/api/capabilities")[0] == 200


def test_review_regressions_validation():
    _, base = serve()
    t = token(base)
    assert call(base, "/api/wallets/add", "POST", {"chain": "solana", "address": "A" * 20 + "\n"}, token=t)[0] == 400
    assert call(base, "/api/settings", "POST", {"chain": []}, token=t)[0] == 400
    assert call(base, "/api/settings", "POST", {"watcher_enabled": "false"}, token=t)[0] == 400
    assert call(base, "/api/watchlist/add", "POST", {"chain": {}, "id": POOL}, token=t)[0] == 400


def test_paper_buy_blocked_without_security_data():
    _, base = serve()
    t = token(base)
    r = call(base, "/api/paper/buy", "POST", {"chain": "solana", "pool": POOL, "usd": 100}, token=t)[1]
    assert r["ok"] is False and any("risky" in p.lower() or "missing" in p.lower() for p in r["problems"])
    assert call(base, "/api/paper")[1]["positions"] == []


def test_paper_capital_locked_after_trades():
    services, base = serve()
    t = token(base)
    assert call(base, "/api/settings", "POST", {"paper_capital_usd": 5000}, token=t)[0] == 200
    services.desk.store.execute("INSERT INTO paper_orders(ts,key,symbol,side,usd,qty,price,fee_usd,slippage_pct) VALUES(1,'k','S','buy',1,1,1,0,0)")
    assert call(base, "/api/settings", "POST", {"paper_capital_usd": 9000}, token=t)[0] == 400
