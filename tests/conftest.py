"""Shared fixtures: a scripted mock transport, both clients behind one facade, local servers."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from polyzon import AsyncPolyzon, Polyzon

TOKEN = "pzk_Q7xR2mVb" + "k" * 35

ITEM: dict[str, Any] = {
    "source": "thingiverse",
    "id": "tv-1234567",
    "title": "Дракон с подвижными сочленениями (пример)",
    "url": "https://www.thingiverse.com/thing:1234567",
    "thumbnail": "https://cdn.thingiverse.com/assets/dragon.jpg",
    "creator": "example_maker",
    "license": "cc-by-nc",
    "price": {"type": "free", "value": 0, "currency": None, "display": "Бесплатно"},
    "formats": ["stl"],
    "tags": ["дракон", "игрушка"],
    "stats": {"likes": 51234, "comments": 870, "makes": 3120, "collects": 61000},
    "publishedAt": "2021-01-17T10:21:00Z",
}

SEARCH_BODY: dict[str, Any] = {
    "query": "дракон",
    "correctedQuery": None,
    "didYouMean": None,
    "total": 1873,
    "totalRelation": "eq",
    "page": 1,
    "size": 20,
    "hasMore": True,
    "partial": False,
    "items": [ITEM],
}

MODEL_BODY: dict[str, Any] = {
    **ITEM,
    "description": "Дракон печатается целиком, без поддержек…",
    "images": ["https://cdn.thingiverse.com/assets/dragon.jpg"],
    "creatorUrl": "https://www.thingiverse.com/example_maker",
    "license": {"id": "cc-by-nc", "raw": "Creative Commons - Attribution - Non-Commercial"},
    "category": "Toys & Games",
    "stats": {
        "likes": 51234,
        "comments": 870,
        "makes": 3120,
        "collects": 61000,
        "downloads": 410000,
        "views": 990000,
    },
    "updatedAt": "2021-02-02T08:00:00Z",
    "copies": [{"source": "printables", "id": "pr-123456"}],
}

ME_BODY: dict[str, Any] = {
    "account": {
        "id": "0f8c2a4e-…",
        "displayName": "Алексей",
        "createdAt": "2026-03-14T10:00:00.000Z",
    },
    "token": {
        "id": "7d0e2f8a-…",
        "name": "Телеграм-бот",
        "prefix": "pzk_Q7xR2mVb",
        "createdAt": "2026-10-09T08:15:00.000Z",
    },
}

LIMITS_BODY: dict[str, Any] = {
    "perMinute": {"limit": 20, "used": 1, "remaining": 19, "resetAt": 1791579180},
    "daily": {"limit": 200, "used": 37, "remaining": 163, "resetAt": 1791590400},
}

RATE_HEADERS = {
    "x-ratelimit-limit": "200",
    "x-ratelimit-remaining": "163",
    "x-ratelimit-reset": "1791590400",
    "x-request-id": "req-42",
}


def ok(body: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers)


def problem(
    status: int,
    code: str,
    detail: str = "Подробности.",
    headers: dict[str, str] | None = None,
    **extra: object,
) -> httpx.Response:
    body = {
        "type": "about:blank",
        "title": "Error",
        "status": status,
        "code": code,
        "detail": detail,
    }
    return httpx.Response(
        status,
        json={**body, **extra},
        headers={"content-type": "application/problem+json; charset=utf-8", **(headers or {})},
    )


Step = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


class Script:
    """MockTransport handler: answers with the next step (the last one repeats)."""

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return httpx.Response(step.status_code, headers=step.headers, content=step.content)
        return step(request)


def make_sync(script: Script, **kwargs: Any) -> Polyzon:
    kwargs.setdefault("api_key", TOKEN)
    client = Polyzon(http_client=httpx.Client(transport=httpx.MockTransport(script)), **kwargs)
    client._sleep = script.sleeps.append
    return client


def make_async(script: Script, **kwargs: Any) -> AsyncPolyzon:
    kwargs.setdefault("api_key", TOKEN)
    client = AsyncPolyzon(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(script)), **kwargs
    )

    async def record(seconds: float) -> None:
        script.sleeps.append(seconds)

    client._sleep = record
    return client


class AsyncFacade:
    """Drives an AsyncPolyzon from sync test code, so one test covers both clients."""

    def __init__(self, client: AsyncPolyzon, loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop

    def run(self, awaitable: Awaitable[Any]) -> Any:
        async def call() -> Any:
            try:
                return await awaitable
            finally:
                for _ in range(3):  # let async-generator finalizers scheduled by httpx run
                    await asyncio.sleep(0)

        return self.loop.run_until_complete(call())

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.client, name)
        if name == "search_all":

            def drain(*args: Any, **kwargs: Any) -> Iterator[Any]:
                iterator = attr(*args, **kwargs)

                async def collect() -> list[Any]:
                    return [item async for item in iterator]

                return iter(self.run(collect()))

            return drain
        if inspect.iscoroutinefunction(attr):
            return lambda *args, **kwargs: self.run(attr(*args, **kwargs))
        return attr


@pytest.fixture(params=["sync", "async"])
def make(request: pytest.FixtureRequest) -> Iterator[Callable[..., Any]]:
    """Factory building a sync client or an async one behind :class:`AsyncFacade`."""
    loop = asyncio.new_event_loop()

    def factory(script: Script, **kwargs: Any) -> Any:
        if request.param == "sync":
            return make_sync(script, **kwargs)
        return AsyncFacade(make_async(script, **kwargs), loop)

    yield factory
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("POLYZON_API_KEY", "POLYZON_BASE_URL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


@contextmanager
def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[ThreadingHTTPServer]:
    """Run a real HTTP server on a free loopback port in a background thread."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def port_of(server: ThreadingHTTPServer) -> int:
    return int(server.server_address[1])


def raw(status: int, body: bytes, headers: dict[str, str] | None = None) -> Step:
    """A step answering with ``body`` exactly as given (e.g. still gzip-compressed)."""
    return lambda request: httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))
