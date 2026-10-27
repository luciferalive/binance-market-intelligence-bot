"""Tests for the HTTP helper, settings, rug check, meme scoring and news tagging."""
import io
import os
import tempfile
import urllib.error

from marketbot import binance_public as bp
from marketbot import memecoins, net, news, rugcheck, settings

# ---------------------------------------------------------------- net

class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code):
    return urllib.error.HTTPError("https://x.test/a?signature=SECRET", code, "err", {}, io.BytesIO(b"nope"))


def test_get_json_retries_on_429_then_succeeds():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(429)
        return _Resp(b'{"ok": true}')

    out = net.get_json("https://x.test/a", urlopen=opener, sleep=lambda s: None)
    assert out == {"ok": True}
    assert len(calls) == 3


def test_get_json_does_not_retry_404_and_hides_query_string():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise _http_error(404)

    try:
        net.get_json("https://x.test/a?signature=SECRET", urlopen=opener, sleep=lambda s: None)
        raise AssertionError("expected HttpError")
    except net.HttpError as exc:
        assert "SECRET" not in str(exc)
        assert exc.status == 404
    assert len(calls) == 1


def test_get_json_gives_up_after_retries():
    def opener(request, timeout):
        raise urllib.error.URLError("dns failure")

    try:
        net.get_json("https://x.test/a", urlopen=opener, sleep=lambda s: None, retries=1)
        raise AssertionError("expected HttpError")
    except net.HttpError as exc:
        assert "dns failure" in str(exc)


def test_to_float_is_tolerant():
    assert net.to_float("$1,234.5") == 1234.5
    assert net.to_float("abc") is None
    assert net.to_float(None, 7.0) == 7.0
    assert net.to_float(float("nan")) is None
    assert net.to_float(True) is None


# ---------------------------------------------------------------- settings

def test_dotenv_loads_without_overriding_and_ignores_junk():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, ".env")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# comment\nMB_TEST_A=one\nexport MB_TEST_B='two words'\nbad line\nMB_TEST_C=\"3\"\n")
        os.environ.pop("MB_TEST_A", None)
        os.environ["MB_TEST_B"] = "keep"
        os.environ.pop("MB_TEST_C", None)
        loaded = settings.load_dotenv(path)
        assert os.environ["MB_TEST_A"] == "one"
        assert os.environ["MB_TEST_B"] == "keep"
        assert os.environ["MB_TEST_C"] == "3"
        assert sorted(loaded) == ["MB_TEST_A", "MB_TEST_C"]
        for name in ("MB_TEST_A", "MB_TEST_B", "MB_TEST_C"):
            os.environ.pop(name, None)


def test_dotenv_missing_file_is_fine():
    assert settings.load_dotenv("/nonexistent/.env") == []


# ---------------------------------------------------------------- binance helpers

def test_leveraged_detection_does_not_remove_real_coins():
    bases = {"BTC", "BTCUP", "BTCDOWN", "JUP", "SYRUP", "ETH", "ETHBULL"}
    found = bp.leveraged_bases(bases)
    assert found == {"BTCUP", "BTCDOWN", "ETHBULL"}
    assert "JUP" not in found and "SYRUP" not in found


def test_normalize_base_strips_multipliers_only():
    assert bp.normalize_base("1000SATS") == "SATS"
    assert bp.normalize_base("1MBABYDOGE") == "BABYDOGE"
    assert bp.normalize_base("1INCH") == "1INCH"
    assert bp.normalize_base("PEPE") == "PEPE"


# ---------------------------------------------------------------- rug check

def _clean_evm():
    return {
        "is_honeypot": "0", "is_open_source": "1", "is_mintable": "0", "is_proxy": "0",
        "buy_tax": "0", "sell_tax": "0", "holder_count": "5000", "creator_percent": "0.01",
        "holders": [{"address": "0xaaa", "percent": "0.03"}] * 10,
        "lp_holders": [{"address": "0xlock", "percent": "0.95", "is_locked": 1}],
        "dex": [],
    }


def _token(**kw):
    base = {"chain": "base", "address": "0xabc", "symbol": "TKN", "name": "Token",
            "pair_address": "0xpair", "liquidity_usd": 300_000.0, "volume_24h": 500_000.0,
            "age_hours": 24 * 30, "fdv": 5_000_000.0}
    base.update(kw)
    return memecoins.DexToken(**base)


