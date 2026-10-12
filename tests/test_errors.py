"""Error mapping, problem parsing, redirects and invalid or oversized bodies."""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest

from conftest import ME_BODY, Script, ok, problem, raw
from polyzon import (
    APIConnectionError,
    APIError,
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

Make = Callable[..., Any]


@pytest.mark.parametrize(
    ("status", "code", "error_type"),
    [
        (400, "bad_request", BadRequestError),
        (400, "page_too_deep", BadRequestError),
        (401, "unauthorized", AuthenticationError),
        (401, "invalid_token", AuthenticationError),
        (403, "account_action_required", PermissionDeniedError),
        (404, "not_found", NotFoundError),
        (410, "gone", GoneError),
        (429, "rate_limited", RateLimitError),
        (429, "quota_exceeded", QuotaExceededError),
        (500, "internal", InternalServerError),
        (503, "unavailable", InternalServerError),
        (504, "anything", InternalServerError),
        (409, "conflict", APIError),
        (418, "teapot", APIError),
    ],
)
def test_status_and_code_mapping(make: Make, status: int, code: str, error_type: type) -> None:
    script = Script(problem(status, code, "Описание.", {"x-request-id": "rid-1"}))
    with pytest.raises(APIError) as caught:
        make(script, max_retries=0).me()
    error = caught.value
    assert type(error) is error_type
    assert isinstance(error, PolyzonError)
    assert error.status_code == status
    assert error.code == code
    assert error.detail == "Описание."
    assert error.title == "Error"
    assert error.request_id == "rid-1"
    assert error.body is not None
    assert error.body["code"] == code
    assert str(error) == f"{status} {code}: Описание. (request id rid-1)"


def test_quota_exceeded_is_a_rate_limit_error(make: Make) -> None:
    headers = {
        "retry-after": "43200",
        "x-ratelimit-limit": "200",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": "1791590400",
    }
    script = Script(
        problem(429, "quota_exceeded", "Суточная квота 200 запросов исчерпана.", headers)
    )
    with pytest.raises(RateLimitError) as caught:
        make(script).search("x")
    assert isinstance(caught.value, QuotaExceededError)
    assert caught.value.retry_after == 43200
    assert caught.value.headers["x-ratelimit-remaining"] == "0"
    assert len(script.requests) == 1


def test_bad_request_param(make: Make) -> None:
    script = Script(problem(400, "bad_request", "size must be between 1 and 50", param="size"))
    with pytest.raises(BadRequestError) as caught:
        make(script).search("x", size=51)
    assert caught.value.param == "size"
    assert str(caught.value) == "400 bad_request: size must be between 1 and 50"


def test_html_error_page_is_mapped_by_status_without_leaking_html(make: Make) -> None:
    html = "<html><body><h1>502 Bad Gateway</h1><script>evil()</script></body></html>"
    script = Script(httpx.Response(502, text=html, headers={"content-type": "text/html"}))
    with pytest.raises(InternalServerError) as caught:
        make(script, max_retries=0).me()
    error = caught.value
    assert error.code is None
    assert error.title is None
    assert error.detail == "HTTP 502"
    assert error.body is None
    assert str(error) == "502: HTTP 502"
    assert "html" not in str(error)


def test_problem_with_wrong_types(make: Make) -> None:
    body = {"code": 5, "detail": ["x"], "title": None, "param": {}}
    script = Script(httpx.Response(404, json=body))
    with pytest.raises(NotFoundError) as caught:
        make(script).get_model("thingiverse", "tv-1")
    assert caught.value.code is None
    assert caught.value.detail == "HTTP 404"
    assert caught.value.param is None
    assert caught.value.body == body


def test_json_array_error_body_is_not_a_problem(make: Make) -> None:
    script = Script(httpx.Response(400, json=["bad"]))
    with pytest.raises(BadRequestError) as caught:
        make(script).me()
    assert caught.value.body is None


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_not_followed(make: Make, status: int) -> None:
    script = Script(
        httpx.Response(status, headers={"location": "https://evil.example.com/steal"}),
        ok(ME_BODY),
    )
    with pytest.raises(APIError) as caught:
        make(script).me()
    assert type(caught.value) is APIError
    assert caught.value.status_code == status
    assert caught.value.headers["location"] == "https://evil.example.com/steal"
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"}),
            "text/html",
        ),
        (httpx.Response(200, json=[1, 2]), "JSON object"),
        (httpx.Response(200, json="string"), "JSON object"),
        (httpx.Response(200, content=b""), "JSON object"),
        (httpx.Response(200, content=b"\xff\xfe{"), "JSON object"),
        (httpx.Response(200, content=b'{"a":'), "JSON object"),
        (httpx.Response(204), "JSON object"),
        (httpx.Response(200, content=b"[" * 100_000 + b"]" * 100_000), "JSON object"),
    ],
)
def test_invalid_success_bodies(make: Make, response: httpx.Response, message: str) -> None:
    with pytest.raises(InvalidResponseError, match=message):
        make(Script(response)).me()


