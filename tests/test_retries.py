"""Retry decisions: which failures are retried, delays, Retry-After cap, logging."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from conftest import ME_BODY, Script, ok, problem
from polyzon import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    InvalidResponseError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
)
from polyzon._base import parse_retry_after, retry_delay

Make = Callable[..., Any]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_retried_with_backoff(make: Make, status: int) -> None:
    script = Script(problem(status, "unavailable"), problem(status, "unavailable"), ok(ME_BODY))
    me = make(script).me()
    assert me.account.id == "0f8c2a4e-…"
    assert len(script.requests) == 3
    assert 0.25 <= script.sleeps[0] <= 0.5
    assert 0.5 <= script.sleeps[1] <= 1.0


def test_retry_after_is_honoured(make: Make) -> None:
    script = Script(problem(503, "unavailable", headers={"retry-after": "5"}), ok(ME_BODY))
    make(script).me()
    assert script.sleeps == [5.0]


def test_rate_limited_is_retried(make: Make) -> None:
    script = Script(problem(429, "rate_limited", headers={"retry-after": "12"}), ok(ME_BODY))
    make(script).me()
    assert script.sleeps == [12.0]


def test_retry_after_above_cap_raises_immediately(make: Make) -> None:
    script = Script(problem(429, "rate_limited", headers={"retry-after": "61"}), ok(ME_BODY))
    with pytest.raises(RateLimitError) as caught:
        make(script).me()
    assert caught.value.retry_after == 61
    assert script.sleeps == []
    assert len(script.requests) == 1


def test_retry_after_at_cap_is_waited(make: Make) -> None:
    script = Script(problem(503, "unavailable", headers={"retry-after": "60"}), ok(ME_BODY))
    make(script).me()
    assert script.sleeps == [60.0]


def test_quota_exceeded_is_never_retried(make: Make) -> None:
    script = Script(problem(429, "quota_exceeded", headers={"retry-after": "1"}), ok(ME_BODY))
    with pytest.raises(QuotaExceededError):
        make(script).search("x")
    assert len(script.requests) == 1
    assert script.sleeps == []


def test_html_429_without_code_is_not_retried(make: Make) -> None:
    script = Script(httpx.Response(429, text="slow down"), ok(ME_BODY))
    with pytest.raises(RateLimitError):
        make(script).me()
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (problem(400, "bad_request"), BadRequestError),
        (problem(404, "not_found"), NotFoundError),
        (problem(501, "not_implemented"), InternalServerError),
        (httpx.Response(200, text="nope"), InvalidResponseError),
    ],
)
def test_other_failures_are_not_retried(make: Make, response: httpx.Response, error: type) -> None:
    script = Script(response, ok(ME_BODY))
    with pytest.raises(error):
        make(script).me()
    assert len(script.requests) == 1


def test_connection_errors_and_timeouts_are_retried(make: Make) -> None:
    script = Script(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), ok(ME_BODY))
    make(script).me()
    assert len(script.requests) == 3
    assert len(script.sleeps) == 2


def test_retries_exhausted_raise_last_error(make: Make) -> None:
    script = Script(httpx.ConnectError("refused"), httpx.ReadTimeout("still slow"))
    with pytest.raises(APITimeoutError, match="still slow"):
        make(script, max_retries=3).me()
    assert len(script.requests) == 4
    assert len(script.sleeps) == 3
    assert 1.0 <= script.sleeps[2] <= 2.0


def test_max_retries_zero_disables_retries(make: Make) -> None:
    script = Script(httpx.ConnectError("refused"), ok(ME_BODY))
    with pytest.raises(APIConnectionError):
        make(script, max_retries=0).me()
    assert len(script.requests) == 1


def test_retry_after_http_date(make: Make) -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    headers = {"retry-after": format_datetime(when, usegmt=True)}
    script = Script(problem(503, "unavailable", headers=headers), ok(ME_BODY))
    make(script).me()
    assert 25 <= script.sleeps[0] <= 31


def test_retry_attempts_are_logged_without_secrets(
    make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="polyzon")
    script = Script(problem(503, "unavailable", headers={"retry-after": "2"}), ok(ME_BODY))
    make(script).search("кот", page=2)
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("GET /v1/search?q=%D0%BA%D0%BE%D1%82&page=2 -> 503 (attempt 1)")
    assert "failed with 503 (attempt 1), retrying in 2.00 s" in messages[1]
    assert messages[2].endswith("-> 200 (attempt 2)")
    assert all("Bearer" not in message for message in messages)


def test_backoff_is_capped_at_eight_seconds() -> None:
    error = APIConnectionError("x")
    delays = [retry_delay(error, attempt, 100) for attempt in range(10)]
    assert all(delay is not None and delay <= 8.0 for delay in delays)
    assert delays[9] is not None
    assert delays[9] >= 4.0
    assert retry_delay(error, 5, 5) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("5", 5.0),
        (" 7 ", 7.0),
        ("0.5", 0.5),
        ("-3", 0.0),
        ("nan", None),
        ("inf", None),
        ("soon", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),
        ("Wed, 21 Oct 2015 07:28:00", 0.0),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected
