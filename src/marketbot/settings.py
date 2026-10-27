"""Local settings: a minimal ``.env`` loader and credential helpers.

Secrets are only ever read from environment variables (optionally populated from a local
``.env`` file that is git-ignored). Nothing here logs or returns secrets in error text.
"""
from __future__ import annotations

import os
from pathlib import Path

LIVE_KEY_VAR = "BINANCE_API_KEY"
LIVE_SECRET_VAR = "BINANCE_API_SECRET"


def load_dotenv(path: str | os.PathLike[str] = ".env", override: bool = False) -> list[str]:
    """Load ``KEY=VALUE`` lines into ``os.environ``. Returns the names that were set.

    Existing environment variables win unless ``override`` is true. Values are never
    returned or printed.
    """
    file = Path(path)
    if not file.is_file():
        return []
    loaded: list[str] = []
    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not name or not name.replace("_", "").isalnum():
            continue
        if name in os.environ and not override:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def binance_credentials() -> tuple[str, str] | None:
    """Read-only live-account credentials, or ``None`` if not configured."""
    key = os.getenv(LIVE_KEY_VAR, "").strip()
    secret = os.getenv(LIVE_SECRET_VAR, "").strip()
    if key and secret:
        return key, secret
    return None


def coingecko_api_key() -> str | None:
    """Optional CoinGecko demo API key (raises the free rate limit)."""
    return os.getenv("COINGECKO_API_KEY", "").strip() or None
