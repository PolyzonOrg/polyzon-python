"""Every endpoint: request shape, query encoding, path validation and response parsing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from conftest import (
    ITEM,
    LIMITS_BODY,
    ME_BODY,
    MODEL_BODY,
    RATE_HEADERS,
    SEARCH_BODY,
    Script,
    ok,
)
from polyzon import (
    Account,
    ItemStats,
    Limits,
    Me,
    Model,
    ModelLicense,
    ModelRef,
    ModelStats,
    Price,
    QuotaWindow,
    RateLimit,
    ResponseMeta,
    SearchItem,
    SearchPage,
    TokenInfo,
)

Make = Callable[..., Any]

EXPECTED_ITEM = SearchItem(
    source="thingiverse",
    id="tv-1234567",
    title="Дракон с подвижными сочленениями (пример)",
    url="https://www.thingiverse.com/thing:1234567",
    thumbnail="https://cdn.thingiverse.com/assets/dragon.jpg",
    creator="example_maker",
    license="cc-by-nc",
    price=Price(type="free", value=0.0, currency=None, display="Бесплатно"),
    formats=("stl",),
    tags=("дракон", "игрушка"),
    stats=ItemStats(likes=51234, comments=870, makes=3120, collects=61000),
    published_at="2021-01-17T10:21:00Z",
)


def test_search_minimal_request_and_parsing(make: Make) -> None:
    script = Script(ok(SEARCH_BODY, RATE_HEADERS))
    page = make(script).search("дракон")
    request = script.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/search"
    assert request.url.query == b"q=%D0%B4%D1%80%D0%B0%D0%BA%D0%BE%D0%BD"
    assert page == SearchPage(
        query="дракон",
        total=1873,
        total_relation="eq",
        page=1,
        size=20,
        has_more=True,
        partial=False,
        items=(EXPECTED_ITEM,),
    )
    assert page.meta == ResponseMeta(
        request_id="req-42", rate_limit=RateLimit(limit=200, remaining=163, reset_at=1791590400)
    )
    assert page.items[0].ref == ModelRef("thingiverse", "tv-1234567")


def test_search_every_parameter_is_encoded(make: Make) -> None:
    script = Script(ok(SEARCH_BODY))
    make(script).search(
        "кот & dog?=1",
        page=2,
        size=50,
        sort="newest",
        free=True,
        sources=["thingiverse", "printables"],
        formats=("stl", "3mf"),
        license=["cc0", "cc-by"],
        lang="en",
    )
    params = script.requests[0].url.params
    assert list(params.multi_items()) == [
        ("q", "кот & dog?=1"),
        ("page", "2"),
        ("size", "50"),
        ("sort", "newest"),
        ("free", "1"),
        ("sources", "thingiverse,printables"),
        ("formats", "stl,3mf"),
        ("license", "cc0,cc-by"),
        ("lang", "en"),
    ]
    raw = script.requests[0].url.query.decode()
    assert "%26" in raw
    assert "%3F" in raw
    assert "%D0%BA%D0%BE%D1%82" in raw


def test_search_omits_unset_and_false_values(make: Make) -> None:
    script = Script(ok(SEARCH_BODY))
    make(script).search("x", free=False, sources=[], formats=None)
    assert list(script.requests[0].url.params.keys()) == ["q"]


def test_search_accepts_unknown_sources_and_licenses(make: Make) -> None:
    script = Script(ok(SEARCH_BODY))
    make(script).search("x", sources=["brand-new-site"], license=["future-license"])
    assert script.requests[0].url.params["sources"] == "brand-new-site"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"sources": "thingiverse"}, TypeError),
        ({"formats": b"stl"}, TypeError),
        ({"license": 5}, TypeError),
        ({"sources": ["ok", 1]}, TypeError),
        ({"sources": ["a,b"]}, ValueError),
        ({"formats": [""]}, ValueError),
        ({"page": 0}, ValueError),
        ({"page": "2"}, TypeError),
        ({"size": True}, TypeError),
        ({"size": 0}, ValueError),
        ({"free": "yes"}, TypeError),
        ({"sort": 1}, TypeError),
        ({"lang": None, "q": 5}, TypeError),
        ({"q": "   "}, ValueError),
        ({"lang": 7}, TypeError),
    ],
)
def test_search_argument_validation_sends_nothing(
    make: Make, kwargs: dict[str, Any], error: type[Exception]
) -> None:
    script = Script(ok(SEARCH_BODY))
    client = make(script)
    kwargs = dict(kwargs)
    q = kwargs.pop("q", "дракон")
    with pytest.raises(error):
        client.search(q, **kwargs)
    with pytest.raises(error):
        client.search_all(q, **kwargs)
    assert script.requests == []


def test_get_model_request_and_parsing(make: Make) -> None:
    script = Script(ok(MODEL_BODY, {"x-request-id": "req-7"}))
    model = make(script).get_model("thingiverse", "tv-1234567", lang="en")
    request = script.requests[0]
    assert request.url.raw_path == b"/v1/models/thingiverse/tv-1234567?lang=en"
    assert model == Model(
        source="thingiverse",
        id="tv-1234567",
        title="Дракон с подвижными сочленениями (пример)",
        url="https://www.thingiverse.com/thing:1234567",
        description="Дракон печатается целиком, без поддержек…",
        thumbnail="https://cdn.thingiverse.com/assets/dragon.jpg",
        images=("https://cdn.thingiverse.com/assets/dragon.jpg",),
        creator="example_maker",
        creator_url="https://www.thingiverse.com/example_maker",
        category="Toys & Games",
        license=ModelLicense(id="cc-by-nc", raw="Creative Commons - Attribution - Non-Commercial"),
        price=Price(type="free", value=0.0, display="Бесплатно"),
        formats=("stl",),
        tags=("дракон", "игрушка"),
        stats=ModelStats(
            likes=51234, comments=870, makes=3120, collects=61000, downloads=410000, views=990000
        ),
        published_at="2021-01-17T10:21:00Z",
        updated_at="2021-02-02T08:00:00Z",
        copies=(ModelRef("printables", "pr-123456"),),
    )
    assert model.meta.request_id == "req-7"
    assert model.meta.rate_limit is None
    assert model.ref == ("thingiverse", "tv-1234567")


def test_get_model_without_lang_has_no_query(make: Make) -> None:
    script = Script(ok(MODEL_BODY))
    make(script).get_model("3dtoday", "3dt.model_v2-1")
    assert script.requests[0].url.raw_path == b"/v1/models/3dtoday/3dt.model_v2-1"


@pytest.mark.parametrize(
    ("source", "model_id"),
    [
        ("Thingiverse", "tv-1"),
        ("thing-verse", "tv-1"),
        ("", "tv-1"),
        ("a" * 33, "tv-1"),
        ("thingiverse", ""),
        ("thingiverse", "."),
        ("thingiverse", ".."),
        ("thingiverse", "../me"),
        ("thingiverse", "tv/1"),
        ("thingiverse", "tv 1"),
        ("thingiverse", "tv%2F1"),
        ("thingiverse", "tv-1?x=1"),
        ("thingiverse", "tv-1\n"),
        ("thingiverse", "дракон"),
        ("thingiverse", "a" * 256),
    ],
)
def test_get_model_path_validation_sends_nothing(make: Make, source: str, model_id: str) -> None:
    script = Script(ok(MODEL_BODY))
    with pytest.raises(ValueError):
        make(script).get_model(source, model_id)
    assert script.requests == []


def test_get_model_argument_types(make: Make) -> None:
    client = make(Script(ok(MODEL_BODY)))
    with pytest.raises(TypeError):
        client.get_model(1, "tv-1")
    with pytest.raises(TypeError):
        client.get_model("thingiverse", None)
    with pytest.raises(TypeError):
        client.get_model("thingiverse", "tv-1", lang=1)


def test_get_model_long_valid_id(make: Make) -> None:
    script = Script(ok(MODEL_BODY))
    make(script).get_model("a" * 32, "b" * 255)
    assert script.requests[0].url.path == f"/v1/models/{'a' * 32}/{'b' * 255}"


def test_me(make: Make) -> None:
    script = Script(ok(ME_BODY, RATE_HEADERS))
    me = make(script).me()
    assert script.requests[0].url.raw_path == b"/v1/me"
    assert me == Me(
        account=Account(
            id="0f8c2a4e-…", display_name="Алексей", created_at="2026-03-14T10:00:00.000Z"
        ),
        token=TokenInfo(
            id="7d0e2f8a-…",
            name="Телеграм-бот",
            prefix="pzk_Q7xR2mVb",
            created_at="2026-10-09T08:15:00.000Z",
        ),
    )
    assert me.meta.rate_limit == RateLimit(limit=200, remaining=163, reset_at=1791590400)


def test_limits(make: Make) -> None:
    script = Script(ok(LIMITS_BODY))
    limits = make(script).limits()
    assert script.requests[0].url.raw_path == b"/v1/limits"
    assert limits == Limits(
        per_minute=QuotaWindow(limit=20, used=1, remaining=19, reset_at=1791579180),
        daily=QuotaWindow(limit=200, used=37, remaining=163, reset_at=1791590400),
    )


def test_meta_excluded_from_equality_and_repr(make: Make) -> None:
    client = make(Script(ok(LIMITS_BODY, RATE_HEADERS), ok(LIMITS_BODY)))
    first, second = client.limits(), client.limits()
    assert first.meta != second.meta
    assert first == second
    assert hash(first) == hash(second)
    assert "meta" not in repr(first)
    assert "req-42" not in repr(first)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-ratelimit-limit": "200", "x-ratelimit-remaining": "5"},
        {"x-ratelimit-limit": "200", "x-ratelimit-remaining": "-1", "x-ratelimit-reset": "1"},
        {"x-ratelimit-limit": "lots", "x-ratelimit-remaining": "1", "x-ratelimit-reset": "1"},
    ],
)
def test_partial_rate_limit_headers_give_none(make: Make, headers: dict[str, str]) -> None:
    me = make(Script(ok(ME_BODY, headers))).me()
    assert me.meta.rate_limit is None
    assert me.meta.request_id is None


def test_lenient_parsing_of_wrong_types(make: Make) -> None:
    body = {
        "query": 5,
        "correctedQuery": "",
        "didYouMean": "драконы",
        "total": 12.9,
        "totalRelation": "approx",
        "page": "1",
        "size": None,
        "hasMore": "yes",
        "partial": True,
        "unknownField": {"nested": [1, 2]},
        "items": [
            {
                **ITEM,
                "title": None,
                "thumbnail": 7,
                "license": ["cc0"],
                "price": {"type": "subscription", "value": "9.99", "display": None},
                "formats": ["stl", 3, None, "obj"],
                "tags": "дракон",
                "stats": {"likes": 12.0, "comments": True, "makes": "5", "collects": [1]},
                "publishedAt": 20210117,
            },
            "garbage",
            None,
            {"id": "only-id"},
        ],
    }
    page = make(Script(ok(body))).search("x")
    assert page.query == ""
    assert page.corrected_query is None
    assert page.did_you_mean == "драконы"
    assert page.total == 12
    assert page.total_relation == "approx"
    assert page.page == 0
    assert page.size == 0
    assert page.has_more is False
    assert page.partial is True
    assert len(page.items) == 2
    item = page.items[0]
    assert item.title == ""
    assert item.thumbnail is None
    assert item.license is None
    assert item.price == Price(type="subscription", value=None, currency=None, display="")
    assert item.formats == ("stl", "obj")
    assert item.tags == ()
    assert item.stats == ItemStats(likes=12)
    assert item.published_at is None
    assert page.items[1] == SearchItem(source="", id="only-id", title="", url="")


def test_lenient_parsing_of_model(make: Make) -> None:
    body = {
        "source": "printables",
        "id": "pr-1",
        "license": "cc0",
        "price": [],
        "stats": "many",
        "images": None,
        "copies": [{"source": "thingiverse", "id": "tv-1"}, "junk", {"source": 5}],
        "description": "",
    }
    model = make(Script(ok(body))).get_model("printables", "pr-1")
    assert model.license is None
    assert model.price is None
    assert model.stats == ModelStats()
    assert model.images == ()
    assert model.description is None
    assert model.copies == (ModelRef("thingiverse", "tv-1"), ModelRef("", ""))
    assert model.title == ""


def test_lenient_numbers(make: Make) -> None:
    body = {
        "items": [
            {
                **ITEM,
                "price": {"type": "paid", "value": 10**400, "currency": "USD", "display": "$$$"},
                "stats": {"likes": 10**30, "comments": -3, "makes": 1e300},
            },
            {**ITEM, "price": {"type": "paid", "value": 4, "currency": "EUR", "display": "4 €"}},
        ],
        "copies": 5,
        "license": {"raw": 5},
    }
    page = make(Script(ok(body))).search("x")
    assert page.items[0].price == Price(type="paid", value=None, currency="USD", display="$$$")
    assert page.items[0].stats.likes == 10**30
    assert page.items[0].stats.comments == -3
    assert page.items[0].stats.makes == int(1e300)
    assert page.items[1].price is not None
    assert page.items[1].price.value == 4.0
    model = make(Script(ok(body))).get_model("x", "y")
    assert model.copies == ()
    assert model.license == ModelLicense(id="", raw=None)


def test_me_and_limits_tolerate_missing_sections(make: Make) -> None:
    client = make(Script(ok({"account": None, "token": {"name": 3}})))
    me = client.me()
    assert me.account == Account(id="", display_name=None, created_at="")
    assert me.token == TokenInfo(id="", name="", prefix="", created_at="")
    limits = make(Script(ok({"perMinute": {"limit": 20.0}}))).limits()
    assert limits.per_minute == QuotaWindow(limit=20, used=0, remaining=0, reset_at=0)
    assert limits.daily == QuotaWindow(limit=0, used=0, remaining=0, reset_at=0)


def test_models_are_frozen_and_hashable() -> None:
    item = EXPECTED_ITEM
    with pytest.raises(AttributeError):
        item.title = "x"  # type: ignore[misc]
    assert {item, item} == {item}
    assert not hasattr(item, "__dict__")


def test_non_finite_numbers_from_raw_json(make: Make) -> None:
    raw = b'{"total": NaN, "page": Infinity, "size": -Infinity, "items": []}'
    page = make(Script(httpx.Response(200, content=raw))).search("x")
    assert (page.total, page.page, page.size) == (0, 0, 0)