def test_honeypot_is_critical_even_when_everything_else_is_clean():
    sec = _clean_evm()
    sec["is_honeypot"] = "1"
    report = rugcheck.assess_risk(_token(), sec, "base")
    assert report.level == "CRITICAL"
    assert report.score >= 90
    assert rugcheck.verdict(95, report.level) == "AVOID"


def test_extreme_sell_tax_is_critical():
    sec = _clean_evm()
    sec["sell_tax"] = "0.9"
    assert rugcheck.assess_risk(_token(), sec, "base").level == "CRITICAL"


def test_clean_established_token_is_low_but_never_called_safe():
    report = rugcheck.assess_risk(_token(), _clean_evm(), "base")
    assert report.level == "LOW"
    assert "not a safety guarantee" in report.summary


def test_missing_security_data_is_never_low():
    for chain in ("base", "solana", "someotherchain"):
        report = rugcheck.assess_risk(_token(chain=chain), None, chain)
        assert report.level in {"UNVERIFIED", "HIGH"}, chain
        assert report.verified is False
        assert any(f.code == "no_security_data" for f in report.flags)


def test_unlocked_liquidity_and_concentration_are_flagged():
    sec = _clean_evm()
    sec["lp_holders"] = [{"address": "0xdev", "percent": "1.0", "is_locked": 0}]
    sec["holders"] = [{"address": f"0x{i}", "percent": "0.08"} for i in range(10)]
    codes = {f.code for f in rugcheck.assess_risk(_token(), sec, "base").flags}
    assert {"lp_unlocked", "concentrated"} <= codes


def test_pool_and_burn_addresses_do_not_count_as_concentration():
    sec = _clean_evm()
    sec["holders"] = [
        {"address": "0xpair", "percent": "0.60"},
        {"address": "0x000000000000000000000000000000000000dead", "percent": "0.30"},
    ]
    codes = {f.code for f in rugcheck.assess_risk(_token(), sec, "base").flags}
    assert "concentrated" not in codes


def test_new_thin_pair_adds_market_risk():
    token = _token(liquidity_usd=4_000.0, age_hours=3, fdv=50_000_000.0)
    codes = {f.code for f in rugcheck.assess_risk(token, _clean_evm(), "base").flags}
    assert {"thin_liquidity", "brand_new", "liq_vs_valuation"} <= codes


def test_solana_mint_and_freeze_authority_flagged():
    sec = {"mintable": {"status": "1"}, "freezable": {"status": "1"}, "holder_count": "9000",
           "holders": [], "dex": []}
    codes = {f.code for f in rugcheck.assess_risk(_token(chain="solana"), sec, "solana").flags}
    assert {"mintable", "freezable"} <= codes


def test_fetch_security_batches_and_normalizes_addresses():
    seen = []

    def getter(url, params=None, **kw):
        seen.append((url, params["contract_addresses"]))
        return {"result": {a.lower(): {"is_honeypot": "0"} for a in params["contract_addresses"].split(",")}}

    addresses = [f"0xABC{i:02d}" for i in range(23)]
    out = rugcheck.fetch_security("base", addresses, getter)
    assert len(seen) == 3  # 10 + 10 + 3
    assert all("/token_security/8453" in url for url, _ in seen)
    assert len(out) == 23


def test_verdict_never_says_buy():
    for level in ("CRITICAL", "HIGH", "UNVERIFIED", "MEDIUM", "LOW"):
        for heat in (0, 50, 100):
            assert rugcheck.verdict(heat, level) in {"AVOID", "HIGH_RISK", "SKIP", "WATCH"}
    assert rugcheck.verdict(90, "UNVERIFIED") == "HIGH_RISK"
    assert rugcheck.verdict(90, "LOW") == "WATCH"


# ---------------------------------------------------------------- meme coins

def _pair(**kw):
    pair = {
        "chainId": "solana", "dexId": "raydium", "pairAddress": "P" * 44,
        "baseToken": {"address": "TokenAddr" + "1" * 30, "symbol": "WIF2", "name": "Dog Hat"},
        "priceUsd": "0.0123", "pairCreatedAt": None,
        "liquidity": {"usd": 250000}, "volume": {"h24": 900000, "h1": 80000},
        "priceChange": {"m5": 1.0, "h1": 12.0, "h6": 40.0, "h24": 85.0},
        "txns": {"h1": {"buys": 300, "sells": 120}, "h24": {"buys": 5000, "sells": 3000}},
        "fdv": 12_000_000, "info": {}, "boosts": {"active": 0},
        "url": "javascript:alert(1)",
    }
    pair.update(kw)
    return pair


