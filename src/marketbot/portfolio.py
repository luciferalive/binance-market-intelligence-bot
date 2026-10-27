"""Read-only view of your real Binance Spot account.

Safety design
-------------
* ``BinanceReadOnlyClient`` can only issue HTTP GET requests. It has no order, transfer or
  withdrawal methods at all, so a bug elsewhere in this project cannot trade or move funds.
* Before reading anything, the client asks Binance what the API key is allowed to do and
  **refuses to continue if the key can withdraw**. Other risky permissions (spot trading,
  margin, futures, transfers, no IP restriction) produce warnings.
* Credentials come from environment variables (``BINANCE_API_KEY`` / ``BINANCE_API_SECRET``),
  never from command-line arguments, and are never written to disk or included in errors.

Create the key in Binance with **Enable Reading only** and restrict it to your IP address.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import binance_public as bp
from .net import HttpError, get_json, to_float

STABLE_VALUE_ASSETS = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USD1", "PYUSD"}
TRADE_QUOTES = ("USDT", "USDC", "FDUSD")
DUST_USD = 1.0
CONCENTRATION_WARN_PCT = 40.0


class UnsafeKeyError(RuntimeError):
    """Raised when the API key has permissions this project refuses to work with."""


@dataclass
class KeyCheck:
    can_read: bool
    warnings: list[str]
    permissions: dict[str, bool]


class BinanceReadOnlyClient:
    """Signed GET-only client for the endpoints needed to display a portfolio."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        getter: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
        recv_window: int = 10_000,
    ):
        if not api_key or not api_secret:
            raise ValueError("Binance API key and secret are required")
        self._key = api_key
        self._secret = api_secret.encode("utf-8")
        self._get = getter or get_json
        self._clock = clock
        self._recv_window = recv_window
        self._offset_ms: int | None = None

    def __repr__(self) -> str:  # never expose secrets in logs or tracebacks
        return "BinanceReadOnlyClient(key=***, secret=***)"

    def _timestamp(self) -> int:
        if self._offset_ms is None:
            try:
                self._offset_ms = bp.server_time_ms(self._get) - int(self._clock() * 1000)
            except (HttpError, KeyError, ValueError, TypeError):
                # Do not remember a failed sync: use the local clock now and try again next call.
                return int(self._clock() * 1000)
        return int(self._clock() * 1000) + self._offset_ms

    def _signed_get(self, path: str, params: dict[str, Any] | None = None, _retried: bool = False) -> Any:
        query_params = dict(params or {})
        query_params["timestamp"] = self._timestamp()
        query_params["recvWindow"] = self._recv_window
        query = urllib.parse.urlencode(query_params)
        signature = hmac.new(self._secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{bp.BASE_URL}{path}?{query}&signature={signature}"
        try:
            # Never follow redirects with the API key header attached.
            return self._get(url, headers={"X-MBX-APIKEY": self._key}, follow_redirects=False)
        except HttpError as exc:
            if not _retried and "-1021" in str(exc):  # timestamp outside recvWindow: resync once
                self._offset_ms = None
                return self._signed_get(path, params, _retried=True)
            raise

    # --- the only operations this client offers --------------------------------------
    def api_restrictions(self) -> dict:
        return self._signed_get("/sapi/v1/account/apiRestrictions")

    def account(self) -> dict:
        return self._signed_get("/api/v3/account", {"omitZeroBalances": "true"})

    def my_trades(self, symbol: str, from_id: int | None = None, limit: int = 1000) -> list[dict]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if from_id is not None:
            params["fromId"] = from_id
        return self._signed_get("/api/v3/myTrades", params)

    def prices(self) -> dict[str, float]:
        return bp.ticker_prices(self._get)

    def tickers_24h(self) -> dict[str, dict]:
        return bp.ticker_24h(self._get)


def check_key(client: BinanceReadOnlyClient, allow_unverified: bool = False) -> KeyCheck:
    """Inspect the key's permissions. Raises ``UnsafeKeyError`` if it can withdraw funds."""
    try:
        info = client.api_restrictions()
    except HttpError as exc:
        if not allow_unverified:
            raise UnsafeKeyError(
                "Could not verify what this API key is allowed to do "
                f"({exc}). Refusing to continue. If you are certain the key is read-only you "
                "can override with --i-verified-key-is-read-only."
            ) from exc
        return KeyCheck(True, ["Key permissions could not be verified (override in use)."], {})

    if not isinstance(info, dict) or "enableWithdrawals" not in info:
        # A reply that does not say whether withdrawals are enabled is not a "no".
        if not allow_unverified:
            raise UnsafeKeyError(
                "Binance did not say whether this API key can withdraw funds. Refusing to "
                "continue. If you are certain the key is read-only you can override with "
                "--i-verified-key-is-read-only."
            )
        return KeyCheck(True, ["Key permissions could not be verified (override in use)."], {})

    permissions = {
        "reading": bool(info.get("enableReading")),
        "withdrawals": bool(info.get("enableWithdrawals")),
        "spot_and_margin_trading": bool(info.get("enableSpotAndMarginTrading")),
        "margin": bool(info.get("enableMargin")),
        "futures": bool(info.get("enableFutures")),
        "internal_transfer": bool(info.get("enableInternalTransfer")),
        "universal_transfer": bool(info.get("permitsUniversalTransfer")),
        "ip_restricted": bool(info.get("ipRestrict")),
    }
    if permissions["withdrawals"]:
        raise UnsafeKeyError(
            "This API key has WITHDRAWALS enabled. Refusing to use it. Create a new key with "
            "'Enable Reading' only, then delete this one in Binance (Account > API Management)."
        )
    warnings = []
    if permissions["spot_and_margin_trading"]:
        warnings.append("Key can place spot/margin trades. This project never trades with it, "
                        "but a read-only key is safer.")
    for name, label in (("margin", "margin"), ("futures", "futures"),
                        ("internal_transfer", "internal transfers"),
                        ("universal_transfer", "universal transfers")):
        if permissions[name]:
            warnings.append(f"Key has {label} permission enabled. Remove it.")
    if not permissions["ip_restricted"]:
        warnings.append("Key is not restricted to your IP address. Add an IP allowlist in Binance.")
    if not permissions["reading"]:
        warnings.append("Key does not have 'Enable Reading'; account data will fail.")
    return KeyCheck(permissions["reading"], warnings, permissions)


# ---------------------------------------------------------------- valuation & cost basis

def usd_price(asset: str, prices: dict[str, float]) -> tuple[float | None, str | None]:
    """Price of one unit of ``asset`` in USD terms and the market used to derive it."""
    if asset in STABLE_VALUE_ASSETS:
        return 1.0, None
    for quote in TRADE_QUOTES:
        pair = f"{asset}{quote}"
        if pair in prices:
            return prices[pair], pair
    btc_pair, btc_usdt = f"{asset}BTC", prices.get("BTCUSDT")
    if btc_pair in prices and btc_usdt:
        return prices[btc_pair] * btc_usdt, btc_pair
    return None, None


def resolve_asset(asset: str, prices: dict[str, float]) -> tuple[str, str | None]:
    """Binance reports Simple Earn flexible balances as ``LD<ASSET>`` (for example ``LDBTC``)."""
    if asset.startswith("LD") and len(asset) > 2:
        underlying = asset[2:]
        if usd_price(asset, prices)[0] is None and usd_price(underlying, prices)[0] is not None:
            return underlying, "Simple Earn"
    return asset, None


def compute_cost_basis(trades: list[dict], base: str, bnb_usdt: float | None = None) -> dict:
    """Average-cost basis from spot trade history.

    Returns the quantity the history can account for, its cost, the average cost, and realized
    PnL. Deposits, airdrops, Earn, Convert and transfers have no trade record, so the tracked
    quantity can be lower than what you hold; callers should show that coverage.
    """
    qty = 0.0
    cost = 0.0
    realized = 0.0

    def order(trade: dict) -> tuple[float, float]:
        return (to_float(trade.get("time"), 0.0) or 0.0, to_float(trade.get("id"), 0.0) or 0.0)

    for trade in sorted((t for t in trades if isinstance(t, dict)), key=order):
        amount = to_float(trade.get("qty"))
        quote_amount = to_float(trade.get("quoteQty"))
        if amount is None or quote_amount is None:
            continue  # skip a malformed row rather than failing the whole portfolio
        fee = to_float(trade.get("commission"), 0.0) or 0.0
        fee_asset = str(trade.get("commissionAsset", ""))
        fee_base = fee if fee_asset == base else 0.0
        if fee_asset == base:
            fee_quote = 0.0  # already taken off the quantity; do not also add it to the cost
        elif fee_asset in STABLE_VALUE_ASSETS:
            fee_quote = fee
        elif fee_asset == "BNB" and bnb_usdt:
            fee_quote = fee * bnb_usdt
        else:
            fee_quote = 0.0
        if trade.get("isBuyer"):
            qty += amount - fee_base
            cost += quote_amount + fee_quote
        else:
            if qty <= 1e-12 or amount <= 0:
                continue
            sold = min(amount, qty)
            share = sold / amount
            average = cost / qty
            realized += (quote_amount - fee_quote) * share - average * sold
            cost -= average * sold
            qty -= sold
    return {
        "tracked_qty": max(qty, 0.0),
        "cost": max(cost, 0.0),
        "avg_cost": (cost / qty) if qty > 1e-12 else None,
        "realized_pnl": realized,
    }


def _fetch_all_trades(
    client: BinanceReadOnlyClient, symbol: str, max_pages: int = 10
) -> tuple[list[dict], bool]:
    """Trade history oldest-first. Returns ``(trades, truncated)``.

    Without ``fromId`` Binance returns the *most recent* trades, so paging starts at 0 to walk
    the history forward. ``truncated`` is true if the page cap stopped us before the end.
    """
    trades: list[dict] = []
    from_id = 0
    for _ in range(max_pages):
        page = client.my_trades(symbol, from_id=from_id)
        if not isinstance(page, list):
            break
        trades.extend(page)
        if len(page) < 1000:
            return trades, False
        last_id = to_float(page[-1].get("id")) if isinstance(page[-1], dict) else None
        if last_id is None:
            break
        from_id = int(last_id) + 1
    return trades, True


def build_snapshot(
    client: BinanceReadOnlyClient,
    *,
    with_pnl: bool = True,
    allow_unverified_key: bool = False,
    trade_cache: dict[str, dict] | None = None,
) -> dict:
    """Assemble holdings, allocation, 24h change and (optionally) PnL into one dictionary."""
    key = check_key(client, allow_unverified=allow_unverified_key)
    account = client.account()
    prices = client.prices()
    tickers = client.tickers_24h()
    warnings = list(key.warnings)

    holdings: list[dict] = []
    unvalued: list[dict] = []
    for balance in account.get("balances", []):
        free = to_float(balance.get("free"), 0.0) or 0.0
        locked = to_float(balance.get("locked"), 0.0) or 0.0
        qty = free + locked
        if qty <= 0:
            continue
        reported = str(balance.get("asset", ""))
        asset, note = resolve_asset(reported, prices)
        price, pair = usd_price(asset, prices)
        if price is None:
            unvalued.append({"asset": reported, "quantity": qty})
            continue
        change = None
        if pair and pair.endswith(TRADE_QUOTES):
            change = to_float((tickers.get(pair) or {}).get("priceChangePercent"))
        value = qty * price
        holdings.append({
            "asset": asset,
            "reported_as": reported if reported != asset else None,
            "note": note,
            "quantity": qty,
            "free": free,
            "locked": locked,
            "price_usd": price,
            "value_usd": value,
            "change_24h_pct": change,
            "is_dust": value < DUST_USD,
        })

    # Merge rows that resolve to the same asset (for example BTC plus LDBTC).
    merged: dict[str, dict] = {}
    for row in holdings:
        existing = merged.get(row["asset"])
        if existing is None:
            merged[row["asset"]] = row
        else:
            for field_name in ("quantity", "free", "locked", "value_usd"):
                existing[field_name] += row[field_name]
            existing["note"] = existing["note"] or row["note"]
            existing["is_dust"] = existing["value_usd"] < DUST_USD
    holdings = sorted(merged.values(), key=lambda r: r["value_usd"], reverse=True)
    total = sum(r["value_usd"] for r in holdings)

    change_usd = 0.0
    for row in holdings:
        row["weight_pct"] = (row["value_usd"] / total * 100) if total else 0.0
        if row["change_24h_pct"] is not None:
            factor = 1 + row["change_24h_pct"] / 100
            if factor <= 0:  # a rounded -100% move: the previous value cannot be derived
                row["change_24h_pct"] = None
                continue
            change_usd += row["value_usd"] - row["value_usd"] / factor

    if with_pnl:
        cache = trade_cache if trade_cache is not None else {}
        bnb_usdt = prices.get("BNBUSDT")
        for row in holdings:
            asset = row["asset"]
            if asset in STABLE_VALUE_ASSETS:
                continue
            # Merge every quote pair of this asset into one time-ordered stream, so a buy on
            # BTCUSDT and a later sell on BTCUSDC net against each other.
            all_trades: list[dict] = []
            for quote in TRADE_QUOTES:
                symbol = f"{asset}{quote}"
                if symbol not in prices:
                    continue
                entry = cache.get(symbol)
                if entry is None:
                    try:
                        trades, truncated = _fetch_all_trades(client, symbol)
                    except HttpError as exc:
                        warnings.append(f"Trade history for {symbol} unavailable: {exc}")
                        continue
                    entry = {"trades": trades, "truncated": truncated}
                    cache[symbol] = entry
                if entry["truncated"]:
                    warnings.append(
                        f"{symbol} has a very long trade history; only the oldest part was read, "
                        f"so {asset} cost data may be incomplete."
                    )
                all_trades.extend(entry["trades"])
            if not all_trades:
                row.update({"avg_cost": None, "unrealized_pnl": None, "unrealized_pnl_pct": None,
                            "realized_pnl": None, "basis_coverage_pct": 0.0})
                continue
            result = compute_cost_basis(all_trades, asset, bnb_usdt)
            tracked, cost_total = result["tracked_qty"], result["cost"]
            held = row["quantity"]
            if tracked > held and tracked > 1e-12:
                # Part of what the history says you bought has left this account (withdrawn,
                # transferred or spent some other way). Keep only the cost of what is still here.
                cost_total *= held / tracked
                tracked = held
            unrealized = tracked * row["price_usd"] - cost_total
            row.update({
                "avg_cost": (cost_total / tracked) if tracked > 1e-12 else None,
                "unrealized_pnl": unrealized if tracked > 1e-12 else None,
                "unrealized_pnl_pct": (unrealized / cost_total * 100) if cost_total > 0 else None,
                "realized_pnl": result["realized_pnl"],
                "basis_coverage_pct": (tracked / held * 100) if held else 0.0,
            })

    flags = []
    for row in holdings:
        if row["weight_pct"] >= CONCENTRATION_WARN_PCT and row["asset"] not in STABLE_VALUE_ASSETS:
            flags.append(f"{row['asset']} is {row['weight_pct']:.0f}% of your portfolio.")
    stable_value = sum(r["value_usd"] for r in holdings if r["asset"] in STABLE_VALUE_ASSETS)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_usd": total,
        "change_24h_usd": change_usd,
        "change_24h_pct": (change_usd / (total - change_usd) * 100) if total - change_usd > 0 else None,
        "stable_pct": (stable_value / total * 100) if total else 0.0,
        "holdings": holdings,
        "unvalued": unvalued,
        "flags": flags,
        "warnings": warnings,
        "key_permissions": key.permissions,
        "pnl_note": (
            "PnL uses average cost from your Binance spot trade history (USDT, USDC, FDUSD pairs). "
            "Coins that arrived by deposit, Earn, Convert or airdrop have no cost basis there; "
            "'coverage' shows how much of each position the history explains."
        ) if with_pnl else None,
    }
