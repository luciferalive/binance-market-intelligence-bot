"""Autonomous Binance Spot Demo Trading loop.

This uses the connected exchange demo balance as the source of truth. The
configured allocation is only a strategy cap; it is not a fabricated wallet.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt

from .binance_demo import BinanceSpotDemo
from .scanner import scan_markets

logger = logging.getLogger(__name__)


class BinanceDemoTrader:
    def __init__(
        self,
        *,
        db_path: str = "data/binance_demo_trader.sqlite3",
        quote: str = "USDT",
        timeframe: str = "1h",
        top: int = 50,
        interval: int = 300,
        max_allocation: float = 1000.0,
        max_positions: int = 5,
        exchange: Any | None = None,
        scanner: Callable[..., list[dict[str, Any]]] = scan_markets,
    ):
        if interval < 30 or top < 1 or max_allocation <= 0 or max_positions < 1:
            raise ValueError("interval >= 30, top/max_positions >= 1, and allocation > 0 required")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.quote = quote.upper()
        self.timeframe = timeframe
        self.top = top
        self.interval = interval
        self.max_allocation = float(max_allocation)
        self.max_positions = max_positions
        self.exchange = exchange or BinanceSpotDemo()
        self.scanner = scanner
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS demo_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target REAL NOT NULL,
                    entry_notional REAL NOT NULL,
                    opened_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS demo_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    average_price REAL,
                    status TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _positions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM demo_positions ORDER BY symbol"
            ).fetchall()]

    def _record_order(self, order: dict[str, Any], symbol: str, side: str,
                      reason: str, quantity: float, average: float | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO demo_orders(exchange_order_id,symbol,side,quantity,
                   average_price,status,reason,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (str(order.get("id") or ""), symbol, side.upper(), quantity, average,
                 str(order.get("status") or "unknown"), reason, self._now()),
            )

    def _order_fill(self, order: dict[str, Any], symbol: str) -> tuple[float, float | None]:
        filled = float(order.get("filled") or 0)
        average = order.get("average")
        if filled > 0:
            return filled, float(average) if average else None
        order_id = order.get("id")
        fetch_order = getattr(self.exchange.exchange, "fetch_order", None)
        if order_id and callable(fetch_order):
            try:
                updated = fetch_order(order_id, symbol)
                filled = float(updated.get("filled") or 0)
                average = updated.get("average")
                return filled, float(average) if average else None
            except ccxt.BaseError as exc:
                logger.warning("Could not refresh demo order fill for %s: %s", symbol, exc)
        return 0.0, None

    def _manage_positions(self, balance: dict[str, Any]) -> None:
        positions = self._positions()
        if not positions:
            return
        tickers = self.exchange.exchange.fetch_tickers([p["symbol"] for p in positions])
        total = balance.get("total") or {}
        free = balance.get("free") or {}
        for position in positions:
            symbol = position["symbol"]
            market = self.exchange.exchange.market(symbol)
            base = market["base"]
            # Never sell more than both the bot-tracked quantity and the connected
            # account's currently available base balance.
            account_base = float(total.get(base) or 0)
            sellable = min(float(position["quantity"]), account_base, float(free.get(base) or 0))
            last = tickers.get(symbol, {}).get("last")
            if not last or sellable <= 0:
                continue
            price = float(last)
            reason = "stop_loss" if price <= position["stop_loss"] else (
                "take_profit" if price >= position["target"] else ""
            )
            if not reason:
                continue
            amount = float(self.exchange.exchange.amount_to_precision(symbol, sellable))
            if amount <= 0:
                continue
            order = self.exchange.market_order(symbol, "sell", amount)
            filled, average = self._order_fill(order, symbol)
            self._record_order(order, symbol, "sell", reason, filled, average)
            if filled > 0:
                with self._connect() as conn:
                    if filled >= float(position["quantity"]) * 0.999:
                        conn.execute("DELETE FROM demo_positions WHERE symbol=?", (symbol,))
                    else:
                        conn.execute(
                            "UPDATE demo_positions SET quantity=quantity-?, entry_notional=entry_notional*(1-?) WHERE symbol=?",
                            (filled, filled / float(position["quantity"]), symbol),
                        )
                print(f"[demo-auto] SELL {symbol} filled={filled:g} reason={reason}")

    def _enter(self, row: dict[str, Any], balance: dict[str, Any]) -> None:
        symbol = row.get("symbol")
        if not symbol or row.get("signal") != "LONG_SETUP":
            return
        positions = self._positions()
        if len(positions) >= self.max_positions or any(p["symbol"] == symbol for p in positions):
            return
        entry = float(row.get("close") or 0)
        stop = float(row.get("stop_loss") or 0)
        target = float(row.get("target") or 0)
        if entry <= 0 or stop <= 0 or stop >= entry or target <= entry:
            return

        free_quote = float((balance.get("free") or {}).get(self.quote) or 0)
        committed = sum(float(p["entry_notional"]) for p in positions)
        cap_remaining = max(0.0, self.max_allocation - committed)
        # The allocation cap limits this strategy's committed entry notional.
        # The exchange's real free quote balance remains the hard funding limit.
        budget = min(cap_remaining, free_quote * 0.995)
        if budget <= 0:
            return
        market = self.exchange.exchange.market(symbol)
        if market.get("quote") != self.quote or not market.get("spot"):
            return
        amount = float(self.exchange.exchange.amount_to_precision(symbol, budget / entry))
        limits = market.get("limits") or {}
        min_amount = ((limits.get("amount") or {}).get("min") or 0)
        min_cost = ((limits.get("cost") or {}).get("min") or 0)
        if amount <= 0 or amount < float(min_amount) or amount * entry < float(min_cost):
            return

        order = self.exchange.market_order(symbol, "buy", amount)
        filled, average = self._order_fill(order, symbol)
        self._record_order(order, symbol, "buy", "LONG_SETUP", filled, average)
        if filled <= 0:
            print(f"[demo-auto] BUY {symbol} submitted but no fill confirmed; not tracking a position")
            return
        fill_price = average or entry
        actual_notional = filled * fill_price
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO demo_positions(symbol,quantity,entry_price,
                   stop_loss,target,entry_notional,opened_at) VALUES(?,?,?,?,?,?,?)""",
                (symbol, filled, fill_price, stop, target, actual_notional, self._now()),
            )
        print(f"[demo-auto] BUY {symbol} filled={filled:g} avg={fill_price:g} "
              f"stop={stop:g} target={target:g}")

    def cycle(self) -> None:
        balance = self.exchange.account_balance()
        self._manage_positions(balance)
        # Refresh after exits so new entries use the actual post-order account balance.
        balance = self.exchange.account_balance()
        rows = self.scanner(quote=self.quote, timeframe=self.timeframe, top=self.top, limit=160)
        for row in rows:
            self._enter(row, balance)
            # Re-read connected account state after each order. No local wallet
            # is maintained or assumed to represent exchange funds.
            balance = self.exchange.account_balance()

    def run_forever(self) -> None:
        print("[demo-auto] Binance Spot Demo Trading only. Ctrl+C stops the loop.")
        print(f"[demo-auto] quote={self.quote} allocation_cap={self.max_allocation:g} "
              f"interval={self.interval}s database={self.db_path}")
        print("[demo-auto] allocation cap is a strategy limit, not a separate wallet.")
        while True:
            started = time.monotonic()
            try:
                self.cycle()
            except KeyboardInterrupt:
                print("\n[demo-auto] stopped; tracked state remains in SQLite.")
                return
            except (ccxt.BaseError, sqlite3.Error, ValueError, TypeError) as exc:
                print(f"[demo-auto] cycle error: {exc}")
            try:
                time.sleep(max(0.0, self.interval - (time.monotonic() - started)))
            except KeyboardInterrupt:
                print("\n[demo-auto] stopped; tracked state remains in SQLite.")
                return