def test_parse_pair_builds_its_own_link_and_ignores_hostile_url():
    token = memecoins.parse_pair(_pair())
    assert token.url == "https://dexscreener.com/solana/" + "P" * 44
    assert "javascript" not in token.url


def test_parse_pair_rejects_hostile_chain_or_pair_for_link():
    token = memecoins.parse_pair(_pair(chainId="so lana/../x", pairAddress="../../evil"))
    assert token.url == ""


def test_heat_rewards_real_momentum_and_penalises_chasing():
    good = memecoins.parse_pair(_pair())
    score, reasons = memecoins.score_heat(good)
    assert score >= 60 and reasons
    chased = memecoins.parse_pair(_pair(priceChange={"h1": 5, "h6": 100, "h24": 900}))
    assert memecoins.score_heat(chased)[0] < score


def test_heat_is_bounded_and_tolerates_missing_fields():
    empty = memecoins.DexToken(chain="x", address="a", symbol="S", name="N")
    score, _ = memecoins.score_heat(empty)
    assert 0 <= score <= 100


def test_dex_candidates_filters_thin_pools_and_survives_a_dead_endpoint():
    good = _pair()
    thin = _pair(baseToken={"address": "Thin" + "2" * 30, "symbol": "THIN", "name": "t"}, liquidity={"usd": 500})

    def getter(url, params=None, **kw):
        if "token-boosts/top" in url:
            return [{"chainId": "solana", "tokenAddress": good["baseToken"]["address"]},
                    {"chainId": "solana", "tokenAddress": thin["baseToken"]["address"]}]
        if "token-boosts/latest" in url:
            raise net.HttpError("HTTP 500", status=500)
        if "token-profiles" in url:
            return []
        if "/tokens/v1/solana/" in url:
            return [good, thin]
        raise AssertionError(url)

    tokens, warnings = memecoins.fetch_dex_candidates(getter)
    assert [t.symbol for t in tokens] == ["WIF2"]
    assert any("token-boosts/latest" in w for w in warnings)


def test_best_pair_keeps_the_most_liquid():
    a = _pair(pairAddress="A" * 44, liquidity={"usd": 1000})
    b = _pair(pairAddress="B" * 44, liquidity={"usd": 9000})
    best = memecoins._best_pairs([a, b])
    assert len(best) == 1 and next(iter(best.values()))["pairAddress"] == "B" * 44


def test_evm_addresses_are_merged_case_insensitively():
    a = _pair(chainId="base", baseToken={"address": "0xABCDEF", "symbol": "A", "name": "a"})
    b = _pair(chainId="base", baseToken={"address": "0xabcdef", "symbol": "A", "name": "a"})
    assert len(memecoins._best_pairs([a, b])) == 1


def test_mark_binance_listed():
    rows = memecoins.mark_binance_listed([{"symbol": "pepe"}, {"symbol": "NOPE"}], {"PEPE"})
    assert rows[0]["on_binance"] is True and rows[1]["on_binance"] is False


