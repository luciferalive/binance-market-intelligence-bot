"""API routes for the Market Desk. Pure functions of (services, request) so they are easy to test.

GET routes only read. POST routes change local state (watchlist, tracked wallets, paper trades,
settings, emergency stop) and are protected by the HTTP layer with a per-run token. No route can
send a real order.
"""
from __future__ import annotations

from typing import Any

from . import scoring
from .chains import CHAINS
from .geckoterminal import valid_address
from .investigate import SECTIONS

# name -> (kind, low, high); bounds stop a typo from disabling a safety limit.
SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "paper_capital_usd": ("num", 100, 10_000_000),
    "max_position_usd": ("num", 1, 1_000_000),
    "max_open_positions": ("int", 1, 50),
    "max_slippage_pct": ("num", 0.1, 50),
    "min_liquidity_multiple": ("num", 2, 1000),
    "max_daily_loss_pct": ("num", 0.5, 100),
    "fee_pct": ("num", 0, 5),
    "network_fee_usd": ("num", 0, 100),
    "max_price_age_s": ("int", 10, 3600),
    "watcher_interval_s": ("int", 60, 3600),
}
LEVELS = {"info", "high", "critical"}


class BadRequest(ValueError):
    pass


def _one(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return (query.get(key) or [default])[0].strip()


def chain_of(query_or_body: dict, services: Any, from_query: bool = False) -> str:
    raw = query_or_body.get("chain")
    if from_query:  # parse_qs gives lists of strings
        raw = raw[0] if raw else None
    if raw is None or raw == "":
        raw = services.desk.store.get_setting("chain")
    if not isinstance(raw, str) or raw not in CHAINS:
        raise BadRequest("Unknown chain.")
    return raw


def route_get(services: Any, path: str, query: dict[str, list[str]]) -> Any:
    desk = services.desk
    if path == "/api/session":
        return {"token": services.session_token}
    if path == "/api/meta":
        return desk.meta()
    chain = chain_of(query, services, True) if path not in {"/api/health", "/api/alerts", "/api/orders", "/api/journal",
                                                     "/api/performance", "/api/settings", "/api/capabilities"} else ""
    if path == "/api/command":
        return desk.command_center(chain)
    if path == "/api/scan":
        mode = _one(query, "mode", "fresh")
        if mode not in scoring.MODES:
            raise BadRequest("Unknown mode.")
        try:
            limit = max(1, min(int(_one(query, "limit", "40")), 100))
        except ValueError as exc:
            raise BadRequest("limit must be a number.") from exc
        return desk.scan(chain, mode, query, limit)
    if path == "/api/rugs":
        return desk.rug_view(chain)
    if path == "/api/narratives":
        return desk.narratives(chain)
    if path == "/api/investigate":
        ident = _one(query, "id")
        if not valid_address(ident):
            raise BadRequest("Enter a token or pool address (letters and numbers, 20-64 characters).")
        sections = {x for x in _one(query, "sections").split(",") if x in SECTIONS} or None
        mode = _one(query, "mode", "fresh")
        if mode not in scoring.MODES:
            raise BadRequest("Unknown mode.")
        tf = _one(query, "tf", "5m")
        return desk.investigator.investigate(chain, ident, sections, mode, tf if tf in {"1m", "5m", "15m", "1h", "4h", "1d"} else "5m")
    if path == "/api/watchlist":
        return {"items": desk.store.watchlist()}
    if path == "/api/wallets":
        return {"items": desk.store.wallets(chain),
                "limits": ["Only wallets you add are tracked. Profit history and clusters are not connected."]}
    if path == "/api/alerts":
        return {"items": desk.alerts.recent(100, _one(query, "level") or None), "counts": desk.alerts.unread_counts(),
                "channels": desk.alerts.channels()}
    if path == "/api/paper":
        return {"summary": desk.paper.summary(), "positions": desk.paper.positions(), "settings": desk.store.all_settings()}
    if path == "/api/orders":
        return {"items": desk.paper.orders(200)}
    if path == "/api/journal":
        return {"items": desk.store.journal(200)}
    if path == "/api/performance":
        return desk.performance()
    if path == "/api/health":
        return desk.health()
    if path == "/api/capabilities":
        return desk.capability_matrix()
    if path == "/api/settings":
        return {"settings": desk.store.all_settings(), "rules": {k: list(v) for k, v in SETTING_RULES.items()}}
    return None


def _coerce(name: str, value: Any) -> Any:
    kind, low, high = SETTING_RULES[name]
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{name} must be a number.") from exc
    if not low <= num <= high:
        raise BadRequest(f"{name} must be between {low:g} and {high:g}.")
    return int(num) if kind == "int" else num


def route_post(services: Any, path: str, body: dict) -> Any:
    desk = services.desk
    store = desk.store
    if path in {"/api/watchlist/add", "/api/watchlist/remove"}:
        chain = chain_of(body, services)
        ident = str(body.get("id") or "")
        if not valid_address(ident):
            raise BadRequest("Invalid address.")
        if path.endswith("remove"):
            store.watch_remove(f"{chain}:{ident}")
            return {"ok": True}
        row = desk.investigator.resolve(chain, ident)
        store.watch_add(row.key, chain, row.pool_address, row.token_address, row.symbol, row.name, str(body.get("note") or ""))
        return {"ok": True, "key": row.key}
    if path in {"/api/wallets/add", "/api/wallets/remove"}:
        chain = chain_of(body, services)
        address = str(body.get("address") or "")
        if not valid_address(address):
            raise BadRequest("Invalid wallet address.")
        if path.endswith("remove"):
            store.wallet_remove(address, chain)
        else:
            store.wallet_add(address, chain, str(body.get("label") or ""))
        return {"ok": True}
    if path == "/api/paper/buy":
        if not isinstance(body.get("pool"), str) or not valid_address(body["pool"]):
            raise BadRequest("Invalid pool address.")
        return desk.paper_buy(body)
    if path == "/api/paper/sell":
        return desk.paper_sell(body)
    if path == "/api/halt":
        desk.paper.halt(str(body.get("reason") or "manual emergency stop")[:120])
        return {"ok": True, "halted": True}
    if path == "/api/resume":
        desk.paper.resume()
        return {"ok": True, "halted": False}
    if path == "/api/alerts/test":
        return desk.alerts.test()
    if path == "/api/alerts/read":
        desk.alerts.mark_all_read()
        return {"ok": True}
    if path == "/api/settings":
        changed = {}
        for name, value in body.items():
            if name in SETTING_RULES:
                if name == "paper_capital_usd" and store.one("SELECT id FROM paper_orders LIMIT 1"):
                    raise BadRequest("Paper capital cannot change after trades exist: it would distort cash and the loss limit.")
                changed[name] = _coerce(name, value)
            elif name == "chain":
                if not isinstance(value, str) or value not in CHAINS:
                    raise BadRequest("Unknown chain.")
                changed[name] = value
            elif name == "watcher_enabled":
                if not isinstance(value, bool):
                    raise BadRequest("watcher_enabled must be true or false.")
                changed[name] = value
            elif name == "alert_telegram_min_level":
                if not isinstance(value, str) or value not in LEVELS:
                    raise BadRequest("Unknown level.")
                changed[name] = value
            elif name == "alert_muted_kinds":
                if not isinstance(value, list) or not all(isinstance(x, str) and len(x) < 40 for x in value):
                    raise BadRequest("alert_muted_kinds must be a list of names.")
                changed[name] = value[:30]
            else:
                raise BadRequest(f"Setting {name!r} cannot be changed here.")
        for name, value in changed.items():
            store.set_setting(name, value)
        if changed.get("watcher_enabled"):
            desk.start_watcher()
        return {"ok": True, "settings": store.all_settings()}
    return None