def test_invalid_response_mentions_request_id(make: Make) -> None:
    response = httpx.Response(200, text="nope", headers={"x-request-id": "rid-9"})
    with pytest.raises(InvalidResponseError, match="request id rid-9"):
        make(Script(response)).me()


class Endless(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A body that never ends; counts how many chunks the client pulled."""

    def __init__(self, chunk: bytes = b" " * 1024 * 1024) -> None:
        self.chunk = chunk
        self.pulled = 0

    def __iter__(self) -> Iterator[bytes]:
        while True:
            self.pulled += 1
            yield self.chunk

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            self.pulled += 1
            yield self.chunk


def test_body_cap_aborts_without_reading_the_rest(make: Make) -> None:
    stream = Endless()
    script = Script(lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(InvalidResponseError, match="exceeds 8 MiB"):
        make(script).me()
    assert stream.pulled <= 9


def test_body_cap_on_error_answer_keeps_the_status(make: Make) -> None:
    stream = Endless()
    script = Script(lambda request: httpx.Response(404, stream=stream))
    with pytest.raises(NotFoundError) as caught:
        make(script).me()
    assert caught.value.body is None
    assert stream.pulled <= 9


def test_body_exactly_at_cap_is_read(make: Make) -> None:
    body = b'{"account": {"id": "a"}, "pad": "' + b"x" * (8 * 1024 * 1024 - 40) + b'"}'
    assert len(body) <= 8 * 1024 * 1024
    me = make(Script(httpx.Response(200, content=body))).me()
    assert me.account.id == "a"


def test_body_cap_counts_decompressed_bytes(make: Make) -> None:
    bomb = gzip.compress(b'{"pad": "' + b"0" * (9 * 1024 * 1024) + b'"}')
    assert len(bomb) < 100_000
    with pytest.raises(InvalidResponseError, match="exceeds 8 MiB"):
        make(Script(raw(200, bomb, {"content-encoding": "gzip"}))).me()


def test_gzip_body_is_decoded(make: Make) -> None:
    body = gzip.compress(b'{"account": {"id": "gz"}}')
    me = make(Script(raw(200, body, {"content-encoding": "gzip"}))).me()
    assert me.account.id == "gz"


def test_broken_compression_is_an_invalid_response(make: Make) -> None:
    step = raw(200, b"not gzip", {"content-encoding": "gzip"})
    with pytest.raises(InvalidResponseError, match="decode"):
        make(Script(step), max_retries=0).me()


def test_transport_errors_are_wrapped(make: Make) -> None:
    script = Script(httpx.ConnectError("[Errno 111] Connection refused"))
    with pytest.raises(APIConnectionError, match="Connection refused") as caught:
        make(script, max_retries=0).me()
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_secrets_are_scrubbed_from_transport_errors(make: Make) -> None:
    from conftest import TOKEN

    script = Script(httpx.ProxyError(f"proxy said: {TOKEN}"))
    with pytest.raises(APIConnectionError) as caught:
        make(script, max_retries=0).me()
    assert TOKEN not in str(caught.value)
    assert "***" in str(caught.value)
