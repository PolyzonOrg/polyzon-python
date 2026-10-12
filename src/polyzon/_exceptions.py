"""Exceptions raised by the Polyzon SDK.

Everything the SDK raises for a failed call derives from :class:`PolyzonError`.
Invalid arguments (a bad model id, a malformed base URL, a token with spaces)
raise the standard :class:`ValueError` / :class:`TypeError` before any request.
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "GoneError",
    "InternalServerError",
    "InvalidResponseError",
    "NotFoundError",
    "PermissionDeniedError",
    "PolyzonError",
    "QuotaExceededError",
    "RateLimitError",
]


class PolyzonError(Exception):
    """Base class of every error the SDK raises for a failed API call."""


class APIConnectionError(PolyzonError):
    """The request never got an HTTP answer: DNS, TLS, proxy, refused or reset connection."""


class APITimeoutError(APIConnectionError):
    """The request did not complete within the configured timeout."""


class InvalidResponseError(PolyzonError):
    """A successful (2xx) answer whose body is not a JSON object or exceeds 8 MiB."""


class APIError(PolyzonError):
    """The API answered with an HTTP error (any non-2xx status, including 3xx redirects).

    Attributes:
        status_code: HTTP status of the answer.
        code: Stable machine-readable error code (``"invalid_token"``,
            ``"rate_limited"``, ...), or ``None`` when the body was not a problem
            document (for example an HTML page from an edge proxy).
        title: Problem title (``"Too Many Requests"``), or ``None``.
        detail: Human-readable explanation; ``"HTTP <status>"`` when the body had none.
        param: Name of the offending query parameter for ``bad_request``, else ``None``.
        request_id: Value of the ``X-Request-ID`` response header, or ``None``.
        retry_after: Seconds the server asked to wait (``Retry-After``), or ``None``.
        headers: All response headers.
        body: The parsed problem document, or ``None`` when the body was not a JSON object.
    """

    status_code: int
    code: str | None
    title: str | None
    detail: str
    param: str | None
    request_id: str | None
    retry_after: float | None
    headers: httpx.Headers
    body: dict[str, Any] | None

    def __init__(
        self,
        *,
        status_code: int,
        code: str | None,
        title: str | None,
        detail: str,
        param: str | None,
        request_id: str | None,
        retry_after: float | None,
        headers: httpx.Headers,
        body: dict[str, Any] | None,
    ) -> None:
        """Build the error from a parsed HTTP answer; the SDK does this for you."""
        self.status_code = status_code
        self.code = code
        self.title = title
        self.detail = detail
        self.param = param
        self.request_id = request_id
        self.retry_after = retry_after
        self.headers = headers
        self.body = body
        message = f"{status_code} {code}: {detail}" if code else f"{status_code}: {detail}"
        if request_id:
            message += f" (request id {request_id})"
        super().__init__(message)


class BadRequestError(APIError):
    """400: a parameter is invalid (``bad_request``, see :attr:`param`) or ``page_too_deep``."""


class AuthenticationError(APIError):
    """401: the token is missing (``unauthorized``), wrong or revoked (``invalid_token``)."""


class PermissionDeniedError(APIError):
    """403: the account must accept the current terms at https://polyzon.org/account/."""


class NotFoundError(APIError):
    """404: no such model (``not_found``)."""


class GoneError(APIError):
    """410: the model was removed at the right holder's request; delete your copy too."""


class RateLimitError(APIError):
    """429: too many requests per minute (``rate_limited``); see :attr:`retry_after`."""


class QuotaExceededError(RateLimitError):
    """429 ``quota_exceeded``: the daily quota is used up until 00:00 UTC. Never retried."""


class InternalServerError(APIError):
    """5xx: the API or the catalogue behind it is temporarily unavailable."""
