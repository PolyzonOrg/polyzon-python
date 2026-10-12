"""Official Python SDK for the Polyzon API (https://api.polyzon.org).

Search 3D models across Thingiverse, Printables, MakerWorld, Cults3D and other
sites, and fetch model cards::

    from polyzon import Polyzon

    with Polyzon() as client:  # reads POLYZON_API_KEY
        page = client.search("дракон", free=True, formats=["stl"])
        for item in page.items:
            print(item.title, item.url)

See :class:`Polyzon` (sync, thread-safe) and :class:`AsyncPolyzon` (asyncio).
"""

import logging

from ._client import AsyncPolyzon, Polyzon
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
from ._models import (
    LANGUAGES,
    LICENSES,
    PRICE_TYPES,
    SORT_ORDERS,
    SOURCES,
    TOTAL_RELATIONS,
    Account,
    ItemStats,
    Lang,
    Limits,
    Me,
    Model,
    ModelLicense,
    ModelRef,
    ModelResult,
    ModelStats,
    Price,
    QuotaWindow,
    RateLimit,
    ResponseMeta,
    SearchItem,
    SearchPage,
    Sort,
    TokenInfo,
)
from ._version import __version__

__all__ = [
    "LANGUAGES",
    "LICENSES",
    "PRICE_TYPES",
    "SORT_ORDERS",
    "SOURCES",
    "TOTAL_RELATIONS",
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "Account",
    "AsyncPolyzon",
    "AuthenticationError",
    "BadRequestError",
    "GoneError",
    "InternalServerError",
    "InvalidResponseError",
    "ItemStats",
    "Lang",
    "Limits",
    "Me",
    "Model",
    "ModelLicense",
    "ModelRef",
    "ModelResult",
    "ModelStats",
    "NotFoundError",
    "PermissionDeniedError",
    "Polyzon",
    "PolyzonError",
    "Price",
    "QuotaExceededError",
    "QuotaWindow",
    "RateLimit",
    "RateLimitError",
    "ResponseMeta",
    "SearchItem",
    "SearchPage",
    "Sort",
    "TokenInfo",
    "__version__",
]

logging.getLogger("polyzon").addHandler(logging.NullHandler())
