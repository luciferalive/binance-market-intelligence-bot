"""Supported chains and what each one can actually do.

A chain is only switchable in the UI for a capability that is really implemented. The matrix is
shown to the user, so "not available" is visible instead of silently missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chain:
    id: str                 # DexScreener / internal id
    name: str
    gecko: str              # GeckoTerminal network id
    goplus: str | None      # GoPlus chain id for EVM; None for Solana (separate endpoint)
    native: str             # native gas / quote token symbol
    explorer: str           # address explorer prefix
    capabilities: dict[str, bool] = field(default_factory=dict)


def _caps(**overrides: bool) -> dict[str, bool]:
    base = {
        "discovery": True,           # new pools, trending pools (GeckoTerminal)
        "security": True,            # contract checks (GoPlus)
        "sell_simulation": True,     # provider honeypot / sell simulation (GoPlus, EVM only)
        "holders": True,             # top holders (GoPlus)
        "trades": True,              # recent swaps (GeckoTerminal)
        "ohlcv": True,               # candles (GeckoTerminal)
        "paper_trading": True,       # simulated entries and exits
        "live_trading": False,       # NOT implemented in this build, on any chain
        "wallet_history": False,     # needs a wallet-analytics provider; not connected
        "wallet_graph": False,       # needs funding-flow data; not connected
        "social": False,             # needs a social data provider; not connected
    }
    base.update(overrides)
    return base


CHAINS: dict[str, Chain] = {
    "solana": Chain("solana", "Solana", "solana", None, "SOL", "https://solscan.io/token/",
                    _caps(sell_simulation=False)),
    "base": Chain("base", "Base", "base", "8453", "ETH", "https://basescan.org/token/", _caps()),
    "ethereum": Chain("ethereum", "Ethereum", "eth", "1", "ETH", "https://etherscan.io/token/", _caps()),
    "bsc": Chain("bsc", "BNB Chain", "bsc", "56", "BNB", "https://bscscan.com/token/", _caps()),
}
DEFAULT_CHAIN = "solana"

CAPABILITY_LABELS = {
    "discovery": "New-pool discovery",
    "security": "Contract security checks",
    "sell_simulation": "Sell simulation (provider)",
    "holders": "Holder distribution",
    "trades": "Recent trades",
    "ohlcv": "Price candles",
    "paper_trading": "Paper trading",
    "live_trading": "Live trading",
    "wallet_history": "Wallet P&L history",
    "wallet_graph": "Wallet relationship graph",
    "social": "Social data",
}


def get_chain(chain_id: str) -> Chain:
    try:
        return CHAINS[chain_id]
    except KeyError:
        raise ValueError(f"Unsupported chain: {chain_id!r}") from None


def chain_from_gecko(network: str) -> str | None:
    for chain in CHAINS.values():
        if chain.gecko == network:
            return chain.id
    return None


def describe() -> list[dict]:
    """Chains with their capability matrix, for the UI."""
    return [
        {"id": c.id, "name": c.name, "native": c.native, "capabilities": dict(c.capabilities)}
        for c in CHAINS.values()
    ]
