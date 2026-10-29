"""Local web dashboard: portfolio, trending meme coins with risk checks, pump/dump watch.

Security model
--------------
* Binds to loopback only (127.0.0.1 / ::1). There is no login, so it refuses to listen on
  any other address.
* Rejects requests whose ``Host`` header is not a loopback name (blocks DNS-rebinding, where a
  malicious website tricks your browser into talking to this local server).
* GET only reads. POST changes local state only, needs a per-run session token, a same-origin
  request and JSON; nothing can place a real order. The portfolio client is read-only.
* A strict Content-Security-Policy and no inline scripts; the front end builds the page with
  ``textContent`` because token names and news headlines are attacker-controlled text.
"""
from __future__ import annotations

import hmac
import json
import math
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import api, memecoins, news, portfolio, pumpwatch, rugcheck, settings
from . import binance_public as bp
from .cache import TTLCache
from .desk import Desk
from .investigate import NotFound
from .net import HttpError, get_json, get_text
from .store import Store

WEB_DIR = Path(__file__).parent / "web"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}  # accepted in the Host header
BIND_HOSTS = {"127.0.0.1", "localhost"}  # addresses the server may listen on
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_QUERY_OK = re.compile(r"^[\w\s$.\-:/]{1,100}$")

# How long each panel is cached, to stay far below the free-tier rate limits of the data sources.
TTL = {"portfolio": 45, "memes": 180, "pumps": 120, "news": 600, "token": 120}


