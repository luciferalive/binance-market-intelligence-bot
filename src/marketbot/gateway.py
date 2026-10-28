"""Single door for every outbound data request.

Why it exists:
* **Rate limits.** The free APIs (GeckoTerminal allows roughly 30 calls a minute) ban noisy
  clients. Calls to each host are spaced out, across threads.
* **Freshness and health.** Every call records success, failure and latency per source, which is
  what the Data Sources and System Health pages display. A feed that stops answering becomes
  visible instead of silently serving old numbers.
* **One place to fake in tests.** Pass ``getter`` and ``limits={}`` to get a no-wait stub.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .net import HttpError, get_json, get_text

# Minimum seconds between calls to the same host, with a friendly name for the UI.
SOURCES: dict[str, tuple[str, float]] = {
    "api.geckoterminal.com": ("GeckoTerminal", 2.2),
    "api.dexscreener.com": ("DexScreener", 0.25),
    "api.gopluslabs.io": ("GoPlus Security", 0.6),
    "api.coingecko.com": ("CoinGecko", 2.5),
    "news.google.com": ("Google News", 1.0),
    "api.binance.com": ("Binance", 0.06),
    "api.telegram.org": ("Telegram", 0.5),
}


@dataclass
class SourceStats:
    name: str
    host: str
    ok: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    last_ok: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    last_latency_ms: float | None = None
    latencies: list[float] = field(default_factory=list)

    def to_dict(self, now: float) -> dict:
        avg = sum(self.latencies) / len(self.latencies) if self.latencies else None
        total = self.ok + self.errors
        return {
            "name": self.name,
            "host": self.host,
            "ok": self.ok,
            "errors": self.errors,
            "error_rate": (self.errors / total) if total else None,
            "consecutive_errors": self.consecutive_errors,
            "last_ok_age_s": (now - self.last_ok) if self.last_ok else None,
            "last_error": self.last_error,
            "last_error_age_s": (now - self.last_error_at) if self.last_error_at else None,
            "last_latency_ms": self.last_latency_ms,
            "avg_latency_ms": avg,
        }


class SourceGateway:
    """Callable like ``get_json``; also exposes ``text`` for RSS and ``status()`` for the UI."""

    def __init__(
        self,
        getter: Callable[..., Any] | None = None,
        text_getter: Callable[..., str] | None = None,
        limits: dict[str, tuple[str, float]] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._get = getter or get_json
        self._get_text = text_getter or get_text
        self._limits = SOURCES if limits is None else limits
        self._clock = clock
        self._sleep = sleep
        self._stats: dict[str, SourceStats] = {}
        self._next_ok: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- internals ---------------------------------------------------------------
    @staticmethod
    def _host(url: str) -> str:
        return urllib.parse.urlparse(url).netloc.lower()

    def _stats_for(self, host: str) -> SourceStats:
        with self._guard:
            stats = self._stats.get(host)
            if stats is None:
                name = self._limits.get(host, SOURCES.get(host, (host, 0.0)))[0]
                stats = self._stats[host] = SourceStats(name=name, host=host)
            return stats

    def _host_lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _throttle(self, host: str) -> None:
        spacing = self._limits.get(host, ("", 0.0))[1]
        if spacing <= 0:
            return
        with self._host_lock(host):
            wait = self._next_ok.get(host, 0.0) - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._next_ok[host] = self._clock() + spacing

    def _call(self, fn: Callable[..., Any], url: str, *args: Any, **kwargs: Any) -> Any:
        host = self._host(url)
        stats = self._stats_for(host)
        self._throttle(host)
        started = self._clock()
        try:
            result = fn(url, *args, **kwargs)
        except HttpError as exc:
            with self._guard:
                stats.errors += 1
                stats.consecutive_errors += 1
                stats.last_error = str(exc)[:200]
                stats.last_error_at = self._clock()
            raise
        elapsed = (self._clock() - started) * 1000
        with self._guard:
            stats.ok += 1
            stats.consecutive_errors = 0
            stats.last_ok = self._clock()
            stats.last_latency_ms = elapsed
            stats.latencies = (stats.latencies + [elapsed])[-30:]
        return result

    # -- public ------------------------------------------------------------------
    def __call__(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return self._call(self._get, url, params=params, headers=headers, **kwargs)

    def text(self, url: str, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None, **kwargs: Any) -> str:
        return self._call(self._get_text, url, params=params, headers=headers, **kwargs)

    def status(self) -> list[dict]:
        now = self._clock()
        with self._guard:
            seen = {s.host: s for s in self._stats.values()}
        rows = []
        for host, (name, _) in self._limits.items():
            stats = seen.pop(host, None) or SourceStats(name=name, host=host)
            rows.append(stats.to_dict(now))
        rows.extend(s.to_dict(now) for s in seen.values())
        return rows

    def freshness(self, host: str) -> float | None:
        """Seconds since the last successful call to ``host`` (None if never)."""
        stats = self._stats.get(host)
        return (self._clock() - stats.last_ok) if stats and stats.last_ok else None
