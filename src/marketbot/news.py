"""News and catalyst tracker.

Pulls recent headlines for a coin from the Google News RSS feed (no API key) and tags them
with event types such as exchange listings, partnerships, company/institutional involvement,
and negative events like hacks or lawsuits.

The tagging is plain keyword matching. It is a fast way to surface *why* something might be
moving, not a substitute for reading the articles. Anyone can publish a headline, and a
"partnership" headline for a micro-cap is frequently marketing.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

from .net import HttpError, get_text

GOOGLE_NEWS_URL = "https://news.google.com/rss/search"

TAG_PATTERNS: dict[str, tuple[str, ...]] = {
    "listing": ("listing", "lists ", "listed on", "to list", "will list", "adds support", "delist"),
    "partnership": ("partner", "integrat", "collaborat", "teams up", "joins forces"),
    "company_interest": (
        "acquire", "acquisition", "treasury", "etf", "institution", "invests", "investment",
        "backed by", "stake", "fund ", "launches token", "adopts", "accepts",
    ),
    "momentum": ("surge", "soar", "rally", "jump", "record high", "breakout", "skyrocket"),
    "negative": (
        "hack", "exploit", "rug pull", "rugpull", "scam", "fraud", "lawsuit", "sued", "sec charges",
        "investigation", "crash", "plunge", "dump", "stolen", "ban ", "delisted", "outage",
    ),
}
POSITIVE_TAGS = {"listing", "partnership", "company_interest", "momentum"}
_DELIST = re.compile(r"\bdelist", re.IGNORECASE)


@dataclass
class Headline:
    title: str
    source: str
    link: str
    published: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def tag_headline(title: str) -> list[str]:
    text = f" {title.lower()} "
    tags = []
    for tag, needles in TAG_PATTERNS.items():
        if any(needle in text for needle in needles):
            tags.append(tag)
    if _DELIST.search(title):
        # "delist" matched the listing tag; a delisting is bad news, not a listing catalyst.
        tags = [t for t in tags if t != "listing"]
        if "negative" not in tags:
            tags.append("negative")
    return tags


def _safe_link(link: str) -> str:
    return link if link.startswith(("https://", "http://")) else ""


def parse_rss(xml_text: str, max_items: int = 10) -> list[Headline]:
    """Parse an RSS document. Documents with DTDs/entities are rejected (XML bomb defence)."""
    head = xml_text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in xml_text.upper():
        raise ValueError("refusing to parse RSS containing a DOCTYPE or ENTITY declaration")
    root = ET.fromstring(xml_text)
    items: list[Headline] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        if not source and " - " in title:
            source = title.rsplit(" - ", 1)[1]
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        published = (node.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(published).isoformat()
        except (TypeError, ValueError):
            pass
        items.append(Headline(
            title=title[:300],
            source=source[:80],
            link=_safe_link((node.findtext("link") or "").strip()),
            published=published,
            tags=tag_headline(title),
        ))
        if len(items) >= max_items:
            break
    return items


def fetch_headlines(
    query: str, text_getter: Any = None, max_items: int = 10, days: int = 7
) -> list[Headline]:
    """Recent headlines mentioning ``query`` (a coin name or ticker plus 'crypto')."""
    text_getter = text_getter or get_text
    cleaned = re.sub(r"[^\w\s$.-]", " ", query).strip()[:60]
    if not cleaned:
        return []
    params = {"q": f"{cleaned} crypto when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        xml_text = text_getter(GOOGLE_NEWS_URL, params=params)
        return parse_rss(xml_text, max_items=max_items)
    except (ET.ParseError, ValueError) as exc:
        raise HttpError(f"news feed could not be parsed: {exc}") from exc


def summarize(headlines: list[Headline]) -> dict:
    """Counts per tag and a simple catalyst balance for a set of headlines."""
    counts: dict[str, int] = {}
    for headline in headlines:
        for tag in headline.tags:
            counts[tag] = counts.get(tag, 0) + 1
    positive = sum(counts.get(tag, 0) for tag in POSITIVE_TAGS)
    negative = counts.get("negative", 0)
    if negative and negative >= positive:
        tone = "negative"
    elif positive > negative:
        tone = "positive"
    else:
        tone = "neutral"
    return {"total": len(headlines), "tag_counts": counts, "tone": tone}
