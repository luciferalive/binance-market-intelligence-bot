"""Persistent paper-trading account using public Binance Spot data only.

This module simulates fills and stores a virtual wallet in SQLite. It never
uses private endpoints or sends orders to Binance.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt

from .risk import RiskLimits, size_position
from .scanner import make_exchange, scan_markets


class PaperTrader:
    def __init__(
        self,
        db_path: str = "data/paper_account.sqlite3",
        starting_cash: float = 1000.0,
        quote: str = "USDT",
        timeframe: str = "1h",
        top: int = 50,
        scan_interval: int = 300,
        fee_rate: float = 0.001,
    ) -> None:
        if starting_cash <= 0 or scan_interval < 30 or top < 1:
            raise ValueError("starting_cash/top must be positive and scan_interval >= 30")
        if not 0 <= fee_rate < 0.05:
            raise ValueError("fee_rate must be in [0, 0.05)")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.quote = quote.upper()
        self.timeframe = timeframe
        self.top = top
        self.scan_interval = scan_interval
        self.fee_rate = fee_rate
        self.limits = RiskLimits()
        self.exchange = make_exchange()
        self._init_db(starting_cash)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self, starting_cash: float) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    signal_score INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO account(id,initial_cash,cash,created_at) VALUES(1,?,?,?)",
                (starting_cash, starting_cash, self._now()),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            account = conn.execute("SELECT * FROM account WHERE id=1").fetchone()
            positions = conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
            trades = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 10").fetchall()
        tickers = {}
        if positions:
            try:
                tickers = self.exchange.fetch_tickers([p["symbol"] for p in positions])
            except ccxt.BaseError:
                tickers = {}
        market_value = 0.0
        unrealized = 0.0
        open_positions = []
        for p in positions:
            ticker = tickers.get(p["symbol"], {})
            price = float(ticker.get("last") or p["entry_price"])
            value = price * p["quantity"]
            pnl = (price - p["entry_price"]) * p["quantity"] - p["entry_fee"]
            market_value += value
            unrealized += pnl
            open_positions.append({
                "symbol": p["symbol"], "quantity": p["quantity"],
                "entry_price": p["entry_price"], "last_price": price,
                "stop_loss": p["stop_loss"], "target": p["target"],
                "unrealized_pnl": pnl,
            })
        return {
            "mode": "PAPER_ONLY",
            "initial_virtual_usdt": account["initial_cash"],
            "cash_usdt": account["cash"],
            "market_value_usdt": market_value,
            "equity_usdt": account["cash"] + market_value,
            "unrealized_pnl_usdt": unrealized,
            "open_positions": open_positions,
            "recent_trades": [dict(t) for t in trades],
            "database": str(self.db_path),
        }

    def _close_position(self, symbol: str, exit_price: float, reason: str) -> None:
        with self._connect() as conn:
            p = conn.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
            if not p:
                return
            gross = (exit_price - p["entry_price"]) * p["quantity"]
            exit_fee = exit_price * p["quantity"] * self.fee_rate
            net = gross - exit_fee - p["entry_fee"]
            conn.execute("UPDATE account SET cash=cash+? WHERE id=1", (gross - exit_fee,))
            conn.execute(
                """INSERT INTO trades(symbol,side,quantity,price,fee,realized_pnl,reason,timestamp)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (symbol, "SELL", p["quantity"], exit_price, exit_fee, net, reason, self._now()),
            )
            conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))

    def _manage_positions(self) -> None:
        with self._connect() as conn:
            positions = conn.execute("SELECT * FROM positions").fetchall()
        if not positions:
            return
        try:
            tickers = self.exchange.fetch_tickers([p["symbol"] for p in positions])
        except ccxt.BaseError as exc:
            print(f"[paper] price refresh failed; keeping positions open: {exc}")
            return
        for p in positions:
            ticker = tickers.get(p["symbol"], {})
            last = ticker.get("last")
            if last is None:
                continue
            price = float(last)
            if price <= p["stop_loss"]:
                self._close_position(p["symbol"], price, "stop_loss")
                print(f"[paper] SELL {p['symbol']} at {price:.8g} (stop_loss)")
            elif price >= p["target"]:
                self._close_position(p["symbol"], price, "take_profit")
                print(f"[paper] SELL {p['symbol']} at {price:.8g} (take_profit)")

    def _open_from_signal(self, row: dict[str, Any]) -> None:
        symbol = row.get("symbol")
        if not symbol or row.get("signal") != "LONG_SETUP":
            return
        entry = float(row.get("close") or 0)
        stop = float(row.get("stop_loss") or 0)
        if entry <= 0 or stop <= 0 or stop >= entry:
            return
        with self._connect() as conn:
            account = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
            existing = conn.execute("SELECT 1 FROM positions WHERE symbol=?", (symbol,)).fetchone()
            positions = conn.execute("SELECT * FROM positions").fetchall()
        if existing or len(positions) >= self.limits.max_open_positions:
            return
        equity = float(account["cash"])
        current_exposure = sum(float(p["entry_price"]) * float(p["quantity"]) for p in positions)
        max_total = equity * self.limits.max_total_exposure_fraction
        remaining_exposure = max(0.0, max_total - current_exposure)
        available_cash = min(equity, remaining_exposure)
        plan = size_position(
            equity=max(equity, 0.01),
            entry=entry,
            stop=stop,
            limits=self.limits,
            available_cash=available_cash / (1 + self.fee_rate),
        )
        if plan.quantity <= 0 or plan.notional * (1 + self.fee_rate) > equity:
            return
        fee = plan.notional * self.fee_rate
        target = float(row.get("target") or (entry + 2 * (entry - stop)))
        with self._connect() as conn:
            # Recheck cash and duplicate position immediately before simulated fill.
            cash_row = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
            if cash_row["cash"] < plan.notional + fee:
                return
            if conn.execute("SELECT 1 FROM positions WHERE symbol=?", (symbol,)).fetchone():
                return
            conn.execute("UPDATE account SET cash=cash-? WHERE id=1", (plan.notional + fee,))
            conn.execute(
                """INSERT INTO positions(symbol,quantity,entry_price,stop_loss,target,entry_fee,opened_at,signal_score)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (symbol, plan.quantity, entry, stop, target, fee, self._now(), int(row.get("score") or 0)),
            )
            conn.execute(
                """INSERT INTO trades(symbol,side,quantity,price,fee,realized_pnl,reason,timestamp)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (symbol, "BUY", plan.quantity, entry, fee, -fee, "signal_entry", self._now()),
            )
        print(f"[paper] BUY {symbol} qty={plan.quantity:.8g} entry={entry:.8g} stop={stop:.8g} target={target:.8g}")

    def cycle(self) -> None:
        print(f"[paper] scan started at {self._now()}")
        self._manage_positions()
        rows = scan_markets(
            quote=self.quote, timeframe=self.timeframe, top=self.top, limit=160
        )
        for row in rows:
            self._open_from_signal(row)
        print(json.dumps(self.status(), indent=2))

    def run_forever(self) -> None:
        print(f"[paper] virtual account initialized/persisted at {self.db_path}")
        print(f"[paper] starting equity: {self.status()['initial_virtual_usdt']} {self.quote}")
        print("[paper] PAPER ONLY: no private account endpoints or real orders. Ctrl+C stops the loop.")
        while True:
            started = time.monotonic()
            try:
                self.cycle()
            except KeyboardInterrupt:
                print("\\n[paper] stopped by user; virtual account state is saved.")
                return
            except (ccxt.BaseError, sqlite3.Error, ValueError, TypeError) as exc:
                print(f"[paper] cycle failed; will retry next interval: {exc}")
            wait = max(0.0, self.scan_interval - (time.monotonic() - started))
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                print("\\n[paper] stopped by user; virtual account state is saved.")
                return
