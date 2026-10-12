"""Real sockets: local API server, timeouts, explicit and environment proxies."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any, ClassVar

import pytest

from conftest import ME_BODY, TOKEN, port_of, serve
from polyzon import APIConnectionError, APITimeoutError, AsyncPolyzon, Polyzon


class Recorder(BaseHTTPRequestHandler):
    """Answers every GET with the /v1/me example and records what it saw."""

    seen: ClassVar[list[dict[str, Any]]] = []
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.seen.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
        body = json.dumps(ME_BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", "real-1")
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self) -> None:
        self.seen.append({"method": "CONNECT", "path": self.path, "headers": dict(self.headers)})
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()


def recorder() -> type[Recorder]:
    class Seen(Recorder):
        pass

    Seen.seen = []
    return Seen


def test_real_server_round_trip() -> None:
    handler = recorder()
    with serve(handler) as server:
        base = f"http://127.0.0.1:{port_of(server)}"
        with Polyzon(api_key=TOKEN, base_url=base) as client:
            me = client.me()
            client.me()
    assert me.account.display_name == "Алексей"
    assert me.meta.request_id == "real-1"
    assert handler.seen[0]["path"] == "/v1/me"
    assert handler.seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert len(handler.seen) == 2


def test_real_server_round_trip_async() -> None:
    handler = recorder()

    async def main(base: str) -> str:
        async with AsyncPolyzon(api_key=TOKEN, base_url=base) as client:
            results = await asyncio.gather(client.me(), client.me(), client.me())
        return results[0].token.name

    with serve(handler) as server:
        assert asyncio.run(main(f"http://127.0.0.1:{port_of(server)}")) == "Телеграм-бот"
    assert len(handler.seen) == 3


def test_connection_refused() -> None:
    with serve(recorder()) as server:
        port = port_of(server)
    with (
        Polyzon(api_key=TOKEN, base_url=f"http://127.0.0.1:{port}", max_retries=0) as client,
        pytest.raises(APIConnectionError),
    ):
        client.me()


def test_timeout() -> None:
    release = threading.Event()

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            release.wait(5)

    with serve(Slow) as server:
        base = f"http://127.0.0.1:{port_of(server)}"
        started = time.monotonic()
        with (
            Polyzon(api_key=TOKEN, base_url=base, timeout=0.2, max_retries=0) as client,
            pytest.raises(APITimeoutError),
        ):
            client.me()
        assert time.monotonic() - started < 3
        release.set()


def test_total_deadline_against_a_dripping_body() -> None:
    release = threading.Event()

    class Drip(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    if release.wait(0.05):
                        return
            except OSError:
                return

    with serve(Drip) as server:
        base = f"http://127.0.0.1:{port_of(server)}"
        started = time.monotonic()
        with (
            Polyzon(api_key=TOKEN, base_url=base, timeout=0.4, max_retries=0) as client,
            pytest.raises(APITimeoutError, match="longer than the timeout"),
        ):
            client.me()
        assert time.monotonic() - started < 3
        release.set()


def test_explicit_proxy_receives_absolute_form_with_auth() -> None:
    proxy = recorder()
    with serve(proxy) as server:
        proxy_url = f"http://user:p%40ss@127.0.0.1:{port_of(server)}"
        with Polyzon(api_key=TOKEN, base_url="http://127.0.0.1:9", proxy=proxy_url) as client:
            me = client.me()
    assert me.token.prefix == "pzk_Q7xR2mVb"
    assert proxy.seen[0]["path"] == "http://127.0.0.1:9/v1/me"
    expected = base64.b64encode(b"user:p@ss").decode()
    assert proxy.seen[0]["headers"]["Proxy-Authorization"] == f"Basic {expected}"
    assert TOKEN not in repr(client)
    assert "p@ss" not in repr(client)


def test_https_target_is_tunnelled_and_token_never_reaches_proxy() -> None:
    proxy = recorder()
    with serve(proxy) as server:
        proxy_url = f"http://user:s3cret-pass@127.0.0.1:{port_of(server)}"
        with (
            Polyzon(api_key=TOKEN, proxy=proxy_url, max_retries=0) as client,
            pytest.raises(APIConnectionError) as caught,
        ):
            client.me()
    request = proxy.seen[0]
    assert request["method"] == "CONNECT"
    assert request["path"] == "api.polyzon.org:443"
    assert "Proxy-Authorization" in request["headers"]
    assert "Authorization" not in request["headers"]
    assert all(TOKEN not in str(value) for value in request["headers"].values())
    assert "s3cret-pass" not in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_async_explicit_proxy() -> None:
    proxy = recorder()

    async def main(proxy_url: str) -> None:
        async with AsyncPolyzon(
            api_key=TOKEN, base_url="http://127.0.0.1:9", proxy=proxy_url
        ) as client:
            await client.me()

    with serve(proxy) as server:
        asyncio.run(main(f"http://127.0.0.1:{port_of(server)}"))
    assert proxy.seen[0]["path"] == "http://127.0.0.1:9/v1/me"


def test_environment_proxy_is_used_for_remote_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = recorder()
    with serve(proxy) as server:
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port_of(server)}")
        with (
            Polyzon(api_key=TOKEN, base_url="https://127.0.0.2:1", max_retries=0) as client,
            pytest.raises(APIConnectionError),
        ):
            client.me()
    assert proxy.seen[0]["method"] == "CONNECT"
    assert proxy.seen[0]["path"] == "127.0.0.2:1"


def test_trust_env_false_ignores_environment_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = recorder()
    with serve(proxy) as server:
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port_of(server)}")
        with (
            Polyzon(
                api_key=TOKEN,
                base_url="https://127.0.0.2:1",
                trust_env=False,
                max_retries=0,
                timeout=3,
            ) as client,
            pytest.raises(APIConnectionError),
        ):
            client.me()
    assert proxy.seen == []


def test_environment_proxy_never_applies_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy, api = recorder(), recorder()
    with serve(proxy) as proxy_server, serve(api) as api_server:
        proxy_url = f"http://127.0.0.1:{port_of(proxy_server)}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("ALL_PROXY", proxy_url)
        for host in ("127.0.0.1", "localhost"):
            with Polyzon(api_key=TOKEN, base_url=f"http://{host}:{port_of(api_server)}") as client:
                client.me()
    assert proxy.seen == []
    assert [entry["path"] for entry in api.seen] == ["/v1/me", "/v1/me"]
