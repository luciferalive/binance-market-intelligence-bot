"""Narrative radar: which themes are attracting trading activity right now.

Tokens are classified into themes by keyword matching on their name and ticker, then market
activity is aggregated per theme. Everything here is derived from market data the scanner
already has. **Social mention counts are not available** without a social data provider, so the
output says so rather than inventing them.

A theme gaining attention does not make every token under it a good investment. The summary
text only describes what the numbers show.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .pools import PoolRow

THEMES: dict[str, tuple[str, ...]] = {
    "AI and agents": ("ai", "gpt", "agent", "agents", "bot", "robot", "neural", "grok", "llm", "sentient", "claude", "gemini", "openai", "deepseek", "agi"),
    "Politics": ("trump", "maga", "biden", "harris", "kamala", "election", "vote", "democrat", "republican", "elon", "musk", "president", "congress"),
    "Dogs": ("dog", "doge", "shib", "inu", "puppy", "wif", "floki", "bonk", "woof", "pup"),
    "Cats": ("cat", "kitty", "meow", "popcat", "mew", "kitten"),
    "Frogs and pepe": ("pepe", "frog", "kek", "toad", "ribbit"),
    "Other animals": ("monkey", "ape", "bear", "bull", "hamster", "penguin", "duck", "fish", "whale", "panda", "bird", "fox", "goat", "pig", "rat", "wolf", "lion", "tiger"),
    "Space": ("moon", "mars", "rocket", "space", "alien", "galaxy", "cosmos", "star"),
    "Food": ("pizza", "burger", "taco", "cheese", "sushi", "banana", "coffee", "beer", "cake", "chicken"),
    "Gaming": ("game", "gamer", "play", "pixel", "quest", "arcade", "gaming"),
}
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
NEW_LAUNCH_MINUTES = 360


def tokenize(*texts: str) -> list[str]:
    words: list[str] = []
    for text in texts:
        words += [w.lower() for w in _WORDS.findall(text or "")]
    return words


def classify(name: str, symbol: str) -> str:
    """Theme for a token, or ``Other``. Short keywords must match a whole word ("ai" != "tail")."""
    words = tokenize(name, symbol)
    joined = "".join(words)
    for theme, keywords in THEMES.items():
        for keyword in keywords:
            if len(keyword) <= 3:
                if keyword in words:
                    return theme
            elif keyword in words or keyword in joined:
                return theme
    return "Other"


def aggregate(rows: Iterable[PoolRow], previous: dict[str, dict] | None = None) -> list[dict]:
    """Per-theme market activity, largest 1-hour volume first. ``previous`` adds growth figures."""
    buckets: dict[str, dict] = {}
    for row in rows:
        theme = classify(row.name, row.symbol)
        b = buckets.setdefault(theme, {"theme": theme, "tokens": 0, "new_launches": 0, "vol_h1": 0.0,
                                       "vol_h24": 0.0, "buyers_h1": 0, "chg_sum": 0.0, "chg_n": 0, "top": []})
        b["tokens"] += 1
        if row.age_minutes is not None and row.age_minutes < NEW_LAUNCH_MINUTES:
            b["new_launches"] += 1
        b["vol_h1"] += row.vol_h1 or 0.0
        b["vol_h24"] += row.vol_h24 or 0.0
        b["buyers_h1"] += row.buyers_h1 or 0
        if row.chg_h1 is not None:
            b["chg_sum"] += row.chg_h1
            b["chg_n"] += 1
        b["top"].append({"symbol": row.symbol, "key": row.key, "vol_h1": row.vol_h1 or 0.0})
    out = []
    for b in buckets.values():
        b["avg_change_h1"] = (b["chg_sum"] / b["chg_n"]) if b["chg_n"] else None
        b["top"] = sorted(b["top"], key=lambda t: t["vol_h1"], reverse=True)[:5]
        for k in ("chg_sum", "chg_n"):
            del b[k]
        prev = (previous or {}).get(b["theme"])
        for field, name in (("vol_h1", "volume"), ("tokens", "tokens"), ("new_launches", "launches")):
            old = prev.get(field) if prev else None
            b[f"{name}_growth_pct"] = ((b[field] - old) / old * 100) if old else None
        b["assessment"] = assess(b)
        out.append(b)
    out.sort(key=lambda b: b["vol_h1"], reverse=True)
    return out


def assess(b: dict) -> dict:
    """Plain description of what the numbers show, with an action that is never 'buy'."""
    notes = []
    fresh_share = b["new_launches"] / b["tokens"] if b["tokens"] else 0.0
    if b["tokens"] < 3:
        notes.append("Only a few tokens, so this is not a trend yet.")
    if fresh_share >= 0.6:
        notes.append("Most tokens here launched in the last 6 hours: treat with caution.")
    growth = b.get("volume_growth_pct")
    if growth is not None and growth > 50:
        notes.append(f"Hourly volume is up {growth:.0f}% since the last reading.")
    if b["avg_change_h1"] is not None and b["avg_change_h1"] > 100:
        notes.append("Average price is already up sharply: chasing risk.")
    if not notes:
        notes.append("Activity is present but unremarkable.")
    elevated = (growth or 0) > 50 or (b["tokens"] >= 8 and b["vol_h1"] > 250_000)
    return {
        "market_reaction": "ELEVATED" if elevated else "NORMAL",
        "action": "MONITOR" if elevated else "NO ACTION",
        "notes": notes,
        "social": "Not connected: mention and account growth need a social data provider.",
    }
