"""Fake GeckoTerminal / GoPlus / DexScreener responses for offline tests (shapes written from the
public API docs; not verified against the live service)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

TOKEN = "T0kenAddress1111111111111111111111111111111"
POOL = "P00lAddress1111111111111111111111111111111"
WALLET = "W4lletAddress11111111111111111111111111111"


def pool_item(pool=POOL, token=TOKEN, symbol="TST", name="Test Coin", liq="60000", age_min=120, buyers=60):
    created = (datetime.now(UTC) - timedelta(minutes=age_min)).isoformat().replace("+00:00", "Z")
    return {
        "id": f"solana_{pool}",
        "attributes": {
            "address": pool, "name": f"{symbol} / SOL", "base_token_price_usd": "0.002",
            "fdv_usd": "900000", "market_cap_usd": None, "reserve_in_usd": liq, "pool_created_at": created,
            "volume_usd": {"m5": "9000", "m15": "20000", "h1": "90000", "h6": "150000", "h24": "200000"},
            "price_change_percentage": {"m5": "2", "h1": "12", "h6": "30", "h24": "55"},
            "transactions": {"m5": {"buys": 40, "sells": 20, "buyers": 25, "sellers": 15},
                             "h1": {"buys": 400, "sells": 220, "buyers": buyers, "sellers": 40},
                             "h24": {"buys": 900, "sells": 700, "buyers": 300, "sellers": 200}},
        },
        "relationships": {"base_token": {"data": {"id": f"solana_{token}"}},
                          "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}},
                          "dex": {"data": {"id": "raydium"}}},
    }


def pool_payload(*items):
    toks = {}
    for it in items:
        tid = it["relationships"]["base_token"]["data"]["id"]
        toks[tid] = {"id": tid, "type": "token", "attributes": {
            "address": tid.split("_", 1)[1], "name": "Test Coin", "symbol": it["attributes"]["name"].split(" ")[0]}}
    toks["solana_So11111111111111111111111111111111111111112"] = {
        "id": "solana_So11111111111111111111111111111111111111112",
        "attributes": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"}}
    return {"data": list(items), "included": list(toks.values())}


def trade(kind, usd, wallet, minute):
    ts = (datetime.now(UTC) - timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
    return {"attributes": {"kind": kind, "volume_in_usd": str(usd), "block_timestamp": ts,
                           "tx_from_address": wallet, "tx_hash": f"tx{minute}",
                           "price_to_in_usd": "0.002", "price_from_in_usd": "0.002"}}


def make_getter(trades=None, goplus=None, dex_pairs=None, fail=()):
    calls: list[str] = []

    def getter(url, params=None, headers=None, **kw):
        calls.append(url)
        for needle in fail:
            if needle in url:
                from marketbot.net import HttpError
                raise HttpError("boom", 500, url)
        if "geckoterminal" in url and url.endswith("/trades"):
            return {"data": trades if trades is not None else []}
        if "geckoterminal" in url and "/ohlcv/" in url:
            base = 1_700_000_000
            return {"data": {"attributes": {"ohlcv_list": [[base + i * 300, 1, 1.1, 0.9, 1.05, 100] for i in range(30)]}}}
        if "geckoterminal" in url and "/tokens/" in url:
            return pool_payload(pool_item())
        if "geckoterminal" in url and "/pools/" in url:
            payload = pool_payload(pool_item())
            return {"data": payload["data"][0], "included": payload["included"]}
        if "geckoterminal" in url:
            return pool_payload(pool_item())
        if "gopluslabs" in url:
            return goplus if goplus is not None else {"code": 1, "result": {}}
        if "dexscreener" in url:
            return {"pairs": dex_pairs or []}
        return {}
    getter.calls = calls
    return getter
