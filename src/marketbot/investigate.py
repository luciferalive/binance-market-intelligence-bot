"""Token investigation: everything the detail page shows, built from the same scored data.

The "AI investigation" is a deterministic, rule-based analyst: it restates the itemised score,
the risk flags and the data gaps in plain language, and lists the observable conditions that
would invalidate the thesis. It uses no language model, so every sentence can be traced to a
number on the page. It never recommends buying or selling.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import geckoterminal as gt
from . import memecoins, news, scoring
from . import security as sec
from .chains import get_chain
from .memepaper import exit_table, price_impact_pct
from .net import HttpError
from .pools import PoolRow
from .store import Store

LARGE_TRADE_USD = 2_000.0
SECTIONS = ("overview", "security", "holders", "smart", "liquidity", "news", "ai", "chart")


class NotFound(LookupError):
    pass


class Investigator:
    def __init__(self, getter: Callable[..., Any], text_getter: Callable[..., Any], store: Store,
                 clock: Callable[[], float] = time.time):
        self.getter = getter
        self.text_getter = text_getter
        self.store = store
        self.clock = clock
        self._views: dict = {}

    # -- resolving ---------------------------------------------------------------
    def resolve(self, chain: str, ident: str) -> PoolRow:
        """``ident`` is a pool address or a token contract address."""
        if not gt.valid_address(ident):
            raise ValueError("Not a valid address.")
        row = None
        try:
            row = gt.pool(chain, ident, self.getter)
        except HttpError:
            row = None
        if row is None:
            pools = gt.token_pools(chain, ident, self.getter)
            row = pools[0] if pools else None
        if row is None:
            raise NotFound("No pool found for that address on this chain.")
        return row

    # -- smart money -------------------------------------------------------------
    def wallet_flow(self, chain: str, pool_address: str) -> dict:
        tracked = {w["address"].lower(): w for w in self.store.wallets(chain)}
        try:
            trades = gt.trades(chain, pool_address, self.getter)
        except (HttpError, ValueError) as exc:
            return {"available": False, "reason": f"Trades unavailable: {exc}", "tracked_count": len(tracked)}
        buys = [t for t in trades if t["kind"] == "buy"]
        sells = [t for t in trades if t["kind"] == "sell"]
        mine = [t for t in trades if (t.get("wallet") or "").lower() in tracked]
        t_buy = sum(t["usd"] for t in mine if t["kind"] == "buy")
        t_sell = sum(t["usd"] for t in mine if t["kind"] == "sell")
        buyers = {t["wallet"].lower() for t in mine if t["kind"] == "buy"}
        large = [t for t in trades if t["usd"] >= LARGE_TRADE_USD]
        large_buy = sum(t["usd"] for t in large if t["kind"] == "buy")
        large_sell = sum(t["usd"] for t in large if t["kind"] == "sell")
        if not tracked:
            state = "No wallets tracked"
        elif not mine:
            state = "No tracked wallet in the recent trades"
        elif t_buy > t_sell * 1.5:
            state = "Tracked wallets accumulating"
        elif t_sell > t_buy * 1.5:
            state = "Tracked wallets distributing"
        else:
            state = "Mixed"
        return {
            "available": True, "state": state, "window": f"last {len(trades)} swaps",
            "tracked_count": len(tracked), "tracked_buyers": len(buyers),
            "buy_usd": t_buy, "sell_usd": t_sell, "net_usd": t_buy - t_sell,
            "large_buy_usd": large_buy, "large_sell_usd": large_sell, "large_count": len(large),
            "buys": len(buys), "sells": len(sells),
            "tracked_trades": [{**t, "label": tracked[t["wallet"].lower()].get("label", "")} for t in mine[:20]],
            "large_trades": large[:20],
            "limits": ["Only the most recent swaps are visible, not full wallet history.",
                       "Wallet profit history and wallet clusters are not connected."],
        }

    def smart_flow_for_scan(self, chain: str, rows: list[PoolRow]) -> dict[str, dict]:
        if not self.store.wallets(chain):
            return {}
        out = {}
        for row in rows:
            flow = self.wallet_flow(chain, row.pool_address)
            if flow.get("available") and flow["tracked_buyers"] + (1 if flow["sell_usd"] else 0):
                out[row.key] = flow
        return out

    # -- liquidity ---------------------------------------------------------------
    def liquidity_tab(self, row: PoolRow, view: sec.SecurityView) -> dict:
        history = self.store.snapshots(row.key)
        series = [{"t": h["ts"], "liquidity": h["liquidity"]} for h in history if h["liquidity"]]
        change = None
        if len(series) >= 2 and series[0]["liquidity"]:
            change = (series[-1]["liquidity"] - series[0]["liquidity"]) / series[0]["liquidity"] * 100
        turnover = (row.vol_h24 / row.liquidity_usd) if row.vol_h24 is not None and row.liquidity_usd else None
        return {
            "liquidity_usd": row.liquidity_usd, "fdv_usd": row.fdv_usd,
            "fdv_to_liquidity": (row.fdv_usd / row.liquidity_usd) if row.fdv_usd and row.liquidity_usd else None,
            "turnover_24h": turnover,
            "lp_locked_pct": view.fields.get("lp_locked_pct"),
            "exit_table": exit_table(row.liquidity_usd),
            "exit_note": "Estimates from a constant-product pool model. Real routes, concentrated liquidity and "
                         "fast price moves can make the true cost higher.",
            "history": series, "history_change_pct": change,
            "history_note": "Liquidity history is recorded by this app while it runs, so a new install starts empty.",
        }

    # -- news --------------------------------------------------------------------
    def news_tab(self, row: PoolRow) -> dict:
        try:
            items = news.fetch_headlines(f"{row.name} {row.symbol}", self.text_getter, max_items=8)
        except HttpError as exc:
            return {"available": False, "reason": str(exc), "headlines": [],
                    "social": "Not connected: mentions, accounts and sentiment need a social data provider."}
        return {"available": True, "headlines": [h.to_dict() for h in items], "summary": news.summarize(items),
                "social": "Not connected: mentions, accounts and sentiment need a social data provider.",
                "note": "Headlines are matched by name, so unrelated stories can appear. Many new tokens have none."}

    # -- AI investigation --------------------------------------------------------
    @staticmethod
    def analyst(item: dict, flow: dict | None, now: float) -> dict:
        pool = item["pool"]
        label = item["label"]
        contribs = sorted(item["contributions"], key=lambda c: c["points"], reverse=True)
        supports = [f"{c['name']}: {c['detail']}" for c in contribs if c["points"] > 0 and c["points"] >= 0.5 * c["max"]][:4]
        risks = [f["message"] for f in item["risk"]["flags"] if f["severity"] in ("critical", "high")][:5]
        risks += [f"Weak {c['name'].lower()}: {c['detail']}" for c in contribs if c["max"] and c["points"] < 0.25 * c["max"]][:3]
        risks += [f"Score capped at {c['cap']}: {c['reason']}" for c in item["caps"]][:3]
        unknown = []
        if not item["security"]["available"]:
            unknown.append("Contract security data was not available.")
        sim = (item["security"]["fields"] or {}).get("sell_simulation") or {}
        if not sim.get("available"):
            unknown.append("No sell simulation for this chain: honeypot behaviour cannot be ruled out by a test.")
        if pool.get("buyers_h1") is None:
            unknown.append("Unique buyer counts are not available from the data source.")
        unknown.append("Social activity is not connected.")
        liq = pool.get("liquidity_usd")
        invalidation = []
        if liq:
            invalidation.append(f"Liquidity falls below ${liq * 0.7:,.0f} (30% below now).")
        invalidation.append("Buy share of trades drops under 40% while volume is still high.")
        invalidation.append("A new critical or high risk flag appears on the contract.")
        if flow and flow.get("tracked_count"):
            invalidation.append("Tracked wallets start selling more than they buy.")
        verdict = {"CANDIDATE": "Worth researching further. Not a buy signal.",
                   "WATCH": "Keep on a watchlist. Evidence is incomplete or mixed.",
                   "SKIP": "Not interesting on current data.",
                   "HIGH RISK": "Risk flags outweigh the opportunity.",
                   "AVOID": "Serious risk flags. Avoid."}.get(label, "No view.")
        return {
            "verdict": verdict, "label": label, "score": item["opportunity"],
            "confidence": item["confidence"]["level"],
            "why_selected": supports or ["No component scored strongly."],
            "risks": risks or ["No major flag found in the data available."],
            "unknown": unknown, "invalidation": invalidation,
            "indicators": [{"name": c["name"], "points": c["points"], "max": c["max"], "detail": c["detail"]}
                           for c in contribs],
            "sources": ["GeckoTerminal (market data)", "GoPlus Security (contract data)", "Google News (headlines)"],
            "updated_at": now,
            "disclaimer": "Rule-based summary of the data shown. It can be wrong. It is not financial advice.",
        }

    # -- main entry --------------------------------------------------------------
    def investigate(self, chain: str, ident: str, sections: set[str] | None = None, mode: str = "fresh",
                    timeframe: str = "5m") -> dict:
        get_chain(chain)
        want = set(sections) if sections else {"overview", "security", "holders", "liquidity", "ai"}
        now = self.clock()
        row = self.resolve(chain, ident)
        views, warnings = sec.fetch_views(chain, [row], self.getter, self._views, now)
        view = views[row.token_address.lower()]

        corroborated = False
        try:
            other = memecoins.search_tokens(row.token_address, self.getter, limit=3)
            corroborated = any(t.address.lower() == row.token_address.lower() and t.liquidity_usd
                               and row.liquidity_usd and abs(t.liquidity_usd - row.liquidity_usd) / row.liquidity_usd < 0.5
                               for t in other)
        except (HttpError, ValueError):
            warnings.append("DexScreener cross-check unavailable.")

        flow = self.wallet_flow(chain, row.pool_address) if want & {"smart", "ai"} else None
        ctx = {"smart": flow if flow and flow.get("available") and flow.get("tracked_count") else None,
               "holder_growth_pct": self.store.holder_growth_pct(row.key)}
        item = scoring.score_pool(mode, row, view, ctx, now, corroborated).to_dict()
        self.store.add_snapshot(row.key, row.liquidity_usd, row.price_usd, view.fields.get("holder_count"), row.vol_h1)

        out: dict[str, Any] = {"key": row.key, "chain": chain, "mode": mode, "warnings": warnings,
                               "fetched_at": now, "capabilities": get_chain(chain).capabilities}
        if "overview" in want:
            out["overview"] = {"pool": row.to_dict(), "label": item["label"], "opportunity": item["opportunity"],
                               "confidence": item["confidence"], "risk": item["risk"], "caps": item["caps"],
                               "notes": item["notes"], "explorer": get_chain(chain).explorer,
                               "dexscreener_corroborated": corroborated,
                               "impact_100": price_impact_pct(row.liquidity_usd, 100.0),
                               "impact_1000": price_impact_pct(row.liquidity_usd, 1000.0)}
        if "security" in want:
            out["security"] = {**item["security"], "risk": item["risk"], "chain_name": get_chain(chain).name}
        if "holders" in want:
            out["holders"] = {"available": bool(view.holders), "rows": view.holders,
                              "top10_pct": view.fields.get("top10_pct"), "holder_count": view.fields.get("holder_count"),
                              "growth_pct": ctx["holder_growth_pct"],
                              "note": "Holder growth is measured from this app's own snapshots, so it needs time to build."}
        if "smart" in want:
            out["smart"] = flow
        if "liquidity" in want:
            out["liquidity"] = self.liquidity_tab(row, view)
        if "news" in want:
            out["news"] = self.news_tab(row)
        if "chart" in want:
            try:
                out["chart"] = {"timeframe": timeframe, "candles": gt.ohlcv(chain, row.pool_address, timeframe, 120, self.getter)}
            except (HttpError, ValueError) as exc:
                out["chart"] = {"timeframe": timeframe, "candles": [], "error": str(exc)}
        if "ai" in want:
            out["ai"] = self.analyst(item, flow, now)
        out["score"] = item
        return out
