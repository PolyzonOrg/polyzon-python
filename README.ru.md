# Polyzon Python SDK

Официальная библиотека Python для [Polyzon API](https://api.polyzon.org): поиск
3D-моделей сразу по Thingiverse, Printables, MakerWorld, Cults3D, MyMiniFactory и
ещё полутора десяткам площадок через единый каталог, а также полные карточки
моделей. В комплекте синхронный потокобезопасный клиент и клиент для asyncio,
типизированные результаты (`mypy --strict`, `py.typed`), автоматические повторы,
пакетная загрузка с ограничением параллельности и ленивая пагинация. Единственная
зависимость — [httpx](https://www.python-httpx.org/).

[English version](README.md)

## Установка

```sh
pip install polyzon
```

Поддержка SOCKS-прокси — дополнительный пакет: `pip install "polyzon[socks]"`.

## Быстрый старт

```python
from polyzon import Polyzon

with Polyzon() as client:  # токен берётся из переменной окружения POLYZON_API_KEY
    page = client.search("дракон", free=True, formats=["stl"])
    for item in page.items:
        print(item.title, item.url)
```

## Авторизация

Создайте личный токен в [polyzon.org/account](https://polyzon.org/account/)
(раздел «API»). Токены выглядят как `pzk_…`. Передайте его явно или, лучше,
храните в окружении:

```sh
export POLYZON_API_KEY="pzk_..."
```

```python
client = Polyzon()  # из POLYZON_API_KEY
client = Polyzon(api_key="pzk_...")  # явно
```

Если токена нет или он некорректен, конструктор бросает `ValueError`. Токен никогда
не пишется в логи и сообщения исключений, а в `repr` маскируется
(`api_key='pzk_Q7xR…'`).

## Методы

Все методы — запросы `GET`; они возвращают неизменяемые dataclass-объекты с полями
в snake_case (списки — кортежи). У каждого результата есть `meta` (см.
[Лимиты и квота](#лимиты-и-квота)).

### `search(q, *, page, size, sort, free, sources, formats, license, lang)` → `SearchPage`

Поиск по каталогу. Тратит один запрос суточной квоты.

```python
page = client.search(
    "шахматы",
    page=1,  # 1..100
    size=50,  # 1..50, page * size <= 2400
    sort="popular",  # relevant | popular | newest | likes | makes | downloads
    free=True,  # только бесплатные
    sources=["thingiverse", "printables"],
    formats=["stl", "3mf"],
    license=["cc0", "cc-by"],
    lang="ru",  # ru (по умолчанию) | en
)
print(page.total, page.total_relation, page.has_more, page.partial)
for item in page.items:
    print(item.source, item.id, item.title, item.price, item.stats.likes)
```

Незаданные аргументы не отправляются. Списочные аргументы принимают список или
кортеж строк — одиночная строка вызывает `TypeError`. `polyzon.SOURCES` и
`polyzon.LICENSES` перечисляют значения, известные на момент выпуска; любые другие
строки тоже принимаются — новые площадки появляются. `page.corrected_query` и
`page.did_you_mean` — исправление опечаток и подсказка; `partial=True` значит, что
часть каталога не ответила вовремя.

### `search_all(q, *, page, ...)` → итератор `SearchItem`

См. [Пагинация](#пагинация).

### `get_model(source, id, *, lang)` → `Model`

Карточка модели: описание, картинки, лицензия, цена, статистика, даты и `copies`
(та же модель на других площадках). Тратит один запрос суточной квоты.

```python
model = client.get_model("thingiverse", "tv-1234567", lang="en")
print(model.title, model.license, model.images, model.copies)
```

`source` должен соответствовать `[a-z0-9]{1,32}`, а `id` — `[A-Za-z0-9._-]{1,255}`;
иначе `ValueError` без отправки запроса. У элемента выдачи есть готовый
`item.ref` — `ModelRef(source, id)`.

### `get_models(refs, *, concurrency=4, lang)` → `list[ModelResult]`

См. [Параллельность и пакетная загрузка](#параллельность-и-пакетная-загрузка).

### `me()` → `Me`

Кому принадлежит токен. Суточную квоту не тратит.

```python
me = client.me()
print(me.account.id, me.account.display_name, me.token.name, me.token.prefix)
```

### `limits()` → `Limits`

Текущие окна квот аккаунта. Суточную квоту не тратит.

```python
limits = client.limits()
print(limits.per_minute.remaining, limits.daily.remaining, limits.daily.reset_at)
```

## Пагинация

`search_all` принимает те же аргументы, что `search`, и лениво идёт по страницам,
начиная с `page` (по умолчанию 1). Обход заканчивается, когда сервер сообщает, что
результатов больше нет (каталог отдаёт не больше 2400 результатов, до 100-й
страницы), или когда страница пришла пустой.

**Каждая загруженная страница тратит один запрос суточной квоты**, поэтому
останавливайтесь, как только данных достаточно:

```python
from itertools import islice

for item in islice(client.search_all("ваза", size=50), 120):  # 3 запроса
    print(item.title)
```

Ошибки аргументов возникают сразу; ошибки API на следующих страницах — во время
итерации.

## Ошибки

Всё, что SDK бросает при неудачном вызове, наследует `polyzon.PolyzonError`.
Неверные аргументы вызывают стандартные `ValueError` / `TypeError` ещё до запроса.

| Исключение | Когда |
| --- | --- |
| `APIConnectionError` | HTTP-ответа нет: DNS, TLS, сбой прокси, соединение отвергнуто или разорвано |
| `APITimeoutError` | Подкласс `APIConnectionError`: истёк таймаут |
| `InvalidResponseError` | Ответ 2xx — не JSON-объект или тело больше 8 МиБ |
| `APIError` | Любой HTTP-ответ с ошибкой (базовый класс; также 3xx и прочие 4xx) |
| `BadRequestError` | 400 `bad_request` (см. `.param`) или `page_too_deep` |
| `AuthenticationError` | 401 `unauthorized` / `invalid_token` |
| `PermissionDeniedError` | 403 `account_action_required`: примите условия в polyzon.org/account |
| `NotFoundError` | 404 `not_found` |
| `GoneError` | 410 `gone`: удалена по требованию правообладателя — удалите и у себя |
| `RateLimitError` | 429 `rate_limited`: больше 20 запросов в минуту |
| `QuotaExceededError` | Подкласс `RateLimitError`: 429 `quota_exceeded`, суточная квота исчерпана |
| `InternalServerError` | 5xx `unavailable` / `internal` / любые другие |

У `APIError` есть `status_code`, `code` (`None`, если ответ не JSON — например,
HTML-страница пограничного прокси), `title`, `detail`, `param`, `request_id`,
`retry_after` (секунды), `headers` и `body` (разобранный problem-документ).

```python
import polyzon

try:
    client.search("дракон")
except polyzon.QuotaExceededError as error:
    print("Суточная квота исчерпана, повторите через", error.retry_after, "с")
except polyzon.RateLimitError:
    print("Слишком часто")
except polyzon.APIError as error:
    print(error.status_code, error.code, error.detail, error.request_id)
except polyzon.APIConnectionError:
    print("Проблема с сетью")
```

Сообщения выглядят так: `429 rate_limited: Не больше 20 запросов в минуту. (request
id …)`. Сообщайте request id, когда пишете в поддержку.

## Повторы

Неудачные вызовы повторяются до `max_retries` раз (по умолчанию 2; `0` отключает)
при ошибках соединения, таймаутах, HTTP 500, 502, 503, 504 и 429 `rate_limited`.
`quota_exceeded` и прочие 4xx не повторяются никогда. Пауза — значение
`Retry-After` от сервера (секунды или HTTP-дата); если сервер просит ждать больше
60 секунд, ошибка бросается сразу, без ожидания. Без `Retry-After` — экспоненциальная
задержка `min(8, 0.5 × 2^попытка)` секунд со случайным множителем.

```python
client = Polyzon(max_retries=5)
strict = client.with_options(max_retries=0)  # общий пул соединений
```

## Таймауты

`timeout` — бюджет одной попытки в секундах (по умолчанию 30): на соединение уходит
не больше 10 из них, весь ответ должен прийти в его пределах. Для управления
отдельными фазами передайте `httpx.Timeout`. Истечение таймаута (после повторов) —
`APITimeoutError`.

```python
client = Polyzon(timeout=10)
client = Polyzon(timeout=httpx.Timeout(20.0, connect=5.0))
```

## Прокси

```python
client = Polyzon(proxy="http://user:password@proxy.internal:3128")
```

Поддерживаются прокси `http://`, `https://` и — после `pip install "polyzon[socks]"`
— `socks5://`; логин и пароль из URL отправляются как Basic-авторизация прокси и
никогда не попадают в `repr` и сообщения об ошибках. Запросы к API идут через
туннель `CONNECT`: TLS сквозной, прокси не видит ваш токен.

Без `proxy=` учитывается стандартное окружение: `HTTPS_PROXY`, `HTTP_PROXY`,
`ALL_PROXY` и `NO_PROXY` (и в нижнем регистре). Запросы к loopback-адресам через
прокси из окружения не идут никогда. `trust_env=False` отключает окружение.

## Параллельность и пакетная загрузка

`Polyzon` неизменяем после создания и безопасен для использования из многих
потоков; он держит пул keep-alive-соединений (`max_connections`, по умолчанию 20).
Создайте один клиент на приложение и переиспользуйте его.

`get_models` загружает много карточек с ограниченной параллельностью (пул потоков в
синхронном клиенте, задачи — в асинхронном). Результаты возвращаются в порядке
входа, по одному на ссылку, и содержат модель или ошибку — сбой одной модели
исключения не бросает:

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

После `AuthenticationError`, `PermissionDeniedError` или `QuotaExceededError` ещё
не начатые ссылки получают ту же ошибку без запросов. Каждая модель тратит один
запрос суточной квоты и учитывается в лимите 20 в минуту (вызовы, упёршиеся в
минутный лимит, повторяются автоматически).

## Async

У `AsyncPolyzon` тот же конструктор, методы — корутины, `search_all` —
асинхронный итератор.

```python
import asyncio
from polyzon import AsyncPolyzon


async def main() -> None:
    async with AsyncPolyzon() as client:
        me, page = await asyncio.gather(client.me(), client.search("кот"))
        async for item in client.search_all("ваза", size=50):
            print(item.title)


asyncio.run(main())
```

Закрывайте его через `await client.aclose()` или `async with`.

## Логирование

SDK пишет в стандартный логгер `logging` с именем `"polyzon"` (с `NullHandler`,
поэтому по умолчанию ничего не выводится). На уровне `DEBUG` логируется каждая
попытка: метод, путь и параметры запроса, статус, номер попытки и пауза до повтора —
но никогда заголовки и токен.

```python
import logging

logging.basicConfig()
logging.getLogger("polyzon").setLevel(logging.DEBUG)
```

## Свой HTTP-клиент, сертификаты и тесты

Проверка TLS включена всегда и не отключается. Корпоративный корневой сертификат
подхватывается из `SSL_CERT_FILE` / `SSL_CERT_DIR`; можно и передать свой
`httpx.Client` (`httpx.AsyncClient` для асинхронного клиента) — для своих CA, mTLS,
транспортов и тестов. Переданный клиент остаётся вашим: `close()` его не закрывает.

```python
import ssl
import httpx
from polyzon import Polyzon

context = ssl.create_default_context(cafile="/etc/ssl/corp-ca.pem")
with httpx.Client(verify=context) as http:
    client = Polyzon(http_client=http)
```

Перенаправления не выполняются никогда (3xx — это `APIError`), тело ответа
ограничено 8 МиБ.

## Лимиты и квота

Квоты считаются на аккаунт, а не на токен:

* **20 запросов в минуту** на любые методы (`RateLimitError`, повторяется
  автоматически);
* **200 запросов в сутки UTC** на `search`, страницы `search_all`, `get_model` и
  элементы `get_models` (`QuotaExceededError`, сброс в 00:00 UTC). `me()` и
  `limits()` суточную квоту не тратят.

Сервер списывает запрос до проверки параметров, поэтому даже `BadRequestError`
тратит квоту. Каждый результат несёт суточное окно из заголовков ответа:

```python
page = client.search("дракон")
print(page.meta.request_id)
if page.meta.rate_limit:
    print(page.meta.rate_limit.remaining, page.meta.rate_limit.reset_at)
```

`meta` не участвует в `==` и `repr`. Оба окна возвращает `limits()`.

## Версии

Пакет следует [семантическому версионированию](https://semver.org/lang/ru/).
Несовместимые изменения публичного API (всё, что экспортирует `polyzon`) возможны
только в новой мажорной версии. Новые поля результатов и коды ошибок могут
появляться в минорных версиях. См. [CHANGELOG.md](CHANGELOG.md).

## Требования

Python 3.10 или новее и `httpx>=0.27,<1`.

## Безопасность

Никогда не встраивайте токен в браузерные и мобильные приложения — оттуда его
может извлечь кто угодно. Держите его на сервере, в переменной окружения или
хранилище секретов. Об уязвимостях сообщайте приватно, как описано в
[SECURITY.md](SECURITY.md).

## Ссылки

* Документация API и песочница: https://api.polyzon.org
* Токены: https://polyzon.org/account/
* Исходный код: https://github.com/PolyzonOrg/polyzon-python

## Лицензия

[MIT](LICENSE)