def jsonable(value: Any) -> Any:
    """Recursively convert dataclasses and replace NaN/inf (invalid JSON) with ``None``."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class Services:
    """Everything the HTTP handler needs. All network access is injectable for tests."""

    def __init__(
        self,
        getter: Callable[..., Any] | None = None,
        text_getter: Callable[..., str] | None = None,
        credentials: tuple[str, str] | None = None,
        allow_unverified_key: bool = False,
        coingecko_key: str | None = None,
        desk: Desk | None = None,
    ):
        self.getter = getter or get_json
        self.text_getter = text_getter or get_text
        self.cache = TTLCache()
        self.coingecko_key = coingecko_key
        self.allow_unverified_key = allow_unverified_key
        self._desk = desk
        self.session_token = secrets.token_urlsafe(32)
        self.trade_cache: dict[str, dict] = {}
        self.trade_cache_time = time.monotonic()
        self.client = (
            portfolio.BinanceReadOnlyClient(credentials[0], credentials[1], getter=self.getter)
            if credentials else None
        )

    @property
    def desk(self) -> Desk:
        if self._desk is None:  # tests and ad-hoc use: in-memory state, no gateway pacing
            self._desk = Desk(self.getter, self.text_getter, Store(":memory:"), use_gateway=False)
        return self._desk

    def status(self) -> dict:
        return {"portfolio_configured": self.client is not None}

    def portfolio(self) -> dict:
        if self.client is None:
            return {"configured": False}

        def build() -> dict:
            if time.monotonic() - self.trade_cache_time > 900:  # refresh cost basis every 15 min
                self.trade_cache.clear()
                self.trade_cache_time = time.monotonic()
            snapshot = portfolio.build_snapshot(
                self.client, allow_unverified_key=self.allow_unverified_key,
                trade_cache=self.trade_cache,
            )
            snapshot["configured"] = True
            return snapshot

        return self.cache.get("portfolio", TTL["portfolio"], build)

    def _binance_bases(self) -> set[str]:
        try:
            prices = bp.ticker_prices(self.getter)
        except HttpError:
            return set()
        return {b for s in prices if (b := bp.split_usdt(s))}

    def memes(self) -> dict:
        def build() -> dict:
            tokens, warnings = memecoins.fetch_dex_candidates(self.getter, max_tokens=60)
            tokens = tokens[:30]
            reports, risk_warnings = rugcheck.check_tokens(tokens, self.getter)
            warnings += risk_warnings
            rows = []
            for token in tokens:
                address = token.address if token.chain == "solana" else token.address.lower()
                report = reports[(token.chain, address)]
                row = token.to_dict()
                row["risk"] = report.to_dict()
                row["verdict"] = rugcheck.verdict(token.heat_score, report.level)
                rows.append(row)
            # Hot-but-safe first, then hot-but-risky, avoid last.
            order = {"WATCH": 0, "HIGH_RISK": 1, "SKIP": 2, "AVOID": 3}
            rows.sort(key=lambda r: (order[r["verdict"]], -r["heat_score"]))

            established: list[dict] = []
            trending: list[dict] = []
            try:
                established = memecoins.fetch_coingecko_memes(self.getter, self.coingecko_key)
            except HttpError as exc:
                warnings.append(f"CoinGecko meme list unavailable: {exc}")
            try:
                trending = memecoins.fetch_coingecko_trending(self.getter, self.coingecko_key)
            except HttpError as exc:
                warnings.append(f"CoinGecko trending unavailable: {exc}")
            bases = self._binance_bases()
            memecoins.mark_binance_listed(established, bases)
            memecoins.mark_binance_listed(trending, bases)
            return {
                "tokens": rows, "established": established, "trending": trending,
                "warnings": warnings,
                "note": (
                    "Heat measures attention and momentum only. Risk checks cannot prove a token "
                    "is safe. Tokens that are not on Binance need a wallet and a DEX to buy; "
                    "this project never trades for you."
                ),
            }

        return self.cache.get("memes", TTL["memes"], build)

    def pumps(self) -> dict:
        def build() -> dict:
            meme_bases: set[str] | None = None
            try:
                meme_bases = set(pumpwatch.FALLBACK_MEME_BASES)
                meme_bases |= {
                    row["symbol"] for row in
                    memecoins.fetch_coingecko_memes(self.getter, self.coingecko_key, per_page=100)
                    if row.get("symbol")
                }
            except HttpError:
                pass
            rows, warnings = pumpwatch.scan_pumps(self.getter, meme_bases=meme_bases)
            return {"rows": [r.to_dict() for r in rows], "warnings": warnings}

        return self.cache.get("pumps", TTL["pumps"], build)

    def news(self, query: str) -> dict:
        key = f"news:{query.lower()}"

        def build() -> dict:
            headlines = news.fetch_headlines(query, self.text_getter)
            return {"query": query, "headlines": [h.to_dict() for h in headlines],
                    "summary": news.summarize(headlines)}

        return self.cache.get(key, TTL["news"], build)

    def token_check(self, query: str) -> dict:
        key = f"token:{query.lower()}"

        def build() -> dict:
            tokens = memecoins.search_tokens(query, self.getter, limit=5)
            reports, warnings = rugcheck.check_tokens(tokens, self.getter)
            rows = []
            for token in tokens:
                address = token.address if token.chain == "solana" else token.address.lower()
                report = reports[(token.chain, address)]
                row = token.to_dict()
                row["risk"] = report.to_dict()
                row["verdict"] = rugcheck.verdict(token.heat_score, report.level)
                rows.append(row)
            return {"query": query, "tokens": rows, "warnings": warnings}

        return self.cache.get(key, TTL["token"], build)


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


MAX_BODY = 16 * 1024
WEB_FILES = ("index.html", "app.css", "app.js", "charts.js", "legacy.js", "views.js", "views2.js", "boot.js")


def make_handler(services: Services, files: dict[str, tuple[bytes, str]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MarketDesk"
        sys_version = ""
        timeout = 15  # a client that stalls mid-request must not hold a thread forever

        def log_message(self, format: str, *args: Any) -> None:
            return  # keep the terminal quiet; errors are reported to the browser

        def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(jsonable(payload), allow_nan=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if _hostname(self.headers.get("Host", "")) not in LOOPBACK_HOSTS:
                self._json(HTTPStatus.FORBIDDEN, {"error": "Only loopback hosts are allowed."})
                return False
            return True

        def _same_site(self) -> bool:
            if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                self._json(HTTPStatus.FORBIDDEN, {"error": "Cross-site requests are not allowed."})
                return False
            return True

        def _fail(self, exc: Exception) -> None:
            if isinstance(exc, portfolio.UnsafeKeyError):
                self._json(HTTPStatus.FORBIDDEN, {"error": str(exc), "unsafe_key": True})
            elif isinstance(exc, NotFound):
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            elif isinstance(exc, (ValueError, KeyError)):
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc).strip("'") or "Bad request."})
            elif isinstance(exc, HttpError):
                self._json(HTTPStatus.BAD_GATEWAY, {"error": f"A data source failed: {exc}"})
            else:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected error. See the terminal."})
                raise exc

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path in {"/", "/index.html"}:
                path = "/index.html"
            if path.lstrip("/") in files and not path.startswith("/api/"):
                body, content_type = files[path.lstrip("/")]
                self._send(HTTPStatus.OK, body, content_type)
                return
            if not path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            if not self._same_site():
                return
            try:
                if path == "/api/status":
                    payload = services.status()
                elif path == "/api/portfolio":
                    payload = services.portfolio()
                elif path == "/api/memes":
                    payload = services.memes()
                elif path == "/api/pumps":
                    payload = services.pumps()
                elif path in {"/api/news", "/api/token"}:
                    text = (query.get("q") or [""])[0].strip()
                    if not _QUERY_OK.match(text):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Enter 1-100 letters, numbers or . - $ : /"})
                        return
                    payload = services.news(text) if path == "/api/news" else services.token_check(text)
                else:
                    payload = api.route_get(services, path, query)
                    if payload is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                        return
            except Exception as exc:  # noqa: BLE001  mapped to a status in _fail, unknown ones re-raised
                self._fail(exc)
                return
            self._json(HTTPStatus.OK, payload)

        def do_POST(self) -> None:
            if not self._host_ok() or not self._same_site():
                return
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            origin = self.headers.get("Origin")
            if origin and _hostname(urllib.parse.urlparse(origin).netloc) not in LOOPBACK_HOSTS:
                self._json(HTTPStatus.FORBIDDEN, {"error": "Foreign origin."})
                return
            token = self.headers.get("X-Desk-Token", "")
            if not hmac.compare_digest(token.encode(), services.session_token.encode()):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Missing or wrong session token. Reload the page."})
                return
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send application/json."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Body too large or length missing."})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object."})
                    return
                payload = api.route_post(services, parsed.path, body)
                if payload is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                    return
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
                return
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
                return
            self._json(HTTPStatus.OK, payload)

    return Handler


def load_web_files() -> dict[str, tuple[bytes, str]]:
    files = {}
    for name in WEB_FILES:
        file = WEB_DIR / name
        if not file.exists():
            continue
        files[name] = (file.read_bytes(), CONTENT_TYPES[file.suffix])
    return files


def create_server(
    services: Services, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    if host not in BIND_HOSTS:
        raise SystemExit(
            f"Refusing to listen on {host!r}. The dashboard has no login and can show your "
            "portfolio, so it only runs on 127.0.0.1 (or localhost)."
        )
    server = ThreadingHTTPServer((host, port), make_handler(services, load_web_files()))
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, allow_unverified_key: bool = False) -> None:
    settings.load_dotenv(".env")
    credentials = settings.binance_credentials()
    desk = Desk(get_json, get_text)
    services = Services(
        desk=desk,
        credentials=credentials,
        allow_unverified_key=allow_unverified_key,
        coingecko_key=settings.coingecko_api_key(),
    )
    server = create_server(services, host, port)
    if desk.store.get_setting("watcher_enabled"):
        desk.start_watcher()
    print(f"Market Desk running at http://{host}:{server.server_address[1]}  (Ctrl+C to stop)")
    if credentials is None:
        print("Portfolio tab is off: set BINANCE_API_KEY and BINANCE_API_SECRET (read-only key) in .env")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
