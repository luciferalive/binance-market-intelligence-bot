"""Tiny standard-library HTTP helper with retry and backoff.

Every new data module goes through ``get_json`` / ``get_text`` so that:

* there is no extra third-party dependency,
* network access can be replaced with a fake in tests,
* query strings (which carry Binance request signatures) never end up in error text.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = "marketbot/0.2 (+https://github.com/luciferalive/binance-market-intelligence-bot)"
MAX_BYTES = 8 * 1024 * 1024
MAX_RETRY_AFTER = 10.0

Getter = Callable[..., Any]


class HttpError(RuntimeError):
    """Network or HTTP failure. ``url`` never includes the query string."""

    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


def _safe_url(url: str) -> str:
    return url.split("?", 1)[0]


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        value = exc.headers.get("Retry-After") if exc.headers else None
        return min(float(value), MAX_RETRY_AFTER) if value else None
    except (TypeError, ValueError):
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into errors, so credentials in headers never follow a redirect."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def get_text(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    retries: int = 2,
    backoff: float = 1.0,
    urlopen: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    follow_redirects: bool = True,
) -> str:
    """GET ``url`` and return the body as text. Retries 429 / 5xx / network errors."""
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    if urlopen is not None:
        opener = urlopen
    elif follow_redirects:
        opener = urllib.request.urlopen
    else:
        opener = urllib.request.build_opener(_NoRedirect).open
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"}
    request_headers.update(headers or {})
    shown = _safe_url(url)
    last_error: HttpError | None = None

    for attempt in range(retries + 1):
        wait = backoff * (2 ** attempt)
        try:
            request = urllib.request.Request(url, headers=request_headers, method="GET")
            with opener(request, timeout=timeout) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise HttpError("response too large", url=shown)
            return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(300).decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001 - body is best-effort context only
                detail = ""
            last_error = HttpError(
                f"HTTP {exc.code} from {shown}" + (f": {detail}" if detail else ""),
                status=exc.code,
                url=shown,
            )
            if not (exc.code == 429 or exc.code >= 500):
                raise last_error from None
            wait = _retry_after(exc) or wait
        except HttpError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = HttpError(f"network error contacting {shown}: {reason}", url=shown)
        if attempt < retries:
            sleep(wait)
    assert last_error is not None
    raise last_error


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    """GET ``url`` and parse the JSON body."""
    text = get_text(url, params=params, headers=headers, **kwargs)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise HttpError(f"invalid JSON from {_safe_url(url)}", url=_safe_url(url)) from exc


def to_float(value: Any, default: float | None = None) -> float | None:
    """Tolerant number parser: accepts numbers and strings such as ``"$1,234.5"``."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("$", "").replace(",", "")
        if not text:
            return default
        try:
            number = float(text)
        except ValueError:
            return default
    if not math.isfinite(number):
        return default
    return number
