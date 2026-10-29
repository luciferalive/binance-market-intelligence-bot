"""Paper trading: simulated fills only. Nothing in this module can place a real order.

Fills model what a small swap would really cost: a pool fee, a flat network fee, and price
impact from a constant-product pool (``impact = usd / (liquidity/2 + usd)``). That is an
approximation, so the impact shown is labelled an estimate everywhere it appears.

Safety rules enforced before any simulated entry: emergency halt, maximum position size, maximum
open positions, minimum liquidity multiple, maximum slippage, price freshness, daily loss limit
and available cash. Exits (take-profit, stop-loss, trailing stop, time stop) are evaluated by
``mark``; the emergency halt blocks entries but never blocks exits.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .store import Store

MODE = "PAPER"


def price_impact_pct(liquidity_usd: float | None, usd: float) -> float | None:
    """Estimated price impact of trading ``usd`` against a pool, in percent. None if unknown."""
    if not liquidity_usd or liquidity_usd <= 0 or usd <= 0:
        return None
    half = liquidity_usd / 2.0
    return usd / (half + usd) * 100.0


def exit_table(liquidity_usd: float | None, sizes: tuple[float, ...] = (100, 500, 1000, 5000, 10000)) -> list[dict]:
    """Estimated impact of selling various sizes, for the liquidity tab."""
    return [{"usd": s, "impact_pct": price_impact_pct(liquidity_usd, s)} for s in sizes]


def _start_of_day(ts: float) -> float:
    d = datetime.fromtimestamp(ts, tz=UTC)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class PaperEngine:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock

    # -- state ------------------------------------------------------------------
    def capital(self) -> float:
        return float(self.store.get_setting("paper_capital_usd") or 0.0)

    def cash(self) -> float:
        value = self.store.get_setting("paper_cash_usd")
        return self.capital() if value is None else float(value)

    def halted(self) -> bool:
        return bool(self.store.get_setting("halted"))

    def halt(self, reason: str = "manual emergency stop") -> None:
        self.store.set_setting("halted", True)
        self.store.set_setting("halt_reason", reason[:200])
        self.store.journal_add("halt", detail={"reason": reason})

    def resume(self) -> None:
        self.store.set_setting("halted", False)
        self.store.set_setting("halt_reason", "")
        self.store.journal_add("resume")

    def realized_today(self) -> float:
        row = self.store.one("SELECT COALESCE(SUM(realized_pnl),0) AS p FROM paper_orders WHERE side='sell' AND ts>=?",
                             (_start_of_day(self.clock()),))
        return float(row["p"]) if row else 0.0

    # -- entry ------------------------------------------------------------------
    def check_entry(self, usd: float, price: float | None, liquidity: float | None,
                    price_ts: float | None, key: str) -> tuple[list[str], float | None]:
        """Reasons the entry is refused (empty = allowed) and the estimated impact."""
        s = self.store.get_setting
        problems: list[str] = []
        if self.halted():
            problems.append("Emergency stop is active: entries are blocked.")
        if not (usd > 0):
            problems.append("Size must be positive.")
        if usd > float(s("max_position_usd")):
            problems.append(f"Size is above the ${float(s('max_position_usd')):,.0f} position limit.")
        if not price or price <= 0:
            problems.append("No price available.")
        if price_ts is None or self.clock() - price_ts > float(s("max_price_age_s")):
            problems.append("Price is stale or its age is unknown.")
        if not liquidity or liquidity <= 0:
            problems.append("Liquidity unknown: cannot estimate slippage.")
        impact = price_impact_pct(liquidity, usd)
        if impact is not None and impact > float(s("max_slippage_pct")):
            problems.append(f"Estimated slippage {impact:.1f}% exceeds the {float(s('max_slippage_pct')):.1f}% limit.")
        if liquidity and usd > 0 and liquidity < usd * float(s("min_liquidity_multiple")):
            problems.append(f"Liquidity is under {float(s('min_liquidity_multiple')):.0f}x the position size.")
        if self.store.one("SELECT key FROM paper_positions WHERE key=?", (key,)):
            problems.append("Already holding this token in paper trading.")
        n = self.store.one("SELECT COUNT(*) AS n FROM paper_positions")["n"]
        if n >= int(s("max_open_positions")):
            problems.append("Maximum open positions reached.")
        if usd > self.cash() + 1e-9:
            problems.append("Not enough paper cash.")
        loss_limit = self.capital() * float(s("max_daily_loss_pct")) / 100.0
        if self.realized_today() <= -loss_limit:
            problems.append("Daily loss limit reached.")
        return problems, impact

    def buy(self, *, chain: str, pool: str, token: str, symbol: str, name: str, usd: float,
            price: float | None, liquidity: float | None, price_ts: float | None,
            tp_pct: float | None = None, sl_pct: float | None = None, trail_pct: float | None = None,
            max_hold_min: float | None = None, reason: str = "") -> dict[str, Any]:
        with self.store.lock:  # check and fill must be one atomic step across threads
            return self._buy_locked(chain=chain, pool=pool, token=token, symbol=symbol, name=name, usd=usd, price=price,
                                    liquidity=liquidity, price_ts=price_ts, tp_pct=tp_pct, sl_pct=sl_pct,
                                    trail_pct=trail_pct, max_hold_min=max_hold_min, reason=reason)

    def _buy_locked(self, *, chain: str, pool: str, token: str, symbol: str, name: str, usd: float,
                    price: float | None, liquidity: float | None, price_ts: float | None,
                    tp_pct: float | None, sl_pct: float | None, trail_pct: float | None,
                    max_hold_min: float | None, reason: str) -> dict[str, Any]:
        key = f"{chain}:{pool}"
        problems, impact = self.check_entry(usd, price, liquidity, price_ts, key)
        if usd > 0 and usd < 4 * float(self.store.get_setting("network_fee_usd")) + 1:
            problems.append("Size is too small: fees would consume it.")
        if problems:
            self.store.journal_add("entry_refused", key, symbol, {"problems": problems, "usd": usd})
            return {"ok": False, "problems": problems, "impact_pct": impact}
        s = self.store.get_setting
        fee = usd * float(s("fee_pct")) / 100.0 + float(s("network_fee_usd"))
        net = max(usd - fee, 0.0)
        impact = price_impact_pct(liquidity, net) or 0.0
        fill = price * (1 + impact / 100.0)
        qty = net / fill
        now = self.clock()

        def work(db):
            db.execute("INSERT INTO paper_positions(key,chain,pool,token,symbol,name,qty,cost_usd,opened_ts,tp_pct,sl_pct,"
                       "trail_pct,peak_price,max_hold_min,last_price,last_liquidity,last_ts,entry_liquidity) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key, chain, pool, token, symbol[:24], name[:60], qty, usd, now, tp_pct, sl_pct, trail_pct,
                        price, max_hold_min, price, liquidity, now, liquidity))
            db.execute("INSERT INTO paper_orders(ts,key,symbol,side,usd,qty,price,fee_usd,slippage_pct,reason,realized_pnl) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                       (now, key, symbol[:24], "buy", usd, qty, fill, fee, impact, reason[:200]))
            db.execute("INSERT INTO settings(k,v) VALUES('paper_cash_usd',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                       (str(self.cash() - usd),))
        self.store.transaction(work)
        self.store.journal_add("entry", key, symbol, {"usd": usd, "fill": fill, "slippage_pct": impact, "reason": reason})
        return {"ok": True, "fill_price": fill, "qty": qty, "fee_usd": fee, "slippage_pct": impact}

    # -- exit -------------------------------------------------------------------
    def sell(self, key: str, *, price: float | None, liquidity: float | None, reason: str = "manual") -> dict[str, Any]:
        with self.store.lock:  # read, delete and credit must not interleave with another sell
            return self._sell_locked(key, price, liquidity, reason)

    def _sell_locked(self, key: str, price: float | None, liquidity: float | None, reason: str) -> dict[str, Any]:
        pos = self.store.one("SELECT * FROM paper_positions WHERE key=?", (key,))
        if not pos:
            return {"ok": False, "problems": ["No such paper position."]}
        if not price or price <= 0:
            price = pos["last_price"]
        if not price:
            return {"ok": False, "problems": ["No price available."]}
        liquidity = liquidity or pos["last_liquidity"]
        value = pos["qty"] * price
        impact = price_impact_pct(liquidity, value)
        if impact is None:
            impact = 100.0  # unknown liquidity: assume the worst rather than a free exit
        proceeds_gross = value * (1 - impact / 100.0)
        s = self.store.get_setting
        fee = proceeds_gross * float(s("fee_pct")) / 100.0 + float(s("network_fee_usd"))
        proceeds = max(proceeds_gross - fee, 0.0)
        pnl = proceeds - pos["cost_usd"]
        now = self.clock()

        def work(db):
            db.execute("DELETE FROM paper_positions WHERE key=?", (key,))
            db.execute("INSERT INTO paper_orders(ts,key,symbol,side,usd,qty,price,fee_usd,slippage_pct,reason,realized_pnl) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (now, key, pos["symbol"], "sell", proceeds, pos["qty"], price * (1 - impact / 100.0), fee, impact,
                        reason[:200], pnl))
            db.execute("INSERT INTO settings(k,v) VALUES('paper_cash_usd',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                       (str(self.cash() + proceeds),))
        self.store.transaction(work)
        self.store.journal_add("exit", key, pos["symbol"], {"pnl": pnl, "reason": reason, "slippage_pct": impact,
                                                            "held_min": (now - pos["opened_ts"]) / 60})
        return {"ok": True, "pnl": pnl, "proceeds": proceeds, "slippage_pct": impact}

    def mark(self, key: str, price: float | None, liquidity: float | None) -> str | None:
        """Update a position with a fresh price and close it if a rule triggers. Returns the reason."""
        pos = self.store.one("SELECT * FROM paper_positions WHERE key=?", (key,))
        if not pos or not price or price <= 0:
            return None
        now = self.clock()
        peak = max(pos["peak_price"] or price, price)
        self.store.execute("UPDATE paper_positions SET last_price=?,last_liquidity=?,last_ts=?,peak_price=? WHERE key=?",
                           (price, liquidity if liquidity else pos["last_liquidity"], now, peak, key))
        value = pos["qty"] * price
        ret = (value - pos["cost_usd"]) / pos["cost_usd"] * 100 if pos["cost_usd"] else 0.0
        reason = None
        if pos["sl_pct"] and ret <= -abs(pos["sl_pct"]):
            reason = f"stop-loss {pos['sl_pct']:.0f}%"
        elif pos["tp_pct"] and ret >= abs(pos["tp_pct"]):
            reason = f"take-profit {pos['tp_pct']:.0f}%"
        elif pos["trail_pct"] and peak > 0 and (peak - price) / peak * 100 >= abs(pos["trail_pct"]):
            reason = f"trailing stop {pos['trail_pct']:.0f}% from peak"
        elif pos["max_hold_min"] and (now - pos["opened_ts"]) / 60 >= pos["max_hold_min"]:
            reason = "time stop"
        if reason:
            self.sell(key, price=price, liquidity=liquidity, reason=reason)
        return reason

    # -- reporting --------------------------------------------------------------
    def positions(self) -> list[dict]:
        out = []
        for p in self.store.query("SELECT * FROM paper_positions ORDER BY opened_ts DESC"):
            price = p["last_price"]
            value = p["qty"] * price if price else None
            impact = price_impact_pct(p["last_liquidity"], value) if value else None
            p["value_usd"] = value
            p["pnl_usd"] = (value - p["cost_usd"]) if value is not None else None
            p["pnl_pct"] = (p["pnl_usd"] / p["cost_usd"] * 100) if p["pnl_usd"] is not None and p["cost_usd"] else None
            p["exit_impact_pct"] = impact
            p["price_age_s"] = (self.clock() - p["last_ts"]) if p["last_ts"] else None
            out.append(p)
        return out

    def orders(self, limit: int = 200) -> list[dict]:
        return self.store.query("SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?", (limit,))

    def summary(self) -> dict:
        positions = self.positions()
        open_value = sum(p["value_usd"] or 0.0 for p in positions)
        sells = [o for o in self.store.query("SELECT realized_pnl FROM paper_orders WHERE side='sell'")]
        wins = [o for o in sells if (o["realized_pnl"] or 0) > 0]
        realized = sum(o["realized_pnl"] or 0.0 for o in sells)
        equity = self.cash() + open_value
        return {
            "mode": MODE, "capital": self.capital(), "cash": self.cash(), "open_value": open_value,
            "equity": equity, "return_pct": ((equity - self.capital()) / self.capital() * 100) if self.capital() else None,
            "realized_pnl": realized, "realized_today": self.realized_today(),
            "trades": len(sells), "win_rate": (len(wins) / len(sells) * 100) if sells else None,
            "halted": self.halted(), "halt_reason": self.store.get_setting("halt_reason"),
            "open_positions": len(positions),
            "live_trading": "Not implemented. This build can only simulate trades.",
        }
