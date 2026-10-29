"""SQLite persistence for everything the dashboard remembers.

One file (default ``data/marketdesk.sqlite3``, git-ignored) holds scan snapshots, the watchlist,
tracked wallets, alerts, paper-trading state, the trade journal and settings. A single
connection is shared behind a lock, so the HTTP threads and the background watcher can use it.
No secrets are ever stored here: API keys and bot tokens live only in environment variables.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  key TEXT NOT NULL, ts REAL NOT NULL, liquidity REAL, price REAL, holders REAL, vol_h1 REAL);
CREATE INDEX IF NOT EXISTS snap_key_ts ON snapshots(key, ts);
CREATE TABLE IF NOT EXISTS watchlist(
  key TEXT PRIMARY KEY, chain TEXT NOT NULL, pool TEXT NOT NULL, token TEXT NOT NULL,
  symbol TEXT, name TEXT, note TEXT, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS wallets(
  address TEXT NOT NULL, chain TEXT NOT NULL, label TEXT, added_at REAL NOT NULL,
  PRIMARY KEY(address, chain));
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
  key TEXT, title TEXT NOT NULL, body TEXT, dedupe TEXT, sent_external INTEGER DEFAULT 0, read INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS alerts_dedupe ON alerts(dedupe, ts);
CREATE TABLE IF NOT EXISTS paper_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, key TEXT NOT NULL, symbol TEXT, side TEXT NOT NULL,
  usd REAL NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, fee_usd REAL NOT NULL, slippage_pct REAL NOT NULL,
  reason TEXT, realized_pnl REAL);
CREATE TABLE IF NOT EXISTS paper_positions(
  key TEXT PRIMARY KEY, chain TEXT NOT NULL, pool TEXT NOT NULL, token TEXT NOT NULL, symbol TEXT, name TEXT,
  qty REAL NOT NULL, cost_usd REAL NOT NULL, opened_ts REAL NOT NULL, tp_pct REAL, sl_pct REAL, trail_pct REAL,
  peak_price REAL, max_hold_min REAL, last_price REAL, last_liquidity REAL, last_ts REAL,
  entry_liquidity REAL);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, event TEXT NOT NULL, key TEXT, symbol TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS theme_snapshots(
  theme TEXT NOT NULL, ts REAL NOT NULL, tokens INTEGER, new_launches INTEGER, vol_h1 REAL);
CREATE INDEX IF NOT EXISTS theme_ts ON theme_snapshots(theme, ts);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "chain": "solana",
    "paper_capital_usd": 10_000.0,
    "max_position_usd": 500.0,
    "max_open_positions": 5,
    "max_slippage_pct": 5.0,
    "min_liquidity_multiple": 20.0,
    "max_daily_loss_pct": 5.0,
    "fee_pct": 0.3,
    "network_fee_usd": 0.5,
    "max_price_age_s": 300,
    "alert_telegram_min_level": "high",
    "alert_muted_kinds": [],
    "watcher_enabled": True,
    "watcher_interval_s": 180,
    "halted": False,
    "halt_reason": "",
    "paper_cash_usd": None,   # None until the first trade; then tracked
}


class Store:
    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.clock = clock
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    @property
    def lock(self) -> threading.RLock:
        """Hold this to make a read-check-write sequence atomic across threads."""
        return self._lock

    # -- plumbing ----------------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def transaction(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run ``fn`` atomically (used for paper fills so cash, position and order stay in step)."""
        with self._lock:
            try:
                result = fn(self._db)
                self._db.commit()
                return result
            except Exception:
                self._db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- settings ----------------------------------------------------------------
    def get_setting(self, name: str) -> Any:
        row = self.one("SELECT v FROM settings WHERE k=?", (name,))
        if row is None:
            return DEFAULT_SETTINGS.get(name)
        try:
            return json.loads(row["v"])
        except ValueError:
            return DEFAULT_SETTINGS.get(name)

    def set_setting(self, name: str, value: Any) -> None:
        if name not in DEFAULT_SETTINGS:
            raise KeyError(f"unknown setting {name!r}")
        self.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (name, json.dumps(value)))

    def all_settings(self) -> dict[str, Any]:
        return {k: self.get_setting(k) for k in DEFAULT_SETTINGS}

    # -- snapshots (liquidity, holders, price history) ---------------------------
    def add_snapshot(self, key: str, liquidity: float | None, price: float | None,
                     holders: float | None, vol_h1: float | None, min_gap_s: float = 240.0) -> bool:
        now = self.clock()
        last = self.one("SELECT ts FROM snapshots WHERE key=? ORDER BY ts DESC LIMIT 1", (key,))
        if last and now - last["ts"] < min_gap_s:
            return False
        self.execute("INSERT INTO snapshots(key,ts,liquidity,price,holders,vol_h1) VALUES(?,?,?,?,?,?)",
                     (key, now, liquidity, price, holders, vol_h1))
        return True

    def snapshots(self, key: str, limit: int = 500) -> list[dict]:
        rows = self.query("SELECT ts,liquidity,price,holders,vol_h1 FROM snapshots WHERE key=? "
                          "ORDER BY ts DESC LIMIT ?", (key, limit))
        return list(reversed(rows))

    def holder_growth_pct(self, key: str) -> float | None:
        rows = [r for r in self.snapshots(key) if r["holders"]]
        if len(rows) < 2 or rows[0]["holders"] <= 0:
            return None
        return (rows[-1]["holders"] - rows[0]["holders"]) / rows[0]["holders"] * 100

    def prune_snapshots(self, older_than_s: float = 7 * 86400) -> None:
        self.execute("DELETE FROM snapshots WHERE ts < ?", (self.clock() - older_than_s,))

    # -- watchlist ---------------------------------------------------------------
    def watch_add(self, key: str, chain: str, pool: str, token: str, symbol: str, name: str, note: str = "") -> None:
        self.execute(
            "INSERT INTO watchlist(key,chain,pool,token,symbol,name,note,added_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET note=excluded.note",
            (key, chain, pool, token, symbol[:24], name[:60], note[:200], self.clock()))

    def watch_remove(self, key: str) -> None:
        self.execute("DELETE FROM watchlist WHERE key=?", (key,))

    def watchlist(self) -> list[dict]:
        return self.query("SELECT * FROM watchlist ORDER BY added_at DESC")

    # -- tracked wallets ---------------------------------------------------------
    def wallet_add(self, address: str, chain: str, label: str = "") -> None:
        self.execute("INSERT OR REPLACE INTO wallets(address,chain,label,added_at) VALUES(?,?,?,?)",
                     (address, chain, label[:60], self.clock()))

    def wallet_remove(self, address: str, chain: str) -> None:
        self.execute("DELETE FROM wallets WHERE address=? AND chain=?", (address, chain))

    def wallets(self, chain: str | None = None) -> list[dict]:
        if chain:
            return self.query("SELECT * FROM wallets WHERE chain=? ORDER BY added_at DESC", (chain,))
        return self.query("SELECT * FROM wallets ORDER BY added_at DESC")

    # -- journal -----------------------------------------------------------------
    def journal_add(self, event: str, key: str = "", symbol: str = "", detail: dict | None = None) -> None:
        self.execute("INSERT INTO journal(ts,event,key,symbol,detail) VALUES(?,?,?,?,?)",
                     (self.clock(), event, key, symbol, json.dumps(detail or {}, default=str)))

    def journal(self, limit: int = 200) -> list[dict]:
        rows = self.query("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            try:
                row["detail"] = json.loads(row["detail"] or "{}")
            except ValueError:
                row["detail"] = {}
        return rows

    # -- narrative snapshots -----------------------------------------------------
    def add_theme_snapshot(self, themes: list[dict], min_gap_s: float = 900.0) -> None:
        now = self.clock()
        last = self.one("SELECT MAX(ts) AS ts FROM theme_snapshots")
        if last and last["ts"] and now - last["ts"] < min_gap_s:
            return
        for t in themes:
            self.execute("INSERT INTO theme_snapshots(theme,ts,tokens,new_launches,vol_h1) VALUES(?,?,?,?,?)",
                         (t["theme"], now, t["tokens"], t["new_launches"], t["vol_h1"]))

    def previous_themes(self, older_than_s: float = 3000.0) -> dict[str, dict]:
        """The most recent reading that is at least ``older_than_s`` old, per theme."""
        cutoff = self.clock() - older_than_s
        rows = self.query(
            "SELECT theme,tokens,new_launches,vol_h1,ts FROM theme_snapshots WHERE ts<=? ORDER BY ts DESC", (cutoff,))
        out: dict[str, dict] = {}
        for row in rows:
            out.setdefault(row["theme"], row)
        return out
