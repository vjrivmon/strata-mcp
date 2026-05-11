"""Shared HTTP helpers for the network adapters.

One place for the polite User-Agent (``STRATA_CONTACT_EMAIL`` appended if set —
arXiv / Semantic Scholar ask for a contact), the request timeout
(``STRATA_HTTP_TIMEOUT``, default 30 s), and a small retry-with-backoff GET that
treats 429 / 5xx / timeouts as transient. Adapters wrap :class:`HttpError` in
their own error type (``ArxivError`` / ``PdfError`` / ``S2Error`` / ...).
"""

from __future__ import annotations

import os
import time
from typing import Any

USER_AGENT_BASE = "strata-mcp/0.1 (+https://github.com/vjrivmon/strata-mcp)"
DEFAULT_HTTP_TIMEOUT = 30.0
RETRIES = 3
BACKOFF_BASE = 1.5
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class HttpError(RuntimeError):
    """An HTTP GET failed — after exhausting retries, or because of a network
    error / non-2xx status that ``raise_for_status`` rejected."""


def user_agent() -> str:
    """The HTTP ``User-Agent``, with ``STRATA_CONTACT_EMAIL`` appended if set."""
    email = (os.environ.get("STRATA_CONTACT_EMAIL") or "").strip()
    return f"{USER_AGENT_BASE} (mailto:{email})" if email else USER_AGENT_BASE


def http_timeout() -> float:
    """The per-request timeout in seconds (``STRATA_HTTP_TIMEOUT``, default 30,
    floored at 1)."""
    try:
        return max(1.0, float(os.environ.get("STRATA_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_HTTP_TIMEOUT


def get_bytes(
    url: str,
    params: dict | None = None,
    *,
    timeout: float | None = None,
    retries: int = RETRIES,
    headers: dict | None = None,
) -> bytes:
    """GET ``url`` and return the response body, retrying with exponential
    backoff on timeouts / 429 / 5xx. Extra ``headers`` are merged on top of the
    polite ``User-Agent``. Raises :class:`HttpError` on a hard failure."""
    import httpx

    timeout = http_timeout() if timeout is None else timeout
    retries = max(1, int(retries))
    hdrs = {"User-Agent": user_agent(), **(headers or {})}
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(url, params=params, headers=hdrs)
            if resp.status_code in _RETRYABLE_STATUS:
                raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(BACKOFF_BASE**attempt)
    raise HttpError(f"GET {url} failed after {retries} attempts: {last_exc}")


def get_json(
    url: str,
    params: dict | None = None,
    *,
    timeout: float | None = None,
    retries: int = RETRIES,
    headers: dict | None = None,
) -> Any:
    """GET ``url`` and parse the body as JSON (same retry policy as
    :func:`get_bytes`). Raises :class:`HttpError` on a hard failure, including a
    body that is not valid JSON."""
    import json

    raw = get_bytes(url, params, timeout=timeout, retries=retries, headers=headers)
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HttpError(f"GET {url} returned a non-JSON body: {exc}") from exc
