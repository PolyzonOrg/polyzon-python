# Polyzon Python SDK

The official Python library for the [Polyzon API](https://api.polyzon.org): search
3D models across Thingiverse, Printables, MakerWorld, Cults3D, MyMiniFactory and
more than a dozen other sites through one catalogue, and fetch full model cards.
It ships a synchronous, thread-safe client and an asyncio client, typed results
(`mypy --strict` clean, `py.typed`), automatic retries, bounded-concurrency batch
fetching and lazy pagination. Its only dependency is [httpx](https://www.python-httpx.org/).

[Русская версия](README.ru.md)

## Installation

```sh
pip install polyzon
```

SOCKS proxy support is an optional extra: `pip install "polyzon[socks]"`.

## Quick start

```python
from polyzon import Polyzon

with Polyzon() as client:  # reads the POLYZON_API_KEY environment variable
    page = client.search("дракон", free=True, formats=["stl"])
    for item in page.items:
        print(item.title, item.url)
```

## Authentication

Create a personal token at [polyzon.org/account](https://polyzon.org/account/)
(section «API»). Tokens look like `pzk_…`. Pass it explicitly or, better, keep it in
the environment:

```sh
export POLYZON_API_KEY="pzk_..."
```

```python
client = Polyzon()  # from POLYZON_API_KEY
client = Polyzon(api_key="pzk_...")  # explicit
```

A missing or malformed token raises `ValueError` when the client is created. The
token is never logged, never put in exception messages and masked in `repr`
(`api_key='pzk_Q7xR…'`).

## Methods

All methods are `GET` requests and return frozen dataclasses with snake_case
fields (lists are tuples). Every result also carries `meta` (see
[Rate limits and quota](#rate-limits-and-quota)).

### `search(q, *, page, size, sort, free, sources, formats, license, lang)` → `SearchPage`

Searches the catalogue. Costs one request of the daily quota.

```python
page = client.search(
    "chess set",
    page=1,  # 1..100
    size=50,  # 1..50, page * size <= 2400
    sort="popular",  # relevant | popular | newest | likes | makes | downloads
    free=True,  # only free models
    sources=["thingiverse", "printables"],
    formats=["stl", "3mf"],
    license=["cc0", "cc-by"],
    lang="en",  # ru (default) | en
)
print(page.total, page.total_relation, page.has_more, page.partial)
for item in page.items:
    print(item.source, item.id, item.title, item.price, item.stats.likes)
```

Unset arguments are not sent. List arguments take a list or tuple of strings — a
bare string raises `TypeError`. `polyzon.SOURCES` and `polyzon.LICENSES` list the
values known at release time; any other string is accepted, new sources appear.
`page.corrected_query` / `page.did_you_mean` hold spelling corrections and
suggestions; `partial=True` means part of the catalogue did not answer in time.

### `search_all(q, *, page, ...)` → iterator of `SearchItem`

See [Pagination](#pagination).

### `get_model(source, id, *, lang)` → `Model`

Fetches one model card: description, images, license, price, stats, dates and
`copies` (the same model on other sites). Costs one request of the daily quota.

```python
model = client.get_model("thingiverse", "tv-1234567", lang="en")
print(model.title, model.license, model.images, model.copies)
```

`source` must match `[a-z0-9]{1,32}` and `id` `[A-Za-z0-9._-]{1,255}`; anything else
raises `ValueError` without sending a request. A search result's `item.ref` is a
ready `ModelRef(source, id)`.

### `get_models(refs, *, concurrency=4, lang)` → `list[ModelResult]`

See [Concurrency and batches](#concurrency-and-batches).

### `me()` → `Me`

Who the token belongs to. Does not use the daily quota.

```python
me = client.me()
print(me.account.id, me.account.display_name, me.token.name, me.token.prefix)
```

### `limits()` → `Limits`

Current quota windows of the account. Does not use the daily quota.

```python
limits = client.limits()
print(limits.per_minute.remaining, limits.daily.remaining, limits.daily.reset_at)
```

## Pagination

`search_all` takes the same arguments as `search` and walks the pages lazily,
starting at `page` (default 1). It stops when the server reports no more results
(the catalogue serves at most 2400 results, up to page 100) or a page comes back
empty.

**Every page fetched costs one request of the daily quota**, so stop early when you
have enough:

```python
from itertools import islice

for item in islice(client.search_all("vase", size=50), 120):  # 3 requests
    print(item.title)
```

Argument errors are raised immediately; API errors of later pages are raised while
iterating.

## Errors

Everything the SDK raises for a failed call derives from `polyzon.PolyzonError`.
Invalid arguments raise the standard `ValueError` / `TypeError` before any request.

| Exception | When |
| --- | --- |
| `APIConnectionError` | No HTTP answer: DNS, TLS, proxy failure, refused or reset connection |
| `APITimeoutError` | Subclass of `APIConnectionError`: the timeout elapsed |
| `InvalidResponseError` | A 2xx answer that is not a JSON object, or a body over 8 MiB |
| `APIError` | Any HTTP error answer (base class; also 3xx and unlisted 4xx) |
| `BadRequestError` | 400 `bad_request` (see `.param`) or `page_too_deep` |
| `AuthenticationError` | 401 `unauthorized` / `invalid_token` |
| `PermissionDeniedError` | 403 `account_action_required`: accept the terms at polyzon.org/account |
| `NotFoundError` | 404 `not_found` |
| `GoneError` | 410 `gone`: removed at the right holder's request — delete your copy too |
| `RateLimitError` | 429 `rate_limited`: more than 20 requests per minute |
| `QuotaExceededError` | Subclass of `RateLimitError`: 429 `quota_exceeded`, daily quota used up |
| `InternalServerError` | 5xx `unavailable` / `internal` / anything else |

`APIError` exposes `status_code`, `code` (`None` for non-JSON answers such as an
HTML page from an edge proxy), `title`, `detail`, `param`, `request_id`,
`retry_after` (seconds), `headers` and `body` (the parsed problem document).

```python
import polyzon

try:
    client.search("дракон")
except polyzon.QuotaExceededError as error:
    print("Daily quota used up, retry after", error.retry_after, "seconds")
except polyzon.RateLimitError:
    print("Slow down")
except polyzon.APIError as error:
    print(error.status_code, error.code, error.detail, error.request_id)
except polyzon.APIConnectionError:
    print("Network problem")
```

Messages look like `429 rate_limited: Не больше 20 запросов в минуту. (request id
…)`. Quote the request id when you contact support.

## Retries

Failed calls are retried up to `max_retries` times (default 2; `0` disables) on
connection errors, timeouts, HTTP 500, 502, 503, 504 and 429 `rate_limited`.
`quota_exceeded` and other 4xx answers are never retried. The delay is the
server's `Retry-After` when present (seconds or an HTTP date); if the server asks
for more than 60 seconds the error is raised immediately instead of waiting.
Without `Retry-After` the delay is exponential backoff, `min(8, 0.5 × 2^attempt)`
seconds with full jitter.

```python
client = Polyzon(max_retries=5)
strict = client.with_options(max_retries=0)  # shares the connection pool
```

## Timeouts

`timeout` is the per-attempt budget in seconds (default 30): connecting may take at
most 10 of them and the whole response must arrive within it. Pass an
`httpx.Timeout` for per-phase control. Timeouts raise `APITimeoutError` (after
retries).

```python
client = Polyzon(timeout=10)
client = Polyzon(timeout=httpx.Timeout(20.0, connect=5.0))
```

## Proxies

```python
client = Polyzon(proxy="http://user:password@proxy.internal:3128")
```

`http://`, `https://` and — with `pip install "polyzon[socks]"` — `socks5://`
proxies are supported; credentials in the URL are sent as Basic proxy
authentication and never appear in `repr` or error messages. Requests to the API
are tunnelled with `CONNECT`, so TLS is end to end and the proxy never sees your
token.

Without `proxy=` the standard environment is honoured: `HTTPS_PROXY`,
`HTTP_PROXY`, `ALL_PROXY` and `NO_PROXY` (lower case too). Loopback targets are
never sent through environment proxies. `trust_env=False` ignores the environment.

## Concurrency and batches

`Polyzon` is immutable after construction and safe to share between threads; it
keeps a pool of keep-alive connections (`max_connections`, default 20). Create one
client per application and reuse it.

`get_models` fetches many cards with bounded parallelism (a thread pool on the
sync client, tasks on the async one). Results come back in input order, one per
ref, each holding the model or the error — a failure of one model never raises:

```python
from polyzon import ModelRef

results = client.get_models(
    [ModelRef("thingiverse", "tv-1234567"), ("printables", "pr-123456")],
    concurrency=4,
)
for result in results:
    if result.ok:
        print(result.model.title)
    else:
        print(result.ref, result.error)
```

After an `AuthenticationError`, `PermissionDeniedError` or `QuotaExceededError` the
refs not yet started get that same error without being requested. Every model costs
one request of the daily quota and counts towards the 20-per-minute limit (rate
limited calls are retried automatically).

## Async

`AsyncPolyzon` has the same constructor and methods as coroutines; `search_all`
is an async iterator.

```python
import asyncio
from polyzon import AsyncPolyzon


async def main() -> None:
    async with AsyncPolyzon() as client:
        me, page = await asyncio.gather(client.me(), client.search("кот"))
        async for item in client.search_all("vase", size=50):
            print(item.title)


asyncio.run(main())
```

Close it with `await client.aclose()` or `async with`.

## Logging

The SDK logs to the standard `logging` logger `"polyzon"` (with a `NullHandler`,
so nothing is printed by default). At `DEBUG` level every attempt is logged with
method, path and query, status, attempt number and retry delay — never headers or
the token.

```python
import logging

logging.basicConfig()
logging.getLogger("polyzon").setLevel(logging.DEBUG)
```

## Custom HTTP client, CA certificates and testing

TLS verification is always on and cannot be disabled. A corporate CA bundle is
picked up from `SSL_CERT_FILE` / `SSL_CERT_DIR`, or pass your own `httpx.Client`
(`httpx.AsyncClient` for the async client) for custom CAs, mTLS, transports or
tests. You keep ownership of a client you pass in: `close()` does not close it.

```python
import ssl
import httpx
from polyzon import Polyzon

context = ssl.create_default_context(cafile="/etc/ssl/corp-ca.pem")
with httpx.Client(verify=context) as http:
    client = Polyzon(http_client=http)
```

Redirects are never followed (a 3xx raises `APIError`) and response bodies are
capped at 8 MiB.

## Rate limits and quota

Quotas are per account, not per token:

* **20 requests per minute** for every method (`RateLimitError`, retried
  automatically);
* **200 requests per UTC day** for `search`, `search_all` pages, `get_model` and
  `get_models` items (`QuotaExceededError`, resets at 00:00 UTC). `me()` and
  `limits()` do not use the daily quota.

The server counts a request before validating its parameters, so even a
`BadRequestError` uses quota. Every result carries the daily window from the
response headers:

```python
page = client.search("дракон")
print(page.meta.request_id)
if page.meta.rate_limit:
    print(page.meta.rate_limit.remaining, page.meta.rate_limit.reset_at)
```

`meta` is excluded from `==` and `repr`. Call `limits()` for both windows.

## Versioning

This package follows [Semantic Versioning](https://semver.org/). Backwards
incompatible changes to the public API (everything exported from `polyzon`) only
happen in a new major version. New fields in results and new error codes may be
added in minor versions. See [CHANGELOG.md](CHANGELOG.md).

## Requirements

Python 3.10 or newer and `httpx>=0.27,<1`.

## Security

Never ship your token to browsers or mobile apps — anyone can extract it there.
Keep it server-side, in an environment variable or a secret store. Report
vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Links

* API documentation and sandbox: https://api.polyzon.org
* Tokens: https://polyzon.org/account/
* Source: https://github.com/PolyzonOrg/polyzon-python

## License

[MIT](LICENSE)
