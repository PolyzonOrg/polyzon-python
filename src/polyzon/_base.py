"""Request building, validation, retry decisions and response parsing.

Shared by the sync and async clients so their behaviour cannot drift: the clients
only differ in how they send bytes, read them and sleep.
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import random
import re
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ._exceptions import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    GoneError,
    InternalServerError,
    InvalidResponseError,
    NotFoundError,
    PermissionDeniedError,
    PolyzonError,
    QuotaExceededError,
    RateLimitError,
)
from ._models import ModelRef, RateLimit, ResponseMeta, SearchPage, _opt_str
from ._version import __version__

logger = logging.getLogger("polyzon")

DEFAULT_BASE_URL = "https://api.polyzon.org"
DEFAULT_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_CONNECTIONS = 20
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_RETRY_AFTER = 60.0
MAX_PAGE = 100
"""The server rejects ``page`` above 100 with 400 (``query-builder.ts`` MAX_PAGE)."""

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,512}")
_UA_SUFFIX_RE = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]{0,254}[\x21-\x7e])?")
_SOURCE_RE = re.compile(r"[a-z0-9]{1,32}")
_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,255}")
_RETRY_STATUSES = frozenset({500, 502, 503, 504})

Json = dict[str, Any]


def default_user_agent() -> str:
    """``polyzon-python/<version> (<runtime> <version>; <os>/<arch>)``."""
    runtime = f"{platform.python_implementation()} {platform.python_version()}"
    system = platform.system().lower() or "unknown"
    machine = platform.machine().lower() or "unknown"
    return f"polyzon-python/{__version__} ({runtime}; {system}/{machine})"


def mask(api_key: str) -> str:
    """Show at most the first 8 characters of a token (fewer for short ones)."""
    return api_key[: min(8, len(api_key) // 2)] + "…"


def _check_int(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _check_str(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    return value


def _check_api_key(api_key: object) -> str:
    if not isinstance(api_key, str):
        raise TypeError(f"api_key must be a str, got {type(api_key).__name__}")
    if not api_key:
        raise ValueError(
            "No API key: pass api_key=... or set the POLYZON_API_KEY environment variable. "
            "Create a token at https://polyzon.org/account/"
        )
    # The message never echoes the value: it is a secret.
    if not _TOKEN_RE.fullmatch(api_key):
        raise ValueError(
            "api_key must be 1-512 visible ASCII characters (no spaces, line breaks or "
            "control characters)"
        )
    return api_key


def _check_base_url(base_url: object) -> str:
    url = _check_str("base_url", base_url)
    # The value is not echoed: a rejected URL may carry credentials.
    error = ValueError(
        "base_url must be an https:// URL (http:// only for localhost, 127.0.0.1 or [::1]) "
        "without credentials, query or fragment"
    )
    if any(ch in url for ch in "?#@") or any(ord(ch) <= 0x20 or ord(ch) >= 0x7F for ch in url):
        raise error
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise error from None
    host = parts.hostname or ""
    if not host or parts.scheme not in ("https", "http"):
        raise error
    if parts.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise error
    return url.rstrip("/")


def _check_timeout(timeout: object) -> tuple[httpx.Timeout, float | None]:
    if isinstance(timeout, httpx.Timeout):
        return timeout, None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f"timeout must be a number of seconds or httpx.Timeout, got {timeout!r}")
    if not (math.isfinite(timeout) and timeout > 0):
        raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
    seconds = float(timeout)
    return httpx.Timeout(seconds, connect=min(seconds, CONNECT_TIMEOUT)), seconds


def _check_user_agent(suffix: object) -> str:
    text = _check_str("user_agent", suffix)
    if not _UA_SUFFIX_RE.fullmatch(text):
        raise ValueError(
            "user_agent must be 1-256 printable ASCII characters without leading or trailing spaces"
        )
    return text


class Options:
    """Validated, immutable per-client settings (the connection pool lives elsewhere)."""

    __slots__ = (
        "api_key",
        "base_url",
        "deadline",
        "max_retries",
        "proxy_secret",
        "timeout",
        "timeout_arg",
        "ua_suffix",
        "user_agent",
    )

    def __init__(
        self,
        *,
        api_key: object,
        base_url: object,
        timeout: object,
        max_retries: object,
        user_agent: object,
        proxy_secret: str | None,
    ) -> None:
        """Validate every setting; raises ``ValueError`` / ``TypeError``."""
        self.api_key = _check_api_key(api_key)
        self.base_url = _check_base_url(base_url)
        self.timeout, self.deadline = _check_timeout(timeout)
        self.timeout_arg: float | httpx.Timeout = (
            self.timeout if self.deadline is None else self.deadline
        )
        self.max_retries = _check_int("max_retries", max_retries, 0)
        self.ua_suffix = None if user_agent is None else _check_user_agent(user_agent)
        self.user_agent = default_user_agent() + (f" {self.ua_suffix}" if self.ua_suffix else "")
        self.proxy_secret = proxy_secret

    @classmethod
    def resolve(
        cls,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: float | httpx.Timeout,
        max_retries: int,
        user_agent: str | None,
        proxy: str | httpx.Proxy | None,
    ) -> Options:
        """Fill unset values from the environment and defaults, then validate."""
        if api_key is None:
            api_key = os.environ.get("POLYZON_API_KEY", "").strip()
        if base_url is None:
            base_url = os.environ.get("POLYZON_BASE_URL") or DEFAULT_BASE_URL
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
            proxy_secret=_proxy_secret(proxy),
        )

    def derive(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: float | httpx.Timeout | None,
        max_retries: int | None,
        user_agent: str | None,
    ) -> Options:
        """A copy with the given (non-``None``) settings replaced and re-validated."""
        return Options(
            api_key=self.api_key if api_key is None else api_key,
            base_url=self.base_url if base_url is None else base_url,
            timeout=self.timeout_arg if timeout is None else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
            user_agent=self.ua_suffix if user_agent is None else user_agent,
            proxy_secret=self.proxy_secret,
        )

    def describe(self, cls_name: str) -> str:
        """The client ``repr``: the token is masked."""
        return (
            f"{cls_name}(base_url={self.base_url!r}, api_key={mask(self.api_key)!r}, "
            f"timeout={self.timeout_arg!r}, max_retries={self.max_retries})"
        )

    def redact(self, text: str) -> str:
        """Remove the token and the proxy password from text that may reach users."""
        for secret in (self.api_key, self.proxy_secret):
            if secret:
                text = text.replace(secret, "***")
        return text


def parse_proxy(proxy: object) -> httpx.Proxy | None:
    """Validate the ``proxy`` option; the message never echoes it (it may hold a password)."""
    if proxy is None or isinstance(proxy, httpx.Proxy):
        return proxy
    if not isinstance(proxy, str):
        raise TypeError(f"proxy must be a URL string or httpx.Proxy, got {type(proxy).__name__}")
    try:
        return httpx.Proxy(proxy)
    except (httpx.InvalidURL, ValueError):
        raise ValueError(
            "proxy must be an http://, https:// or socks5:// URL, e.g. http://user:pass@host:3128"
        ) from None


def _proxy_secret(proxy: object) -> str | None:
    parsed = parse_proxy(proxy)
    return parsed.auth[1] if parsed is not None and parsed.auth else None


def http_client_kwargs(
    *, proxy: str | httpx.Proxy | None, trust_env: bool, max_connections: int | None
) -> dict[str, Any]:
    """Keyword arguments for the ``httpx.Client`` / ``httpx.AsyncClient`` the SDK owns."""
    connections = _check_int(
        "max_connections",
        DEFAULT_MAX_CONNECTIONS if max_connections is None else max_connections,
        1,
    )
    kwargs: dict[str, Any] = {
        "limits": httpx.Limits(max_connections=connections, max_keepalive_connections=connections),
        "trust_env": trust_env,
        "follow_redirects": False,
    }
    if proxy is not None:
        kwargs["proxy"] = parse_proxy(proxy)
    elif trust_env:
        # Environment proxies never apply to loopback targets: route them directly.
        kwargs["mounts"] = dict.fromkeys(("all://localhost", "all://127.0.0.1", "all://[::1]"))
    return kwargs


# --- request building ------------------------------------------------------------


def _csv(name: str, values: object) -> str | None:
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError(f"{name} must be a list of strings, e.g. [{'stl'!r}], got {values!r}")
    items = list(values)
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{name} items must be str, got {type(item).__name__}")
        if not item or "," in item:
            raise ValueError(f"{name} items must be non-empty and contain no commas, got {item!r}")
    return ",".join(items) or None


def search_params(
    q: object,
    *,
    page: object,
    size: object,
    sort: object,
    free: object,
    sources: object,
    formats: object,
    license: object,
    lang: object,
) -> list[tuple[str, str]]:
    """Validate search arguments and build the query; unset values are omitted."""
    query = _check_str("q", q)
    if not query.strip():
        raise ValueError("q must not be empty")
    params = [("q", query)]
    if page is not None:
        params.append(("page", str(_check_int("page", page, 1))))
    if size is not None:
        params.append(("size", str(_check_int("size", size, 1))))
    if sort is not None:
        params.append(("sort", _check_str("sort", sort)))
    if free is not None and not isinstance(free, bool):
        raise TypeError(f"free must be a bool, got {type(free).__name__}")
    if free:
        params.append(("free", "1"))
    for name, values in (("sources", sources), ("formats", formats), ("license", license)):
        joined = _csv(name, values)
        if joined:
            params.append((name, joined))
    if lang is not None:
        params.append(("lang", _check_str("lang", lang)))
    return params


def lang_params(lang: object) -> list[tuple[str, str]]:
    """The optional ``lang`` query of the model endpoint."""
    return [] if lang is None else [("lang", _check_str("lang", lang))]


def model_path(source: object, model_id: object) -> str:
    """``/v1/models/{source}/{id}`` with both segments validated and percent-encoded."""
    source = _check_str("source", source)
    model_id = _check_str("id", model_id)
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"source must match [a-z0-9]{{1,32}}, got {source!r}")
    if not _ID_RE.fullmatch(model_id) or model_id in (".", ".."):
        raise ValueError(
            f"id must match [A-Za-z0-9._-]{{1,255}} and not be a dot segment, got {model_id!r}"
        )
    return f"/v1/models/{quote(source, safe='')}/{quote(model_id, safe='')}"


def check_refs(refs: object) -> list[ModelRef]:
    """Validate every ref of a batch before any request is sent."""
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Iterable):
        raise TypeError("refs must be an iterable of ModelRef or (source, id) tuples")
    checked = []
    for ref in refs:
        if not isinstance(ref, tuple) or len(ref) != 2:
            raise TypeError(f"each ref must be a ModelRef or a (source, id) tuple, got {ref!r}")
        model_path(*ref)
        checked.append(ModelRef(*ref))
    return checked


def check_concurrency(concurrency: object) -> int:
    """Batch concurrency must be an int >= 1."""
    return _check_int("concurrency", concurrency, 1)


def check_page(page: object) -> int:
    """Start page of ``search_all``; ``None`` means 1."""
    return 1 if page is None else _check_int("page", page, 1)


def next_page(result: SearchPage, current: int) -> int | None:
    """The page ``search_all`` fetches after ``current``, or ``None`` to stop."""
    if not result.has_more or not result.items or current >= MAX_PAGE:
        return None
    return current + 1


def is_batch_fatal(error: PolyzonError) -> bool:
    """Errors that make every remaining call of a batch fail the same way."""
    return isinstance(error, (AuthenticationError, PermissionDeniedError, QuotaExceededError))


def build_request(
    http: httpx.Client | httpx.AsyncClient,
    options: Options,
    path: str,
    params: Sequence[tuple[str, str]],
) -> httpx.Request:
    """The GET request: exactly Authorization, Accept and User-Agent are set by the SDK."""
    return http.build_request(
        "GET",
        options.base_url + path,
        params=list(params),
        headers={
            "Authorization": f"Bearer {options.api_key}",
            "Accept": "application/json",
            "User-Agent": options.user_agent,
        },
        timeout=options.timeout,
    )


# --- response handling -------------------------------------------------------------


class BodyReader:
    """Collects a streamed body; stops at 8 MiB and enforces the per-attempt deadline."""

    __slots__ = ("_chunks", "_deadline", "_size", "too_large")

    def __init__(self, timeout: float | None) -> None:
        """Start the clock for an attempt; ``timeout`` ``None`` disables the deadline."""
        self._chunks: list[bytes] = []
        self._size = 0
        self._deadline = None if timeout is None else time.monotonic() + timeout
        self.too_large = False

    def add(self, chunk: bytes) -> bool:
        """Keep ``chunk``; ``False`` means the cap was hit and reading must stop."""
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise APITimeoutError("Request timed out: the response took longer than the timeout")
        self._size += len(chunk)
        if self._size > MAX_BODY_BYTES:
            self.too_large = True
            self._chunks.clear()
            return False
        self._chunks.append(chunk)
        return True

    @property
    def body(self) -> bytes:
        """Everything read so far."""
        return b"".join(self._chunks)


def transport_error(error: httpx.HTTPError, options: Options) -> PolyzonError:
    """Translate an httpx failure; secrets are scrubbed from the message."""
    detail = options.redact(str(error) or type(error).__name__)
    if isinstance(error, httpx.TimeoutException):
        return APITimeoutError(f"Request timed out: {detail}")
    if isinstance(error, httpx.DecodingError):
        return InvalidResponseError(f"Could not decode the response body: {detail}")
    return APIConnectionError(f"Connection error: {detail}")


def _decode(body: bytes) -> Json | None:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name, "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


def _rate_limit(headers: httpx.Headers) -> RateLimit | None:
    limit = _header_int(headers, "x-ratelimit-limit")
    remaining = _header_int(headers, "x-ratelimit-remaining")
    reset_at = _header_int(headers, "x-ratelimit-reset")
    if limit is None or remaining is None or reset_at is None:
        return None
    return RateLimit(limit=limit, remaining=remaining, reset_at=reset_at)


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds from now: delta-seconds or an HTTP-date."""
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _error_class(status: int, code: str | None) -> type[APIError]:
    if status == 429:
        return QuotaExceededError if code == "quota_exceeded" else RateLimitError
    if status >= 500:
        return InternalServerError
    return {
        400: BadRequestError,
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        410: GoneError,
    }.get(status, APIError)


