"""The synchronous :class:`Polyzon` and asynchronous :class:`AsyncPolyzon` clients."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Sequence,
)
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import TypeVar, cast

import httpx

from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    BodyReader,
    Json,
    Options,
    build_request,
    check_concurrency,
    check_page,
    check_refs,
    http_client_kwargs,
    is_batch_fatal,
    lang_params,
    log_response,
    log_retry,
    model_path,
    next_page,
    parse_response,
    retry_delay,
    search_params,
    transport_error,
)
from ._exceptions import PolyzonError
from ._models import (
    Lang,
    Limits,
    Me,
    Model,
    ModelRef,
    ModelResult,
    ResponseMeta,
    SearchItem,
    SearchPage,
    Sort,
    parse_limits,
    parse_me,
    parse_model,
    parse_search_page,
)

__all__ = ["AsyncPolyzon", "Polyzon"]

_C = TypeVar("_C", bound="_BaseClient")


class _BaseClient:
    _options: Options
    _owns_http: bool

    def __repr__(self) -> str:
        return self._options.describe(type(self).__name__)

    def with_options(
        self: _C,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        user_agent: str | None = None,
    ) -> _C:
        """Return a client with some settings replaced that shares this client's connection pool.

        Arguments left as ``None`` keep their current value. The derived client is
        cheap to create; closing it does not close the shared pool (the client that
        created the pool owns it).

        Raises:
            ValueError, TypeError: a new value is invalid.
        """
        clone = copy.copy(self)
        clone._options = self._options.derive(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
        )
        clone._owns_http = False
        return clone


class Polyzon(_BaseClient):
    """Synchronous client of the Polyzon API.

    Immutable after construction and safe to share between threads: one instance
    holds one pooled ``httpx.Client``. Close it with :meth:`close` or use it as a
    context manager.

    Args:
        api_key: Personal token from https://polyzon.org/account/ (section «API»).
            Defaults to the ``POLYZON_API_KEY`` environment variable.
        base_url: API origin. Defaults to ``POLYZON_BASE_URL`` or
            ``https://api.polyzon.org``. Must be ``https://`` (``http://`` only for
            localhost) without credentials, query or fragment.
        timeout: Seconds per attempt (connect at most 10 s; the whole response must
            arrive within ``timeout``), or an ``httpx.Timeout`` for per-phase control.
        max_retries: Retries of failed idempotent calls; ``0`` disables them.
        proxy: Proxy URL, e.g. ``http://user:pass@proxy:3128``, ``https://...`` or
            ``socks5://...`` (needs ``pip install "polyzon[socks]"``). By default the
            ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY`` environment is honoured.
        trust_env: Read proxies and ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` from the
            environment. ``False`` ignores them.
        max_connections: Size of the connection pool (default 20).
        user_agent: Text appended to the default ``User-Agent``, e.g. ``"my-bot/2.1"``.
        http_client: Your own ``httpx.Client`` (custom CA, mTLS, transport, tests).
            You keep ownership: :meth:`close` will not close it. ``proxy`` and
            ``max_connections`` must then be configured on it instead.

    Raises:
        ValueError, TypeError: an argument is invalid or no token is available.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        proxy: str | httpx.Proxy | None = None,
        trust_env: bool = True,
        max_connections: int | None = None,
        user_agent: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Validate the settings and open the connection pool."""
        self._options = Options.resolve(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
            proxy=proxy,
        )
        if http_client is None:
            kwargs = http_client_kwargs(
                proxy=proxy, trust_env=trust_env, max_connections=max_connections
            )
            self._http = httpx.Client(**kwargs)
            self._owns_http = True
        else:
            if not isinstance(http_client, httpx.Client):
                raise TypeError("http_client must be an httpx.Client (use AsyncPolyzon for async)")
            if proxy is not None or max_connections is not None:
                raise ValueError("configure proxy and max_connections on your http_client instead")
            self._http = http_client
            self._owns_http = False
        self._sleep: Callable[[float], None] = time.sleep

    def __enter__(self: _C) -> _C:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection pool, unless it was passed in or belongs to a parent client."""
        if self._owns_http:
            self._http.close()

    def search(
        self,
        q: str,
        *,
        page: int | None = None,
        size: int | None = None,
        sort: Sort | None = None,
        free: bool | None = None,
        sources: Sequence[str] | None = None,
        formats: Sequence[str] | None = None,
        license: Sequence[str] | None = None,
        lang: Lang | None = None,
    ) -> SearchPage:
        """Search the catalogue. Costs one request of the daily quota.

        Args:
            q: Query in Russian or English, up to 200 characters.
            page: Page number from 1 (server maximum 100).
            size: Results per page, 1-50 (default 20); ``page * size`` must stay <= 2400.
            sort: ``"relevant"`` (default), ``"popular"``, ``"newest"``, ``"likes"``,
                ``"makes"`` or ``"downloads"``.
            free: ``True`` returns only free models.
            sources: Source sites, e.g. ``["thingiverse", "printables"]`` (see ``SOURCES``).
            formats: File formats, e.g. ``["stl", "3mf"]``.
            license: License ids, e.g. ``["cc0", "cc-by"]`` (see ``LICENSES``).
            lang: ``"ru"`` (default) or ``"en"`` for titles and tags.

        Raises:
            ValueError, TypeError: an argument is invalid (nothing is sent).
            BadRequestError: the server rejected a parameter (the request is still counted).
            PolyzonError: any other failure.
        """
        params = search_params(
            q,
            page=page,
            size=size,
            sort=sort,
            free=free,
            sources=sources,
            formats=formats,
            license=license,
            lang=lang,
        )
        return parse_search_page(*self._get("/v1/search", params))

    def search_all(
        self,
        q: str,
        *,
        page: int | None = None,
        size: int | None = None,
        sort: Sort | None = None,
        free: bool | None = None,
        sources: Sequence[str] | None = None,
        formats: Sequence[str] | None = None,
        license: Sequence[str] | None = None,
        lang: Lang | None = None,
    ) -> Iterator[SearchItem]:
        """Iterate over every result, fetching pages lazily from ``page`` (default 1).

        Stops when the server reports no more results (at most 2400 results, page
        100) or a page comes back empty. **Every page fetched costs one request of
        the daily quota** (200 per day by default), so stop iterating early when you
        have enough. Arguments are validated immediately; errors of later pages are
        raised during iteration. Takes the same arguments as :meth:`search`.
        """
        start = check_page(page)
        params = search_params(
            q,
            page=None,
            size=size,
            sort=sort,
            free=free,
            sources=sources,
            formats=formats,
            license=license,
            lang=lang,
        )
        return self._search_pages(params, start)

    def _search_pages(self, params: list[tuple[str, str]], page: int) -> Iterator[SearchItem]:
        current: int | None = page
        while current is not None:
            result = parse_search_page(*self._get("/v1/search", [*params, ("page", str(current))]))
            yield from result.items
            current = next_page(result, current)

    def get_model(self, source: str, id: str, *, lang: Lang | None = None) -> Model:
        """Fetch one model card. Costs one request of the daily quota.

        Args:
            source: Source site, e.g. ``"thingiverse"`` (``[a-z0-9]{1,32}``).
            id: Model id with the Polyzon prefix, e.g. ``"tv-1234567"``.
            lang: ``"ru"`` (default) or ``"en"``.

        Raises:
            ValueError, TypeError: ``source`` or ``id`` is malformed (nothing is sent).
            NotFoundError: no such model.
            GoneError: the model was removed at the right holder's request.
            PolyzonError: any other failure.
        """
        path = model_path(source, id)
        return parse_model(*self._get(path, lang_params(lang)))

    def get_models(
        self,
        refs: Iterable[ModelRef | tuple[str, str]],
        *,
        concurrency: int = 4,
        lang: Lang | None = None,
    ) -> list[ModelResult]:
        """Fetch many models in parallel threads. Each model costs one daily-quota request.

        Returns one :class:`ModelResult` per ref, in input order, holding the model or
        the error; per-model failures are never raised. After an
        ``AuthenticationError``, ``PermissionDeniedError`` or ``QuotaExceededError``
        the refs not yet started get that same error without being requested.

        Args:
            refs: ``ModelRef`` values or ``(source, id)`` tuples.
            concurrency: Maximum requests in flight (>= 1).
            lang: ``"ru"`` (default) or ``"en"``.

        Raises:
            ValueError, TypeError: a ref or argument is invalid (nothing is sent).
        """
        checked = check_refs(refs)
        workers = check_concurrency(concurrency)
        lang_params(lang)
        if not checked:
            return []
        fatal: list[PolyzonError] = []

        def fetch(ref: ModelRef) -> ModelResult:
            if fatal:
                return ModelResult(ref, None, fatal[0])
            try:
                return ModelResult(ref, self.get_model(ref.source, ref.id, lang=lang), None)
            except PolyzonError as error:
                if is_batch_fatal(error):
                    fatal.append(error)
                return ModelResult(ref, None, error)

        with ThreadPoolExecutor(min(workers, len(checked)), "polyzon") as pool:
            return list(pool.map(fetch, checked))

    def me(self) -> Me:
        """The account and token this client authenticates as. Free of daily quota."""
        return parse_me(*self._get("/v1/me", []))

    def limits(self) -> Limits:
        """Remaining per-minute and daily quota of the account. Free of daily quota."""
        return parse_limits(*self._get("/v1/limits", []))

    def _get(self, path: str, params: list[tuple[str, str]]) -> tuple[Json, ResponseMeta]:
        request = build_request(self._http, self._options, path, params)
        attempt = 0
        while True:
            try:
                return self._send(request, attempt)
            except PolyzonError as error:
                delay = retry_delay(error, attempt, self._options.max_retries)
                if delay is None:
                    raise
                log_retry(request, error, attempt, delay)
            self._sleep(delay)
            attempt += 1

    def _send(self, request: httpx.Request, attempt: int) -> tuple[Json, ResponseMeta]:
        reader = BodyReader(self._options.deadline)
        try:
            response = self._http.send(request, stream=True, follow_redirects=False)
            try:
                for chunk in response.iter_bytes():
                    if not reader.add(chunk):
                        break
            finally:
                response.close()
        except httpx.HTTPError as error:
            raise transport_error(error, self._options) from None
        log_response(request, response.status_code, attempt)
        return parse_response(response, reader)


class AsyncPolyzon(_BaseClient):
    """Asynchronous (asyncio) client of the Polyzon API.

    Same settings and behaviour as :class:`Polyzon`; methods are coroutines and
    :meth:`search_all` is an async iterator. Safe to share between tasks. Close it
    with :meth:`aclose` or use ``async with``. ``http_client`` must be an
    ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        proxy: str | httpx.Proxy | None = None,
        trust_env: bool = True,
        max_connections: int | None = None,
        user_agent: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Validate the settings and open the connection pool (see :class:`Polyzon`)."""
        self._options = Options.resolve(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
            proxy=proxy,
        )
        if http_client is None:
            kwargs = http_client_kwargs(
                proxy=proxy, trust_env=trust_env, max_connections=max_connections
            )
            self._http = httpx.AsyncClient(**kwargs)
            self._owns_http = True
        else:
            if not isinstance(http_client, httpx.AsyncClient):
                raise TypeError("http_client must be an httpx.AsyncClient (use Polyzon for sync)")
            if proxy is not None or max_connections is not None:
                raise ValueError("configure proxy and max_connections on your http_client instead")
            self._http = http_client
            self._owns_http = False
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def __aenter__(self: _C) -> _C:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the connection pool, unless it was passed in or belongs to a parent client."""
        if self._owns_http:
            await self._http.aclose()

    async def search(
        self,
        q: str,
        *,
        page: int | None = None,
        size: int | None = None,
        sort: Sort | None = None,
        free: bool | None = None,
        sources: Sequence[str] | None = None,
        formats: Sequence[str] | None = None,
        license: Sequence[str] | None = None,
        lang: Lang | None = None,
    ) -> SearchPage:
        """Search the catalogue (see :meth:`Polyzon.search`). Costs one daily-quota request."""
        params = search_params(
            q,
            page=page,
            size=size,
            sort=sort,
            free=free,
            sources=sources,
            formats=formats,
            license=license,
            lang=lang,
        )
        return parse_search_page(*await self._get("/v1/search", params))

    def search_all(
        self,
        q: str,
        *,
        page: int | None = None,
        size: int | None = None,
        sort: Sort | None = None,
        free: bool | None = None,
        sources: Sequence[str] | None = None,
        formats: Sequence[str] | None = None,
        license: Sequence[str] | None = None,
        lang: Lang | None = None,
    ) -> AsyncIterator[SearchItem]:
        """Iterate with ``async for`` over every result (see :meth:`Polyzon.search_all`).

        **Every page fetched costs one request of the daily quota.**
        """
        start = check_page(page)
        params = search_params(
            q,
            page=None,
            size=size,
            sort=sort,
            free=free,
            sources=sources,
            formats=formats,
            license=license,
            lang=lang,
        )
        return self._search_pages(params, start)

    async def _search_pages(
        self, params: list[tuple[str, str]], page: int
    ) -> AsyncIterator[SearchItem]:
        current: int | None = page
        while current is not None:
            data = await self._get("/v1/search", [*params, ("page", str(current))])
            result = parse_search_page(*data)
            for item in result.items:
                yield item
            current = next_page(result, current)

    async def get_model(self, source: str, id: str, *, lang: Lang | None = None) -> Model:
        """Fetch one model card (see :meth:`Polyzon.get_model`). Costs one daily-quota request."""
        path = model_path(source, id)
        return parse_model(*await self._get(path, lang_params(lang)))

    async def get_models(
        self,
        refs: Iterable[ModelRef | tuple[str, str]],
        *,
        concurrency: int = 4,
        lang: Lang | None = None,
    ) -> list[ModelResult]:
        """Fetch many models concurrently (see :meth:`Polyzon.get_models`).

        At most ``concurrency`` requests are in flight; results keep input order.
        """
        checked = check_refs(refs)
        semaphore = asyncio.Semaphore(check_concurrency(concurrency))
        lang_params(lang)
        fatal: list[PolyzonError] = []

        async def fetch(ref: ModelRef) -> ModelResult:
            async with semaphore:
                if fatal:
                    return ModelResult(ref, None, fatal[0])
                try:
                    model = await self.get_model(ref.source, ref.id, lang=lang)
                except PolyzonError as error:
                    if is_batch_fatal(error):
                        fatal.append(error)
                    return ModelResult(ref, None, error)
                return ModelResult(ref, model, None)

        return list(await asyncio.gather(*(fetch(ref) for ref in checked)))

    async def me(self) -> Me:
        """The account and token this client authenticates as. Free of daily quota."""
        return parse_me(*await self._get("/v1/me", []))

    async def limits(self) -> Limits:
        """Remaining per-minute and daily quota of the account. Free of daily quota."""
        return parse_limits(*await self._get("/v1/limits", []))

    async def _get(self, path: str, params: list[tuple[str, str]]) -> tuple[Json, ResponseMeta]:
        request = build_request(self._http, self._options, path, params)
        attempt = 0
        while True:
            try:
                return await self._send(request, attempt)
            except PolyzonError as error:
                delay = retry_delay(error, attempt, self._options.max_retries)
                if delay is None:
                    raise
                log_retry(request, error, attempt, delay)
            await self._sleep(delay)
            attempt += 1

    async def _send(self, request: httpx.Request, attempt: int) -> tuple[Json, ResponseMeta]:
        reader = BodyReader(self._options.deadline)
        try:
            response = await self._http.send(request, stream=True, follow_redirects=False)
            chunks = cast("AsyncGenerator[bytes, None]", response.aiter_bytes())
            try:
                async for chunk in chunks:
                    if not reader.add(chunk):
                        break
            finally:
                await chunks.aclose()
                await response.aclose()
        except httpx.HTTPError as error:
            raise transport_error(error, self._options) from None
        log_response(request, response.status_code, attempt)
        return parse_response(response, reader)