# ---------------------------------------------------------------- news

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Binance will list FOO token - CoinDesk</title><link>https://example.com/a</link>
<pubDate>Mon, 26 Oct 2026 08:00:00 GMT</pubDate><source url="https://coindesk.com">CoinDesk</source></item>
<item><title>Exchange to delist FOO next week - Decrypt</title><link>javascript:alert(1)</link></item>
<item><title>FOO partners with big company</title><link>https://example.com/c</link></item>
</channel></rss>"""


def test_rss_parse_tags_and_link_safety():
    items = news.parse_rss(RSS)
    assert items[0].title == "Binance will list FOO token"
    assert items[0].source == "CoinDesk"
    assert "listing" in items[0].tags
    assert items[1].link == ""
    assert "negative" in items[1].tags and "listing" not in items[1].tags
    assert "partnership" in items[2].tags


def test_rss_with_entities_is_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
    try:
        news.parse_rss(bomb)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_news_summary_tone():
    positive = news.summarize(news.parse_rss(RSS)[:1] + news.parse_rss(RSS)[2:])
    assert positive["tone"] == "positive"
    negative = news.summarize([news.Headline("Exchange hacked, funds stolen", "x", "", "", news.tag_headline("Exchange hacked, funds stolen"))])
    assert negative["tone"] == "negative"


def test_fetch_headlines_sanitises_query():
    seen = {}

    def text_getter(url, params=None, **kw):
        seen.update(params)
        return RSS

    news.fetch_headlines("PEPE <script>&", text_getter)
    assert "<" not in seen["q"] and "&" not in seen["q"]


# ---------------------------------------------------------------- review regressions: rug check

def _level(changes, chain="base"):
    sec = _clean_evm()
    sec.update(changes)
    return rugcheck.assess_risk(_token(), sec, chain).level


def test_one_serious_flag_cannot_be_averaged_into_low():
    assert _level({"sell_tax": "0.49"}) == "HIGH"
    assert _level({"hidden_owner": "1"}) == "HIGH"
    assert _level({"lp_holders": [{"address": "0xdev", "percent": "1", "is_locked": 0}]}) == "HIGH"
    assert _level({"is_mintable": "1", "transfer_pausable": "1", "slippage_modifiable": "1"}) == "HIGH"
    assert _level({"is_open_source": "0", "is_mintable": "1"}) == "HIGH"
    assert _level({"is_open_source": "0"}) == "MEDIUM"  # one high flag is never LOW


def test_null_or_blank_fields_are_unknown_not_safe():
    assert _level({"is_honeypot": None}) == "UNVERIFIED"
    assert _level({"sell_tax": ""}) == "UNVERIFIED"
    assert rugcheck.assess_risk(_token(), {"is_honeypot": "0"}, "base").level == "UNVERIFIED"
    assert rugcheck.assess_risk(_token(chain="solana"), {"holder_count": "9999"}, "solana").level == "UNVERIFIED"


def test_missing_holder_or_liquidity_lock_data_keeps_token_out_of_low():
    assert _level({"holder_count": None}) == "MEDIUM"
    assert _level({"lp_holders": {"a": 1}}) == "MEDIUM"
    assert _level({"holders": "oops"}) == "MEDIUM"


def test_oddly_shaped_provider_data_never_crashes_and_never_reads_as_clean():
    for changes in ({"dex": {"a": 1}}, {"dex": ["x"]}, {"holders": ["a", "b"]}, {"lp_holders": ["z"]},
                    {"holders": {"a": 1}}):
        assert _level(changes) in {"LOW", "MEDIUM", "UNVERIFIED", "HIGH"}
    sol = {"mintable": {"status": "0"}, "freezable": {"status": "0"}, "holders": ["x"], "dex": "y",
           "holder_count": "900"}
    assert rugcheck.assess_risk(_token(chain="solana"), sol, "solana").level != "LOW"


def test_check_tokens_fails_closed_when_one_record_is_unreadable():
    def getter(url, params=None, **kw):
        return {"result": {"0xabc": {"is_honeypot": "0", "sell_tax": "0", "buy_tax": "0",
                                     "is_open_source": "1", "holder_count": "9000"}}}

    class Boom(dict):
        def get(self, *a, **k):
            raise AttributeError("unexpected shape")

    original = rugcheck.assess_risk
    try:
        def exploding(token, security, chain):
            raise AttributeError("unexpected shape")
        rugcheck.assess_risk = exploding
        reports, warnings = rugcheck.check_tokens([_token(address="0xabc")], getter)
    finally:
        rugcheck.assess_risk = original
    report = reports[("base", "0xabc")]
    assert report.level == "UNVERIFIED" and report.verified is False
    assert warnings


def test_fetch_security_tolerates_non_dict_responses():
    assert rugcheck.fetch_security("base", ["0xabc"], lambda *a, **k: ["not", "a", "dict"]) == {}
    assert rugcheck.fetch_security("base", ["0xabc"], lambda *a, **k: None) == {}


# ---------------------------------------------------------------- review regressions: net

def test_no_redirect_opener_turns_a_redirect_into_an_error():
    import http.server
    import threading

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/never")
            self.end_headers()

        def log_message(self, *a):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/x"
        try:
            net.get_text(url, headers={"X-MBX-APIKEY": "k"}, follow_redirects=False, retries=0)
            raise AssertionError("expected HttpError")
        except net.HttpError as exc:
            assert exc.status == 302
    finally:
        server.shutdown()
        server.server_close()
