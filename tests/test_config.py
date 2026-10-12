"""Construction, validation of settings, masking and derived clients."""

from __future__ import annotations

import asyncio
import logging
import platform

import httpx
import pytest

import polyzon
from conftest import ME_BODY, TOKEN, Script, make_async, make_sync, ok, problem
from polyzon import AsyncPolyzon, AuthenticationError, Polyzon


def test_package_metadata() -> None:
    assert polyzon.__version__ == "1.0.0"
    assert set(polyzon.__all__) <= set(dir(polyzon))
    assert any(isinstance(h, logging.NullHandler) for h in logging.getLogger("polyzon").handlers)


def test_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYZON_API_KEY", f"  {TOKEN}\n")
    script = Script(ok(ME_BODY))
    client = make_sync(script, api_key=None)
    client.me()
    assert script.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_missing_api_key_fails_at_construction() -> None:
    with pytest.raises(ValueError, match="POLYZON_API_KEY"):
        Polyzon()
    with pytest.raises(ValueError, match="POLYZON_API_KEY"):
        AsyncPolyzon(api_key="")


@pytest.mark.parametrize(
    "bad",
    [
        "pzk_ abc",
        "pzk_abc\r\nX-Injected: 1",
        "pzk_abc\n",
        "pzk_\x00abc",
        "pzk_абв",
        "pzk_\tabc",
        "pzk_\x7fabc",
        "p" * 513,
    ],
)
def test_api_key_rejects_unsafe_values_without_echoing_them(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        Polyzon(api_key=bad)
    assert bad not in str(caught.value)
    assert "abc" not in str(caught.value)


def test_api_key_accepts_any_visible_ascii_shape() -> None:
    Polyzon(api_key="x" * 512).close()
    Polyzon(api_key="not-a-pzk-token!~").close()


def test_api_key_type_is_checked() -> None:
    with pytest.raises(TypeError):
        Polyzon(api_key=b"pzk_bytes")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.polyzon.org", "https://api.polyzon.org"),
        ("https://api.polyzon.org/", "https://api.polyzon.org"),
        ("https://gateway.example.com/polyzon/", "https://gateway.example.com/polyzon"),
        ("https://api.polyzon.org:8443", "https://api.polyzon.org:8443"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1:9", "http://127.0.0.1:9"),
        ("http://[::1]:9/", "http://[::1]:9"),
    ],
)
def test_base_url_accepted(url: str, expected: str) -> None:
    script = Script(ok(ME_BODY))
    client = make_sync(script, base_url=url)
    client.me()
    assert str(script.requests[0].url) == f"{expected}/v1/me"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.polyzon.org",
        "http://10.0.0.1:8080",
        "http://localhost.evil.com",
        "ftp://api.polyzon.org",
        "api.polyzon.org",
        "https://user:pass@api.polyzon.org",
        "https://token@api.polyzon.org",
        "https://api.polyzon.org/?x=1",
        "https://api.polyzon.org?",
        "https://api.polyzon.org/#frag",
        "https://api.polyzon.org:99999",
        "https://api.polyzon.org:port",
        "https://[::1",
        "https://",
        "https:///v1",
        "https://api.polyzon.org /x",
        "https://api.polyzon.org\n",
        "https://апи.рф",
        "",
    ],
)
def test_base_url_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        Polyzon(api_key=TOKEN, base_url=url)


def test_rejected_base_url_is_not_echoed() -> None:
    with pytest.raises(ValueError) as caught:
        Polyzon(api_key=TOKEN, base_url="https://user:hunter2@api.polyzon.org")
    assert "hunter2" not in str(caught.value)


def test_base_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYZON_BASE_URL", "http://localhost:5000/")
    script = Script(ok(ME_BODY))
    make_sync(script).me()
    assert str(script.requests[0].url) == "http://localhost:5000/v1/me"
    monkeypatch.setenv("POLYZON_BASE_URL", "http://evil.example.com")
    with pytest.raises(ValueError, match="base_url"):
        Polyzon(api_key=TOKEN)


