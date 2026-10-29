"""The desk: one object that wires scanning, investigation, paper trading, alerts and storage.

The HTTP layer only calls methods here. A background watcher can re-scan on an interval, record
snapshots, evaluate alerts and mark paper positions; it is off in tests and can be switched off in
Settings. Nothing here can place a real order.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import narratives, scoring
from . import security as sec
from .alerts import AlertEngine, Notifier, evaluate
from .chains import CHAINS, DEFAULT_CHAIN, describe, get_chain
from .discover import Filters, Scanner
from .gateway import SourceGateway
from .investigate import Investigator
from .memepaper import PaperEngine
from .net import HttpError
from .store import Store

DEFAULT_DB = Path("data/marketdesk.sqlite3")

NAV = [
    ("OVERVIEW", [("command", "Command Center", True)]),
    ("DISCOVERY", [("launches", "New Launches", True), ("scanner", "Token Scanner", True),
                   ("watchlist", "Watchlist", True), ("narratives", "Trending Narratives", True)]),
    ("INTELLIGENCE", [("investigate", "Token Investigation", True), ("rugs", "Rug Pull Detector", True),
                      ("smart", "Smart Money", True), ("clusters", "Wallet Clusters", False),
                      ("news", "News Radar", True)]),
    ("TRADING", [("terminal", "Trading Terminal", True), ("positions", "Open Positions", True),
                 ("orders", "Order History", True), ("risk", "Risk Management", True)]),
    ("RESEARCH", [("lab", "Strategy Lab", False), ("backtest", "Backtesting", False),
                  ("journal", "Trade Journal", True), ("performance", "Performance Analytics", True)]),
    ("SYSTEM", [("alerts", "Alerts", True), ("sources", "Data Sources", True),
                ("health", "System Health", True), ("settings", "Settings", True),
                ("portfolio", "Binance Portfolio", True)]),
]


class Desk:
    def __init__(self, getter: Callable[..., Any], text_getter: Callable[..., Any], store: Store | None = None,
                 notifier: Notifier | None = None, clock: Callable[[], float] = time.time,
                 use_gateway: bool = True):
        self.clock = clock
        self.store = store or Store(DEFAULT_DB, clock=clock)
        if use_gateway:
            self.gateway: SourceGateway | None = SourceGateway(getter, text_getter, clock=clock)
            net_get, net_text = self.gateway, self.gateway.text
        else:
            self.gateway, net_get, net_text = None, getter, text_getter
        self.alerts = AlertEngine(self.store, notifier, clock)
        self.paper = PaperEngine(self.store, clock)
        self.investigator = Investigator(net_get, net_text, self.store, clock)
        self._sec_cache = self.investigator._views
        self.scanner = Scanner(
            net_get, history=lambda k: {"holder_growth_pct": self.store.holder_growth_pct(k)},
            record=self._record, smart_flow=self.investigator.smart_flow_for_scan, clock=clock)
        self.scanner._security_cache = self._sec_cache
        self.started = clock()
        self._watch_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_watch: dict[str, Any] = {"at": None, "error": None, "alerts": 0}

    # -- recording ---------------------------------------------------------------
    def _record(self, item: scoring.ScoredPool) -> None:
        row = item.row
        self.store.add_snapshot(row.key, row.liquidity_usd, row.price_usd,
                                item.view.fields.get("holder_count"), row.vol_h1)

    # -- meta --------------------------------------------------------------------
    def meta(self) -> dict:
        chain = self.store.get_setting("chain")
        return {
            "chains": describe(),
            "chain": chain if chain in CHAINS else DEFAULT_CHAIN,
            "mode": "PAPER", "halted": self.paper.halted(), "halt_reason": self.store.get_setting("halt_reason"),
            "nav": [{"section": s, "items": [{"id": i, "label": label, "ready": ok} for i, label, ok in items]}
                    for s, items in NAV],
            "alert_counts": self.alerts.unread_counts(),
            "alert_channels": self.alerts.channels(),
            "sources": self.gateway.status() if self.gateway else [],
            "server_time": self.clock(),
            "live_trading": "Not implemented. This build only simulates trades.",
        }

    # -- scan --------------------------------------------------------------------
    def scan(self, chain: str, mode: str, query: dict[str, list[str]], limit: int = 40) -> dict:
        get_chain(chain)
        filters = Filters.from_query(query)
        out = self.scanner.scan(chain, mode, filters, limit)
        watch = {w["key"] for w in self.store.watchlist()}
        for row in out["rows"]:
            row["watched"] = row["key"] in watch
        return out

    def rug_view(self, chain: str, limit: int = 40) -> dict:
        """Newest pools sorted by risk, so the worst offenders are visible first."""
        out = self.scanner.scan(chain, "fresh", Filters(), limit=100)
        order = {"CRITICAL": 0, "UNVERIFIED": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
        rows = sorted(out["rows"], key=lambda r: (order.get(r["risk"]["level"], 5), -(r["pool"]["liquidity_usd"] or 0)))
        out["rows"] = rows[:limit]
        return out

    # -- narratives ---------------------------------------------------------------
    def narratives(self, chain: str) -> dict:
        rows: dict[str, Any] = {}
        warnings: list[str] = []
        for mode in ("fresh", "momentum"):
            got, warn, _ = self.scanner.candidates(chain, mode)
            warnings += warn
            for r in got:
                rows.setdefault(r.key, r)
        themes = narratives.aggregate(rows.values(), self.store.previous_themes())
        self.store.add_theme_snapshot(themes)
        return {"chain": chain, "themes": themes, "warnings": list(dict.fromkeys(warnings)),
                "note": "Themes come from token names. Social mentions need a provider that is not connected."}

    # -- command center ----------------------------------------------------------
    def command_center(self, chain: str) -> dict:
        fresh = self.scanner.scan(chain, "fresh", Filters(), limit=12)
        momentum = self.scanner.scan(chain, "momentum", Filters(), limit=12)
        themes = self.narratives(chain)["themes"][:6]
        cands = list({r["key"]: r for r in fresh["rows"] + momentum["rows"] if r["label"] == "CANDIDATE"}.values())
        risky = [r for r in fresh["rows"] if r["risk"]["level"] in ("CRITICAL", "HIGH", "UNVERIFIED")]
        return {
            "chain": chain, "paper": self.paper.summary(), "fresh": fresh["rows"][:8], "momentum": momentum["rows"][:8],
            "candidates": cands[:6], "risky_launches": risky[:6], "themes": themes,
            "alerts": self.alerts.recent(12), "alert_counts": self.alerts.unread_counts(),
            "warnings": list(dict.fromkeys(fresh["warnings"] + momentum["warnings"]))[:6],
            "freshness": fresh["freshness"],
        }

    # -- paper trading -----------------------------------------------------------
    def paper_buy(self, body: dict) -> dict:
        chain = str(body.get("chain") or "")
        ident = str(body.get("pool") or "")
        get_chain(chain)
        row = self.investigator.resolve(chain, ident)
        views, _ = sec.fetch_views(chain, [row], self.investigator.getter, self._sec_cache, self.clock())
        view = views[row.token_address.lower()]
        if not view.available or view.report.level in ("CRITICAL", "HIGH", "UNVERIFIED"):
            reason = ("Security data is missing, so the token is treated as risky."
                      if not view.available else f"Risk level is {view.report.level}.")
            self.store.journal_add("entry_refused", row.key, row.symbol, {"problems": [reason]})
            return {"ok": False, "problems": [reason + " Paper entries are blocked for risky or unverified tokens."],
                    "risk_level": view.report.level}
        res = self.paper.buy(
            chain=chain, pool=row.pool_address, token=row.token_address, symbol=row.symbol, name=row.name,
            usd=_num(body.get("usd")), price=row.price_usd, liquidity=row.liquidity_usd, price_ts=row.fetched_at,
            tp_pct=_opt(body.get("tp_pct")), sl_pct=_opt(body.get("sl_pct")), trail_pct=_opt(body.get("trail_pct")),
            max_hold_min=_opt(body.get("max_hold_min")), reason=str(body.get("reason") or "manual")[:120])
        res["risk_level"] = view.report.level
        return res

    def paper_sell(self, body: dict) -> dict:
        key = str(body.get("key") or "")
        pos = self.store.one("SELECT chain,pool FROM paper_positions WHERE key=?", (key,))
        if not pos:
            return {"ok": False, "problems": ["No such paper position."]}
        price = liq = None
        try:
            row = self.investigator.resolve(pos["chain"], pos["pool"])
            price, liq = row.price_usd, row.liquidity_usd
        except (HttpError, LookupError, ValueError):
            pass  # fall back to the last known price; the engine stays pessimistic about liquidity
        return self.paper.sell(key, price=price, liquidity=liq, reason="manual")

    # -- watcher -----------------------------------------------------------------
    def watch_once(self) -> dict:
        chain = self.store.get_setting("chain")
        held = {p["key"]: p for p in self.paper.positions()}
        watch = {w["key"] for w in self.store.watchlist()}
        alerts = 0
        errors: list[str] = []
        scored: list[dict] = []
        try:
            for mode in ("fresh", "momentum"):
                scored += self.scanner.scan(chain, mode, Filters(), limit=60)["rows"]
            alerts += evaluate(self.alerts, scored=scored, watch_keys=watch, held=held)
        except (HttpError, ValueError) as exc:
            errors.append(str(exc))
        # Held and watched tokens are checked directly, on their own chain, whether or not they
        # still appear in the scan lists. Switching the chain dropdown must not stop exits.
        seen = {r["key"] for r in scored}
        for key in set(held) | watch:
            pos = held.get(key)
            item = self._direct_check(key, pos)
            if item is None:
                continue
            row_dict, scored_item = item
            if key not in seen:
                alerts += evaluate(self.alerts, scored=[scored_item], watch_keys=watch, held=held)
            if pos:
                reason = self.paper.mark(key, row_dict["price_usd"], row_dict["liquidity_usd"])
                if reason:
                    alerts += self.alerts.emit("high", "paper_exit", f"{pos['symbol']}: paper exit ({reason})",
                                               "Simulated position closed by its rule.", key) is not None
        self.last_watch = {"at": self.clock(), "error": "; ".join(errors) or None, "alerts": alerts}
        self.store.prune_snapshots()
        return self.last_watch

    def _direct_check(self, key: str, pos: dict | None) -> tuple[dict, dict] | None:
        chain, _, pool = key.partition(":")
        if chain not in CHAINS or not pool:
            return None
        try:
            row = self.investigator.resolve(chain, pool)
            views, _ = sec.fetch_views(chain, [row], self.investigator.getter, self._sec_cache, self.clock())
        except (HttpError, LookupError, ValueError):
            return None
        item = scoring.score_pool("fresh", row, views[row.token_address.lower()], {}, self.clock()).to_dict()
        return row.to_dict(), item

    def start_watcher(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return

        def loop() -> None:
            while not self._stop.wait(max(60, int(self.store.get_setting("watcher_interval_s") or 180))):
                if self.store.get_setting("watcher_enabled"):
                    try:
                        self.watch_once()
                    except Exception as exc:  # noqa: BLE001  keep the thread alive
                        self.last_watch = {"at": self.clock(), "error": type(exc).__name__, "alerts": 0}

        self._watch_thread = threading.Thread(target=loop, name="desk-watcher", daemon=True)
        self._watch_thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- health ------------------------------------------------------------------
    def health(self) -> dict:
        sources = self.gateway.status() if self.gateway else []
        bad = [s for s in sources if s.get("errors") and s.get("ok", 0) == 0]
        return {
            "uptime_s": self.clock() - self.started, "sources": sources,
            "status": "DEGRADED" if bad else "OK",
            "watcher": {"enabled": bool(self.store.get_setting("watcher_enabled")),
                        "running": bool(self._watch_thread and self._watch_thread.is_alive()), **self.last_watch},
            "database": {"path": self.store.path, "snapshots": self.store.one("SELECT COUNT(*) AS n FROM snapshots")["n"]},
            "alert_channels": self.alerts.channels(),
            "notes": ["Rate limits are enforced per source. Source stats reset when the app restarts."],
        }

    def capability_matrix(self) -> dict:
        from .chains import CAPABILITY_LABELS
        return {"labels": CAPABILITY_LABELS, "chains": describe(),
                "notes": ["Anything marked unavailable is not connected. It is never estimated or guessed."]}

    def performance(self) -> dict:
        orders = [o for o in self.paper.orders(1000) if o["side"] == "sell"]
        pnls = [o["realized_pnl"] or 0.0 for o in orders]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        curve, total = [], 0.0
        for o in reversed(orders):
            total += o["realized_pnl"] or 0.0
            curve.append({"t": o["ts"], "pnl": total})
        peak, drawdown = 0.0, 0.0
        for point in curve:
            peak = max(peak, point["pnl"])
            drawdown = min(drawdown, point["pnl"] - peak)
        return {"summary": self.paper.summary(), "trades": len(pnls), "wins": len(wins), "losses": len(losses),
                "avg_win": (sum(wins) / len(wins)) if wins else None,
                "avg_loss": (sum(losses) / len(losses)) if losses else None,
                "max_drawdown": drawdown, "equity_curve": curve,
                "note": "Simulated results only. Fills model fees and price impact but not failed transactions or front-running."}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _opt(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None
