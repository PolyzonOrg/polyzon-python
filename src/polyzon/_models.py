"""Typed results of the Polyzon API and the lenient parsers that build them.

All result classes are frozen, slotted dataclasses: immutable, hashable and cheap.
JSON fields arrive in camelCase and are exposed in snake_case. Lists are tuples.
The server is treated as untrusted input: unknown fields are ignored, missing or
wrong-typed optional fields become ``None`` / ``()``, counts sent as floats are
truncated to ``int``, and dates stay ISO-8601 strings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple

from ._exceptions import PolyzonError

__all__ = [
    "LANGUAGES",
    "LICENSES",
    "PRICE_TYPES",
    "SORT_ORDERS",
    "SOURCES",
    "TOTAL_RELATIONS",
    "Account",
    "ItemStats",
    "Lang",
    "Limits",
    "Me",
    "Model",
    "ModelLicense",
    "ModelRef",
    "ModelResult",
    "ModelStats",
    "Price",
    "QuotaWindow",
    "RateLimit",
    "ResponseMeta",
    "SearchItem",
    "SearchPage",
    "Sort",
    "TokenInfo",
]

Sort = Literal["relevant", "popular", "newest", "likes", "makes", "downloads"]
"""Order of search results; ``"relevant"`` is the server default."""

Lang = Literal["ru", "en"]
"""Language of titles, descriptions and tags; ``"ru"`` is the server default."""

SOURCES: tuple[str, ...] = (
    "thingiverse",
    "cults3d",
    "printables",
    "makerworld",
    "3dtoday",
    "crealitycloud",
    "myminifactory",
    "thangs",
    "youmagine",
    "grabcad",
    "sketchfab",
    "threeding",
    "nexprint",
    "nasa",
    "nih3d",
    "smithsonian",
    "pinshape",
    "cgtrader",
    "gambody",
    "jewelmodel",
    "makeronline",
    "3dexport",
)
"""Source sites known when this SDK was released. Any other string is accepted too."""

LICENSES: tuple[str, ...] = (
    "cc0",
    "cc-by",
    "cc-by-sa",
    "cc-by-nd",
    "cc-by-nc",
    "cc-by-nc-sa",
    "cc-by-nc-nd",
    "gpl",
    "lgpl",
    "mit",
    "bsd",
    "private-use",
    "commercial",
    "custom",
    "unknown",
)
"""License ids known when this SDK was released. Any other string is accepted too."""

SORT_ORDERS: tuple[str, ...] = ("relevant", "popular", "newest", "likes", "makes", "downloads")
"""Every value of :data:`Sort`."""

LANGUAGES: tuple[str, ...] = ("ru", "en")
"""Every value of :data:`Lang`."""

PRICE_TYPES: tuple[str, ...] = ("free", "paid", "open")
"""Known values of :attr:`Price.type`; new ones may appear and are passed through."""

TOTAL_RELATIONS: tuple[str, ...] = ("eq", "gte")
"""Known values of :attr:`SearchPage.total_relation`."""

_Json = dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class RateLimit:
    """The daily quota window as reported by the ``x-ratelimit-*`` response headers."""

    limit: int
    """Requests allowed per UTC day."""
    remaining: int
    """Requests left today."""
    reset_at: int
    """When the window resets, Unix seconds."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseMeta:
    """Transport metadata of a successful answer. Excluded from ``==`` and ``repr``."""

    request_id: str | None = None
    """``X-Request-ID`` of the answer; quote it when reporting a problem."""
    rate_limit: RateLimit | None = None
    """The daily quota after this call, or ``None`` when the headers were absent."""


_NO_META = ResponseMeta()