def test_base_url_type_is_checked() -> None:
    with pytest.raises(TypeError):
        Polyzon(api_key=TOKEN, base_url=42)  # type: ignore[arg-type]


def test_timeout_settings() -> None:
    script = Script(ok(ME_BODY))
    make_sync(script).me()
    assert script.requests[0].extensions["timeout"] == {
        "connect": 10.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    script = Script(ok(ME_BODY))
    make_sync(script, timeout=2).me()
    assert script.requests[0].extensions["timeout"]["connect"] == 2.0
    custom = httpx.Timeout(5.0, read=60.0)
    script = Script(ok(ME_BODY))
    make_sync(script, timeout=custom).me()
    assert script.requests[0].extensions["timeout"]["read"] == 60.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_timeout_must_be_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        Polyzon(api_key=TOKEN, timeout=bad)


@pytest.mark.parametrize("bad", [None, "30", True])
def test_timeout_type_is_checked(bad: object) -> None:
    with pytest.raises(TypeError, match="timeout"):
        Polyzon(api_key=TOKEN, timeout=bad)  # type: ignore[arg-type]


def test_max_retries_and_connections_are_validated() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        Polyzon(api_key=TOKEN, max_retries=-1)
    with pytest.raises(TypeError, match="max_retries"):
        Polyzon(api_key=TOKEN, max_retries=True)
    with pytest.raises(ValueError, match="max_connections"):
        Polyzon(api_key=TOKEN, max_connections=0)
    with pytest.raises(TypeError, match="max_connections"):
        AsyncPolyzon(api_key=TOKEN, max_connections=2.5)  # type: ignore[arg-type]


def test_default_user_agent_and_headers() -> None:
    script = Script(ok(ME_BODY))
    make_sync(script).me()
    request = script.requests[0]
    expected = (
        f"polyzon-python/1.0.0 ({platform.python_implementation()} {platform.python_version()}; "
        f"{platform.system().lower()}/{platform.machine().lower()})"
    )
    assert request.headers["user-agent"] == expected
    assert request.headers["accept"] == "application/json"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    # Only transport-level headers besides the three the SDK sets.
    assert set(request.headers.keys()) <= {
        "host",
        "accept",
        "accept-encoding",
        "connection",
        "user-agent",
        "authorization",
    }


def test_user_agent_suffix() -> None:
    script = Script(ok(ME_BODY))
    make_sync(script, user_agent="my-bot/2.1 (+https://example.com)").me()
    assert script.requests[0].headers["user-agent"].endswith(") my-bot/2.1 (+https://example.com)")


@pytest.mark.parametrize("bad", ["", " lead", "trail ", "bad\r\nX-Evil: 1", "тест", "x" * 257])
def test_user_agent_suffix_must_be_header_safe(bad: str) -> None:
    with pytest.raises(ValueError, match="user_agent"):
        Polyzon(api_key=TOKEN, user_agent=bad)


def test_repr_masks_token() -> None:
    client = Polyzon(api_key=TOKEN)
    text = repr(client)
    assert TOKEN not in text
    assert "pzk_Q7xR…" in text
    assert "https://api.polyzon.org" in text
    client.close()
    short = Polyzon(api_key="abcdef")
    assert "'abc…'" in repr(short)
    short.close()
    async_client = AsyncPolyzon(api_key=TOKEN)
    assert repr(async_client).startswith("AsyncPolyzon(")
    assert TOKEN not in repr(async_client)
    asyncio.run(async_client.aclose())


def test_token_never_in_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="polyzon")
    script = Script(problem(401, "invalid_token", "Токен не найден или отозван."))
    client = make_sync(script)
    with pytest.raises(AuthenticationError) as caught:
        client.me()
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
    assert caplog.records
    assert all(TOKEN not in record.getMessage() for record in caplog.records)


def test_http_client_type_is_checked() -> None:
    with pytest.raises(TypeError, match=r"httpx\.Client"):
        Polyzon(api_key=TOKEN, http_client=httpx.AsyncClient())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"httpx\.AsyncClient"):
        AsyncPolyzon(api_key=TOKEN, http_client=httpx.Client())  # type: ignore[arg-type]


