"""Explicitly connected Binance Spot Demo Trading adapter.

This module refuses to fall back to live endpoints or generic testnet routing.
Credentials are read from environment variables only.
"""
from __future__ import annotations

import os
from typing import Any


class BinanceSpotDemo:
    """CCXT adapter that explicitly enables Binance Demo Trading."""

    def __init__(self, api_key: str | None = None, secret: str | None = None):
        key = api_key or os.getenv("BINANCE_DEMO_API_KEY")
        password = secret or os.getenv("BINANCE_DEMO_API_SECRET")
        if not key or not password:
            raise RuntimeError(
                "Missing demo credentials. Set BINANCE_DEMO_API_KEY and "
                "BINANCE_DEMO_API_SECRET in your local environment."
            )

        import ccxt

        self.exchange = ccxt.binance({
            "apiKey": key,
            "secret": password,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

        # Binance Demo Trading is not interchangeable with the old Spot Testnet.
        # Fail closed if this CCXT version cannot select the demo environment.
        enable_demo = getattr(self.exchange, "enable_demo_trading", None)
        if not callable(enable_demo):
            raise RuntimeError(
                "Your CCXT version lacks Binance Demo Trading support. "
                "Upgrade CCXT; refusing to use set_sandbox_mode() as a fallback."
            )
        enable_demo(True)

    def account_balance(self) -> dict[str, Any]:
        """Return balances from the connected Binance Spot Demo account."""
        return self.exchange.fetch_balance()

    def market_order(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Submit a market order to Binance Demo Trading only."""
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.exchange.load_markets()
        market = self.exchange.market(symbol)
        if not market.get("spot"):
            raise ValueError(f"{symbol} is not a Spot market")
        return self.exchange.create_order(symbol, "market", side, amount)