class ModelRef(NamedTuple):
    """A model address: source site and model id. A plain ``(source, id)`` tuple works too."""

    source: str
    """Source site, e.g. ``"thingiverse"``."""
    id: str
    """Model id on that site with the Polyzon prefix, e.g. ``"tv-1234567"``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Price:
    """Price of a model."""

    type: str
    """``"free"``, ``"paid"`` or ``"open"`` (see :data:`PRICE_TYPES`)."""
    value: float | None = None
    """Amount, when known."""
    currency: str | None = None
    """ISO currency code, when known."""
    display: str = ""
    """Ready-to-show text, e.g. ``"Бесплатно"``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemStats:
    """Counters of a search result; ``None`` when the source does not publish one."""

    likes: int | None = None
    comments: int | None = None
    makes: int | None = None
    collects: int | None = None
    """Saves to collections, or downloads when the source does not count saves."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchItem:
    """One model in a search result page."""

    source: str
    id: str
    title: str
    url: str
    """The model page on the source site."""
    thumbnail: str | None = None
    creator: str | None = None
    license: str | None = None
    """License id (see :data:`LICENSES`)."""
    price: Price | None = None
    formats: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    stats: ItemStats = field(default_factory=ItemStats)
    published_at: str | None = None
    """ISO-8601 string as reported by the source; not parsed."""

    @property
    def ref(self) -> ModelRef:
        """The address to pass to ``get_model`` / ``get_models``."""
        return ModelRef(self.source, self.id)


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchPage:
    """One page of search results."""

    query: str
    corrected_query: str | None = None
    """The spelling-corrected query the results are for, if the server corrected it."""
    did_you_mean: str | None = None
    """A suggested alternative query."""
    total: int
    total_relation: str
    """``"eq"``: :attr:`total` is exact; ``"gte"``: it is a lower bound."""
    page: int
    size: int
    has_more: bool
    """Whether a next page exists within the 2400-result window."""
    partial: bool
    """Part of the catalogue did not answer in time; results may be incomplete."""
    items: tuple[SearchItem, ...] = ()
    meta: ResponseMeta = field(default=_NO_META, compare=False, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelLicense:
    """License of a model."""

    id: str
    """License id (see :data:`LICENSES`)."""
    raw: str | None = None
    """The license text as the source shows it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelStats:
    """Counters of a model; ``None`` when the source does not publish one."""

    likes: int | None = None
    comments: int | None = None
    makes: int | None = None
    collects: int | None = None
    downloads: int | None = None
    views: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Model:
    """A model card."""

    source: str
    id: str
    title: str
    url: str
    description: str | None = None
    thumbnail: str | None = None
    images: tuple[str, ...] = ()
    """Image URLs on the source site's CDN."""
    creator: str | None = None
    creator_url: str | None = None
    category: str | None = None
    license: ModelLicense | None = None
    price: Price | None = None
    formats: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    stats: ModelStats = field(default_factory=ModelStats)
    published_at: str | None = None
    updated_at: str | None = None
    copies: tuple[ModelRef, ...] = ()
    """The same model on other source sites."""
    meta: ResponseMeta = field(default=_NO_META, compare=False, repr=False)

    @property
    def ref(self) -> ModelRef:
        """The address of this model."""
        return ModelRef(self.source, self.id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Account:
    """The account that owns the token."""

    id: str
    display_name: str | None = None
    created_at: str
    """ISO-8601."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenInfo:
    """The token the request was made with (never the secret itself)."""

    id: str
    name: str
    prefix: str
    """The first characters of the token, e.g. ``"pzk_Q7xR2mVb"``."""
    created_at: str
    """ISO-8601."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Me:
    """Who the token belongs to."""

    account: Account
    token: TokenInfo
    meta: ResponseMeta = field(default=_NO_META, compare=False, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class QuotaWindow:
    """One quota window."""

    limit: int
    used: int
    remaining: int
    reset_at: int
    """Start of the next window, Unix seconds."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    """Quota windows of the account (they are per account, not per token)."""

    per_minute: QuotaWindow
    """Every method counts here (default 20 per minute)."""
    daily: QuotaWindow
    """Search and model calls count here (default 200 per UTC day)."""
    meta: ResponseMeta = field(default=_NO_META, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class ModelResult:
    """One entry of a ``get_models`` batch: either :attr:`model` or :attr:`error` is set."""

    ref: ModelRef
    """The requested address, normalised to :class:`ModelRef`."""
    model: Model | None
    """The model, when the call succeeded."""
    error: PolyzonError | None
    """Why the call failed, when it did."""

    @property
    def ok(self) -> bool:
        """``True`` when the model was fetched."""
        return self.error is None


# --- lenient parsing ------------------------------------------------------------


def _obj(value: object) -> _Json:
    return value if isinstance(value, dict) else {}


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    return None


def _int(value: object) -> int:
    return _opt_int(value) or 0


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer beyond the float range
        return None
    return number if math.isfinite(number) else None


def _price(value: object) -> Price | None:
    if not isinstance(value, dict):
        return None
    return Price(
        type=_str(value.get("type")),
        value=_opt_float(value.get("value")),
        currency=_opt_str(value.get("currency")),
        display=_str(value.get("display")),
    )


def _item(data: _Json) -> SearchItem:
    stats = _obj(data.get("stats"))
    return SearchItem(
        source=_str(data.get("source")),
        id=_str(data.get("id")),
        title=_str(data.get("title")),
        url=_str(data.get("url")),
        thumbnail=_opt_str(data.get("thumbnail")),
        creator=_opt_str(data.get("creator")),
        license=_opt_str(data.get("license")),
        price=_price(data.get("price")),
        formats=_strings(data.get("formats")),
        tags=_strings(data.get("tags")),
        stats=ItemStats(
            likes=_opt_int(stats.get("likes")),
            comments=_opt_int(stats.get("comments")),
            makes=_opt_int(stats.get("makes")),
            collects=_opt_int(stats.get("collects")),
        ),
        published_at=_opt_str(data.get("publishedAt")),
    )


def parse_search_page(data: _Json, meta: ResponseMeta) -> SearchPage:
    """Build a :class:`SearchPage` from a decoded ``/v1/search`` body."""
    items = data.get("items")
    return SearchPage(
        query=_str(data.get("query")),
        corrected_query=_opt_str(data.get("correctedQuery")),
        did_you_mean=_opt_str(data.get("didYouMean")),
        total=_int(data.get("total")),
        total_relation=_str(data.get("totalRelation")),
        page=_int(data.get("page")),
        size=_int(data.get("size")),
        has_more=data.get("hasMore") is True,
        partial=data.get("partial") is True,
        items=tuple(_item(i) for i in items if isinstance(i, dict))
        if isinstance(items, list)
        else (),
        meta=meta,
    )


def parse_model(data: _Json, meta: ResponseMeta) -> Model:
    """Build a :class:`Model` from a decoded ``/v1/models/{source}/{id}`` body."""
    stats = _obj(data.get("stats"))
    license_data = data.get("license")
    copies = data.get("copies")
    return Model(
        source=_str(data.get("source")),
        id=_str(data.get("id")),
        title=_str(data.get("title")),
        url=_str(data.get("url")),
        description=_opt_str(data.get("description")),
        thumbnail=_opt_str(data.get("thumbnail")),
        images=_strings(data.get("images")),
        creator=_opt_str(data.get("creator")),
        creator_url=_opt_str(data.get("creatorUrl")),
        category=_opt_str(data.get("category")),
        license=ModelLicense(id=_str(license_data.get("id")), raw=_opt_str(license_data.get("raw")))
        if isinstance(license_data, dict)
        else None,
        price=_price(data.get("price")),
        formats=_strings(data.get("formats")),
        tags=_strings(data.get("tags")),
        stats=ModelStats(
            likes=_opt_int(stats.get("likes")),
            comments=_opt_int(stats.get("comments")),
            makes=_opt_int(stats.get("makes")),
            collects=_opt_int(stats.get("collects")),
            downloads=_opt_int(stats.get("downloads")),
            views=_opt_int(stats.get("views")),
        ),
        published_at=_opt_str(data.get("publishedAt")),
        updated_at=_opt_str(data.get("updatedAt")),
        copies=tuple(
            ModelRef(_str(c.get("source")), _str(c.get("id")))
            for c in copies
            if isinstance(c, dict)
        )
        if isinstance(copies, list)
        else (),
        meta=meta,
    )


def parse_me(data: _Json, meta: ResponseMeta) -> Me:
    """Build a :class:`Me` from a decoded ``/v1/me`` body."""
    account = _obj(data.get("account"))
    token = _obj(data.get("token"))
    return Me(
        account=Account(
            id=_str(account.get("id")),
            display_name=_opt_str(account.get("displayName")),
            created_at=_str(account.get("createdAt")),
        ),
        token=TokenInfo(
            id=_str(token.get("id")),
            name=_str(token.get("name")),
            prefix=_str(token.get("prefix")),
            created_at=_str(token.get("createdAt")),
        ),
        meta=meta,
    )


def _window(value: object) -> QuotaWindow:
    data = _obj(value)
    return QuotaWindow(
        limit=_int(data.get("limit")),
        used=_int(data.get("used")),
        remaining=_int(data.get("remaining")),
        reset_at=_int(data.get("resetAt")),
    )


def parse_limits(data: _Json, meta: ResponseMeta) -> Limits:
    """Build a :class:`Limits` from a decoded ``/v1/limits`` body."""
    return Limits(
        per_minute=_window(data.get("perMinute")), daily=_window(data.get("daily")), meta=meta
    )