def test_http_client_conflicts_with_pool_options() -> None:
    with httpx.Client() as http:
        with pytest.raises(ValueError, match="proxy"):
            Polyzon(api_key=TOKEN, http_client=http, proxy="http://127.0.0.1:3128")
        with pytest.raises(ValueError, match="max_connections"):
            Polyzon(api_key=TOKEN, http_client=http, max_connections=5)
    with pytest.raises(ValueError, match="proxy"):
        AsyncPolyzon(api_key=TOKEN, http_client=httpx.AsyncClient(), proxy="http://127.0.0.1:1")


def test_passed_in_http_client_is_not_closed() -> None:
    http = httpx.Client(transport=httpx.MockTransport(Script(ok(ME_BODY))))
    with Polyzon(api_key=TOKEN, http_client=http) as client:
        client.me()
    assert not http.is_closed
    http.close()


def test_owned_pool_is_closed() -> None:
    with Polyzon(api_key=TOKEN) as client:
        pass
    assert client._http.is_closed


def test_with_options_shares_pool_and_revalidates() -> None:
    script = Script(ok(ME_BODY))
    parent = make_sync(script, max_retries=0)
    child = parent.with_options(api_key="pzk_other", max_retries=5, timeout=3, user_agent="x/1")
    assert child._http is parent._http
    assert child._sleep == parent._sleep
    assert "max_retries=5" in repr(child)
    assert "timeout=3" in repr(child)
    child.me()
    assert script.requests[0].headers["authorization"] == "Bearer pzk_other"
    assert script.requests[0].headers["user-agent"].endswith(" x/1")
    parent.me()
    assert script.requests[1].headers["authorization"] == f"Bearer {TOKEN}"
    assert script.requests[1].headers["user-agent"].endswith(")")
    same = parent.with_options()
    assert repr(same) == repr(parent)
    with pytest.raises(ValueError, match="base_url"):
        parent.with_options(base_url="http://example.com")
    with pytest.raises(ValueError, match="api_key"):
        parent.with_options(api_key="has space")


def test_with_options_child_does_not_close_shared_pool() -> None:
    with Polyzon(api_key=TOKEN) as parent:
        with parent.with_options(max_retries=0) as child:
            assert child._http is parent._http
        assert not parent._http.is_closed
    assert parent._http.is_closed


def test_async_with_options_and_context_manager() -> None:
    script = Script(ok(ME_BODY))

    async def main() -> None:
        async with make_async(script) as parent:
            child = parent.with_options(base_url="http://localhost:1234")
            await child.me()
            await child.aclose()
            assert not parent._http.is_closed
            assert str(script.requests[0].url) == "http://localhost:1234/v1/me"

    asyncio.run(main())

    async def owned() -> AsyncPolyzon:
        async with AsyncPolyzon(api_key=TOKEN) as client:
            return client

    assert asyncio.run(owned())._http.is_closed


@pytest.mark.parametrize(
    "bad", ["ftp://user:hunter2@proxy:21", "http://user:hunter2@proxy:port", "user:hunter2"]
)
def test_invalid_proxy_is_rejected_without_echoing_it(bad: str) -> None:
    with pytest.raises(ValueError, match="proxy") as caught:
        Polyzon(api_key=TOKEN, proxy=bad)
    assert "hunter2" not in str(caught.value)
    with pytest.raises(TypeError, match="proxy"):
        AsyncPolyzon(api_key=TOKEN, proxy=3128)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "proxy",
    ["http://user:p%40ss-hunter2@127.0.0.1:3128", httpx.Proxy("http://user:p%40ss-hunter2@h:1")],
)
def test_proxy_password_is_redacted(proxy: str | httpx.Proxy) -> None:
    with Polyzon(api_key=TOKEN, proxy=proxy) as client:
        assert client._options.redact("boom p@ss-hunter2 boom") == "boom *** boom"
        assert "hunter2" not in repr(client)
    with Polyzon(api_key=TOKEN, proxy="socks5://127.0.0.1:1080") as client:
        assert client._options.proxy_secret is None
