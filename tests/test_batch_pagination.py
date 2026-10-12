"""get_models (order, bounded concurrency, short-circuit) and search_all pagination."""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from conftest import ITEM, MODEL_BODY, SEARCH_BODY, TOKEN, Script, make_sync, ok, problem
from polyzon import (
    AsyncPolyzon,
    AuthenticationError,
    ModelRef,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
)

Make = Callable[..., Any]


def model_for(request: httpx.Request) -> httpx.Response:
    _, _, _, source, model_id = request.url.path.split("/")
    return ok({**MODEL_BODY, "source": source, "id": model_id})


def test_get_models_preserves_input_order(make: Make) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(random.uniform(0, 0.01))  # noqa: S311
        return model_for(request)

    refs = [ModelRef("thingiverse", f"tv-{n}") for n in range(20)]
    mixed = [ref if n % 2 else (ref.source, ref.id) for n, ref in enumerate(refs)]
    results = make(Script(slow)).get_models(iter(mixed), concurrency=5, lang="en")
    assert [result.ref for result in results] == refs
    assert [result.model.id for result in results if result.model] == [ref.id for ref in refs]
    assert all(result.ok and result.error is None for result in results)


def test_get_models_sends_lang(make: Make) -> None:
    script = Script(model_for)
    make(script).get_models([("printables", "pr-1")], lang="en")
    assert script.requests[0].url.params["lang"] == "en"


def test_get_models_collects_per_item_errors(make: Make) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tv-2"):
            return problem(404, "not_found")
        return model_for(request)

    results = make(Script(answer)).get_models(
        [("thingiverse", "tv-1"), ("thingiverse", "tv-2"), ("thingiverse", "tv-3")],
        concurrency=1,
    )
    assert [result.ok for result in results] == [True, False, True]
    assert isinstance(results[1].error, NotFoundError)
    assert results[1].model is None


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (problem(401, "invalid_token"), AuthenticationError),
        (problem(403, "account_action_required"), PermissionDeniedError),
        (problem(429, "quota_exceeded", headers={"retry-after": "3600"}), QuotaExceededError),
    ],
)
def test_get_models_short_circuits_on_fatal_errors(
    make: Make, response: httpx.Response, error_type: type
) -> None:
    script = Script(response)
    refs = [("thingiverse", f"tv-{n}") for n in range(5)]
    results = make(script).get_models(refs, concurrency=1)
    assert len(script.requests) == 1
    assert all(isinstance(result.error, error_type) for result in results)
    assert all(result.error is results[0].error for result in results)
    assert [result.ref for result in results] == [ModelRef(*ref) for ref in refs]


def test_get_models_short_circuit_with_parallel_workers() -> None:
    script = Script(problem(401, "invalid_token"))
    results = make_sync(script).get_models([("a", str(n)) for n in range(50)], concurrency=4)
    assert len(script.requests) <= 4
    assert all(isinstance(result.error, AuthenticationError) for result in results)


def test_get_models_bounded_concurrency_threads() -> None:
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def tracked(request: httpx.Request) -> httpx.Response:
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return model_for(request)

    results = make_sync(Script(tracked)).get_models(
        [("thingiverse", f"tv-{n}") for n in range(16)], concurrency=3
    )
    assert len(results) == 16
    assert 2 <= state["max"] <= 3


def test_get_models_bounded_concurrency_async() -> None:
    state = {"now": 0, "max": 0, "calls": 0}

    async def tracked(request: httpx.Request) -> httpx.Response:
        state["now"] += 1
        state["calls"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return model_for(request)

    async def main() -> list[Any]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(tracked))
        async with AsyncPolyzon(api_key=TOKEN, http_client=http) as client:
            results = await client.get_models(
                [ModelRef("thingiverse", f"tv-{n}") for n in range(12)], concurrency=3
            )
        await http.aclose()
        return results

    results = asyncio.run(main())
    assert [result.model.id for result in results] == [f"tv-{n}" for n in range(12)]
    assert state["max"] == 3
    assert state["calls"] == 12