def parse_response(response: httpx.Response, reader: BodyReader) -> tuple[Json, ResponseMeta]:
    """The decoded body of a 2xx answer, or the matching :class:`PolyzonError`."""
    status = response.status_code
    headers = response.headers
    request_id = headers.get("x-request-id") or None
    data = None if reader.too_large else _decode(reader.body)
    if 200 <= status < 300:
        suffix = f" (request id {request_id})" if request_id else ""
        if reader.too_large:
            raise InvalidResponseError(f"{status}: response body exceeds 8 MiB{suffix}")
        if data is None:
            kind = headers.get("content-type", "no content type").split(";")[0]
            raise InvalidResponseError(f"{status}: expected a JSON object, got {kind!r}{suffix}")
        return data, ResponseMeta(request_id=request_id, rate_limit=_rate_limit(headers))
    problem = data or {}
    code = _opt_str(problem.get("code"))
    raise _error_class(status, code)(
        status_code=status,
        code=code,
        title=_opt_str(problem.get("title")),
        detail=_opt_str(problem.get("detail")) or f"HTTP {status}",
        param=_opt_str(problem.get("param")),
        request_id=request_id,
        retry_after=parse_retry_after(headers.get("retry-after")),
        headers=headers,
        body=data,
    )


def retry_delay(error: PolyzonError, attempt: int, max_retries: int) -> float | None:
    """Seconds to wait before retrying after ``error``, or ``None`` to raise it.

    ``attempt`` counts from 0 for the first request.
    """
    if attempt >= max_retries:
        return None
    if isinstance(error, APIError):
        retryable = error.status_code in _RETRY_STATUSES or (
            error.status_code == 429 and error.code == "rate_limited"
        )
        if not retryable:
            return None
        if error.retry_after is not None:
            return error.retry_after if error.retry_after <= MAX_RETRY_AFTER else None
    elif not isinstance(error, APIConnectionError):
        return None
    return min(8.0, 0.5 * 2.0**attempt) * random.uniform(0.5, 1.0)  # noqa: S311 - jitter, not crypto


def target(request: httpx.Request) -> str:
    """Path and query for logs (never headers, never the host's credentials)."""
    return request.url.raw_path.decode("ascii")


def log_response(request: httpx.Request, status: int, attempt: int) -> None:
    """Debug-log one HTTP answer."""
    logger.debug("GET %s -> %d (attempt %d)", target(request), status, attempt + 1)


def log_retry(request: httpx.Request, error: PolyzonError, attempt: int, delay: float) -> None:
    """Debug-log that a failed attempt will be retried."""
    what = error.status_code if isinstance(error, APIError) else type(error).__name__
    logger.debug(
        "GET %s failed with %s (attempt %d), retrying in %.2f s",
        target(request),
        what,
        attempt + 1,
        delay,
    )
