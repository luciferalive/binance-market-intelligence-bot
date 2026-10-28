"""Alerts: three priority levels, de-duplication, and optional Telegram or webhook delivery.

Levels: ``critical`` (risk changed on something you hold or watch; cannot be muted or
suppressed by cooldown settings), ``high`` and ``info``. Alerts describe what changed and
never tell anyone to buy or sell.

Delivery targets come from environment variables only (``TELEGRAM_BOT_TOKEN`` plus
``TELEGRAM_CHAT_ID``, and/or ``ALERT_WEBHOOK_URL``). They are never stored in the database,
returned by the API or written to logs, and error text never includes them.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .store import Store

LEVELS = ("info", "high", "critical")
RANK = {name: i for i, name in enumerate(LEVELS)}
COOLDOWN_S = {"info": 3600.0, "high": 1800.0, "critical": 600.0}
MAX_TEXT = 3500

Poster = Callable[[str, dict, float], int]


def _post_json(url: str, payload: dict, timeout: float) -> int:
    """POST JSON, no redirects, returns the HTTP status. Raises OSError-like errors without the URL."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: Any, **k: Any) -> None:
            return None
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "marketbot"})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001  network error: never include the URL (it may hold a secret)
        raise OSError(type(exc).__name__) from None


class Notifier:
    def __init__(self, env: dict[str, str] | None = None, poster: Poster = _post_json):
        env = os.environ if env is None else env
        self._token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self._chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self._hook = (env.get("ALERT_WEBHOOK_URL") or "").strip()
        if self._hook and not self._hook.startswith("https://"):
            self._hook = ""  # plain http would send alerts in the clear
        self._post = poster

    def channels(self) -> dict[str, bool]:
        return {"telegram": bool(self._token and self._chat), "webhook": bool(self._hook)}

    def configured(self) -> bool:
        return any(self.channels().values())

    def send(self, text: str) -> dict[str, str]:
        """Send to every configured channel. Returns {channel: 'ok' | error summary}."""
        text = text[:MAX_TEXT]
        out: dict[str, str] = {}
        if self._token and self._chat:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            out["telegram"] = self._deliver(url, {"chat_id": self._chat, "text": text,
                                                  "disable_web_page_preview": True})
        if self._hook:
            out["webhook"] = self._deliver(self._hook, {"text": text, "content": text})
        return out

    def _deliver(self, url: str, payload: dict) -> str:
        try:
            status = self._post(url, payload, 10.0)
        except OSError as exc:
            return f"failed ({exc})"
        return "ok" if 200 <= status < 300 else f"rejected (HTTP {status})"


class AlertEngine:
    def __init__(self, store: Store, notifier: Notifier | None = None, clock: Callable[[], float] = time.time):
        self.store = store
        self.notifier = notifier or Notifier()
        self.clock = clock

    def emit(self, level: str, kind: str, title: str, body: str = "", key: str = "",
             dedupe: str | None = None) -> int | None:
        """Record an alert. Returns its id, or None if it was a duplicate inside the cooldown
        or muted. Critical alerts ignore mute settings."""
        if level not in RANK:
            raise ValueError(f"unknown level {level!r}")
        if level != "critical" and kind in (self.store.get_setting("alert_muted_kinds") or []):
            return None
        dedupe = dedupe or f"{kind}:{key}:{level}"
        now = self.clock()
        recent = self.store.one("SELECT id FROM alerts WHERE dedupe=? AND ts>?", (dedupe, now - COOLDOWN_S[level]))
        if recent:
            return None
        cur = self.store.execute(
            "INSERT INTO alerts(ts,level,kind,key,title,body,dedupe) VALUES(?,?,?,?,?,?,?)",
            (now, level, kind, key, title[:140], body[:1000], dedupe))
        alert_id = int(cur.lastrowid)
        self._maybe_send(alert_id, level, title, body)
        return alert_id

    def _maybe_send(self, alert_id: int, level: str, title: str, body: str) -> None:
        if not self.notifier.configured():
            return
        floor = self.store.get_setting("alert_telegram_min_level") or "high"
        if level != "critical" and RANK[level] < RANK.get(floor, 1):
            return
        results = self.notifier.send(f"[{level.upper()}] {title}\n{body}".strip())
        if any(v == "ok" for v in results.values()):
            self.store.execute("UPDATE alerts SET sent_external=1 WHERE id=?", (alert_id,))

    def test(self) -> dict[str, Any]:
        """Send a test message so the person can confirm their setup works."""
        if not self.notifier.configured():
            return {"ok": False, "channels": self.notifier.channels(),
                    "message": "No delivery channel configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or ALERT_WEBHOOK_URL."}
        results = self.notifier.send("[TEST] Market Desk alert test. Nothing to do.")
        return {"ok": any(v == "ok" for v in results.values()), "channels": self.notifier.channels(), "results": results}

    def recent(self, limit: int = 100, level: str | None = None) -> list[dict]:
        if level in RANK:
            return self.store.query("SELECT * FROM alerts WHERE level=? ORDER BY id DESC LIMIT ?", (level, limit))
        return self.store.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    def mark_all_read(self) -> None:
        self.store.execute("UPDATE alerts SET read=1")

    def unread_counts(self) -> dict[str, int]:
        rows = self.store.query("SELECT level, COUNT(*) AS n FROM alerts WHERE read=0 GROUP BY level")
        counts = {name: 0 for name in LEVELS}
        counts.update({r["level"]: r["n"] for r in rows})
        return counts

    def channels(self) -> dict[str, bool]:
        return self.notifier.channels()


def evaluate(engine: AlertEngine, *, scored: list[dict], watch_keys: set[str], held: dict[str, dict]) -> int:
    """Turn scan results into alerts. ``scored`` are ScoredPool dicts; ``held`` maps key -> paper position.

    Rules (all factual, none advisory):
      * critical: a watched or held token now has CRITICAL risk, or a held token lost over 30% of liquidity
      * high: a new CANDIDATE appears with HIGH data confidence, or a watched token turns HIGH risk
    """
    n = 0
    for item in scored:
        key = item.get("key") or ""
        pool = item.get("pool") or {}
        risk_info = item.get("risk") or {}
        symbol = pool.get("symbol") or "?"
        risk = (risk_info.get("level") or "").upper()
        reasons = "; ".join(f.get("message", "") for f in (risk_info.get("flags") or []))[:400]
        watched = key in watch_keys or key in held
        if watched and risk == "CRITICAL":
            n += engine.emit("critical", "risk_critical", f"{symbol}: critical risk flag", reasons, key) is not None
        elif watched and risk == "HIGH":
            n += engine.emit("high", "risk_high", f"{symbol}: high risk flag", reasons, key) is not None
        pos = held.get(key)
        liq = pool.get("liquidity_usd")
        if pos and liq and pos.get("last_liquidity") and liq < 0.7 * pos["last_liquidity"]:
            drop = (1 - liq / pos["last_liquidity"]) * 100
            n += engine.emit("critical", "liquidity_drop", f"{symbol}: liquidity down {drop:.0f}%",
                             "Liquidity fell since the last reading. The exit cost of a held position rose.", key) is not None
        if item.get("label") == "CANDIDATE" and (item.get("confidence") or {}).get("level") == "HIGH" and not watched:
            n += engine.emit("high", "candidate", f"{symbol}: new candidate (score {item.get('opportunity')})",
                             "Passed the risk gates with high data confidence. Research it; this is not advice.",
                             key) is not None
    return n