@pytest.mark.parametrize(
    ("refs", "concurrency", "error"),
    [
        ([("thingiverse", "tv-1")], 0, ValueError),
        ([("thingiverse", "tv-1")], True, TypeError),
        ([("thingiverse", "tv-1")], 2.0, TypeError),
        ("thingiverse", 4, TypeError),
        (5, 4, TypeError),
        ([("thingiverse",)], 4, TypeError),
        (["thingiverse/tv-1"], 4, TypeError),
        ([("thingiverse", "tv-1"), ("Bad", "tv-2")], 4, ValueError),
        ([("thingiverse", "..")], 4, ValueError),
    ],
)
def test_get_models_validates_everything_first(
    make: Make, refs: Any, concurrency: Any, error: type
) -> None:
    script = Script(model_for)
    with pytest.raises(error):
        make(script).get_models(refs, concurrency=concurrency)
    assert script.requests == []


def test_get_models_empty(make: Make) -> None:
    script = Script(model_for)
    assert make(script).get_models([]) == []
    assert script.requests == []


# --- pagination -----------------------------------------------------------------


def page_body(page: int, *, has_more: bool, count: int = 2) -> dict[str, Any]:
    items = [{**ITEM, "id": f"tv-{page}-{n}"} for n in range(count)]
    return {**SEARCH_BODY, "page": page, "size": count, "hasMore": has_more, "items": items}


def test_search_all_walks_until_has_more_is_false(make: Make) -> None:
    script = Script(
        ok(page_body(1, has_more=True)),
        ok(page_body(2, has_more=True)),
        ok(page_body(3, has_more=False)),
    )
    items = list(make(script).search_all("дракон", size=2, free=True, sources=["thingiverse"]))
    assert [item.id for item in items] == [
        "tv-1-0",
        "tv-1-1",
        "tv-2-0",
        "tv-2-1",
        "tv-3-0",
        "tv-3-1",
    ]
    assert [r.url.params["page"] for r in script.requests] == ["1", "2", "3"]
    for request in script.requests:
        assert request.url.params["q"] == "дракон"
        assert request.url.params["size"] == "2"
        assert request.url.params["free"] == "1"
        assert request.url.params["sources"] == "thingiverse"


def test_search_all_starts_at_given_page(make: Make) -> None:
    script = Script(ok(page_body(7, has_more=False)))
    items = list(make(script).search_all("x", page=7))
    assert len(items) == 2
    assert script.requests[0].url.params["page"] == "7"


def test_search_all_stops_on_empty_page(make: Make) -> None:
    script = Script(ok(page_body(1, has_more=True)), ok(page_body(2, has_more=True, count=0)))
    assert len(list(make(script).search_all("x"))) == 2
    assert len(script.requests) == 2


def test_search_all_stops_at_page_100(make: Make) -> None:
    script = Script(ok(page_body(100, has_more=True)), ok(page_body(101, has_more=True)))
    assert len(list(make(script).search_all("x", page=99))) == 4
    assert [r.url.params["page"] for r in script.requests] == ["99", "100"]


def test_search_all_invalid_start_page(make: Make) -> None:
    client = make(Script(ok(SEARCH_BODY)))
    with pytest.raises(ValueError):
        client.search_all("x", page=0)
    with pytest.raises(TypeError):
        client.search_all("x", page="1")


def test_search_all_is_lazy_and_raises_during_iteration() -> None:
    script = Script(ok(page_body(1, has_more=True)), problem(429, "quota_exceeded"))
    iterator = make_sync(script).search_all("x")
    assert script.requests == []
    assert next(iterator).id == "tv-1-0"
    assert next(iterator).id == "tv-1-1"
    with pytest.raises(QuotaExceededError):
        next(iterator)
    assert len(script.requests) == 2


def test_async_search_all_is_lazy() -> None:
    from conftest import make_async

    script = Script(ok(page_body(1, has_more=True)), ok(page_body(2, has_more=False)))

    async def main() -> list[str]:
        client = make_async(script)
        iterator = client.search_all("x")
        assert script.requests == []
        ids = []
        async for item in iterator:
            ids.append(item.id)
            if len(ids) == 3:
                break
        return ids

    assert asyncio.run(main()) == ["tv-1-0", "tv-1-1", "tv-2-0"]
    assert len(script.requests) == 2
