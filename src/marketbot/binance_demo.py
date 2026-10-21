"""Explicitly sandboxed Binance Spot demo account adapter.

This module never permits live trading: sandbox mode is enabled before any
private API call, and credentials are read from environment variables only.
"""
from __future__ import annotations

import os
from typing import Any


class BinanceSpotDemo:
    """Thin CCXT adapter for Binance Spot demo trading."""

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
        # Enable sandbox immediately after constructing the exchange and before
        # loading markets or making any request.
        self.exchange.set_sandbox_mode(True)

    def account_balance(self) -> dict[str, Any]:
        """Return demo-account balances, never live-account balances."""
        return self.exchange.fetch_balance()

    def market_order(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Submit a market order to the sandbox only."""
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.exchange.load_markets()
        if symbol not in self.exchange.markets:
            raise ValueError(f"Market {symbol} is not available on Binance Spot demo")
        return self.exchange.create_order(symbol, "market", side, amount)
