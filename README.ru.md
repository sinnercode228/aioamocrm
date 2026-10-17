# aioamocrm

[English](https://github.com/sinnercode228/aioamocrm/blob/main/README.md) · Русский

[![CI](https://github.com/sinnercode228/aioamocrm/actions/workflows/ci.yml/badge.svg)](https://github.com/sinnercode228/aioamocrm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aioamocrm.svg)](https://pypi.org/project/aioamocrm/)

Асинхронный клиент amoCRM API v4 для Python. Работает и с Kommo: там тот же API, только на
`kommo.com`. Библиотека берёт на себя то, что каждая интеграция с amoCRM рано или поздно пишет
сама: OAuth2-токены, которые меняются при каждом обновлении, лимит 7 запросов в секунду, повторы,
которые без вашего разрешения не отправят запись дважды, постраничные списки с фильтрами,
дополнительные поля и вебхуки, которые amoCRM присылает в формате form-urlencoded. Python 3.10 и
новее, httpx и pydantic 2, код проходит `mypy --strict`, в пакете есть `py.typed`.

Библиотека выросла из коннектора amoCRM в [Relay](https://github.com/sinnercode228/integration-hub):
там она создаёт сделки с контактами и примечаниями и принимает вебхуки аккаунта.

Версия 0.1.0 пока не опубликована на PyPI. Тесты работают только с замоканным HTTP, ответы в них
написаны по документации amoCRM: живого аккаунта за ними не было. Что именно из этого следует,
перечислено в разделе [Проверено только по документации](#проверено-только-по-документации).

## Зачем ещё один клиент

Вот что было на PyPI в октябре 2026 года. Дата релиза здесь — последняя загрузка любого файла,
по данным PyPI JSON API (`https://pypi.org/pypi/<имя>/json`):

| Пакет | Последний релиз | Что это |
| --- | --- | --- |
| [amocrm](https://pypi.org/project/amocrm/) | 0.7.7, 2017-08-04 | Синхронный, на `requests`; ходит в старый API `private/api/v2` |
| [amoapi](https://pypi.org/project/amoapi/) | 0.0.5, 2021-12-01 | «Simple python wrapper for some parts of the amocrm API», по описанию автора |
| [amocrm-client](https://pypi.org/project/amocrm-client/) | 0.2.5, 2022-09-02 | Асинхронный, API v4; жёстко требует `aiohttp==3.7.4` и `httpx>=0.21,<0.22` |
| [amocrm-api](https://pypi.org/project/amocrm-api/) | 2.6.1, 2023-06-27 | Синхронный, на `requests`, API v4 |
| [amocrm-sdk](https://pypi.org/project/amocrm-sdk/) | 0.4.0, 2026-03-17 | Синхронный, на `requests`, API v4 |
| [amocrm-api-wrapper](https://pypi.org/project/amocrm-api-wrapper/) | 1.0.0, 2026-08-06 | Синхронный и асинхронный клиенты на httpx, API v4, `py.typed`; ответы — обычные `dict` |

Из пакетов, существовавших до 2026 года, amocrm, amocrm-client и amocrm-api последний раз
выходили в 2017, 2022 и 2023 годах, amoapi — в 2021-м. Из двух пакетов 2026 года
amocrm-api-wrapper покрывает больше эндпоинтов, чем эта библиотека (чаты, каталоги,
покупатели, виджеты и другие). Если они нужны вам уже сейчас, посмотрите на него.

Что делает aioamocrm, если говорить о механизмах:

- **Только async.** Один `AmoCRMClient` на аккаунт, работает как `async with`.
- **Типизированные ответы.** Сделки, контакты, компании, примечания, задачи, воронки, описания
  дополнительных полей, пользователи и аккаунт приходят моделями pydantic. Поля, которые amoCRM
  добавит позже, сохраняются, а не ломают разбор. Дополнительные поля читаются и задаются по
  `field_id` или по коду (`"PHONE"`).
- **Refresh-токен не теряется.** При каждом обновлении amoCRM выдаёт новый refresh-токен, а
  старый перестаёт работать. Новая пара сохраняется в ваш `TokenStorage` до того, как ею
  воспользуется хоть один запрос; параллельные запросы делят одно обновление; токены, которые
  сохранил другой процесс, подхватываются из хранилища.
- **По умолчанию без дублей.** `POST` и `PATCH` не отправляются повторно после 429, 5xx или
  таймаута, если вызов не передал `retry=True`. Если пачка упала, исключение говорит, на какой
  части это случилось и что успело сохраниться до неё.
- **Интервал между запросами:** не больше 7 в секунду на клиент, в порядке очереди.
- **Разбор вебхуков** с ключами в скобочной нотации PHP (`leads[status][0][id]=...`) в события.

## Установка

```sh
pip install aioamocrm
```

Пока 0.1.0 не опубликована, ставьте с GitHub:
`pip install "aioamocrm @ git+https://github.com/sinnercode228/aioamocrm"`.

Нужны Python 3.10 или новее, `httpx>=0.25,<1` и `pydantic>=2.5,<3`. CI гоняет тесты на Python
от 3.10 до 3.13 с последними версиями обеих библиотек и на Python 3.10 с самыми старыми из
поддерживаемых (httpx 0.25.0, pydantic 2.5.0).

## Быстрый старт

Проще всего начать с долгосрочного токена: создайте в настройках аккаунта приватную интеграцию
и сгенерируйте в ней долгосрочный токен. Клиент передаёт его как bearer-токен в каждом запросе.
Сохраните это в `quickstart.py`:

<!-- sample: quickstart -->
```python
import asyncio
import os
import time

from aioamocrm import AmoCRMClient, Lead, LongLivedToken


async def main() -> None:
    auth = LongLivedToken(os.environ["AMO_LONG_LIVED_TOKEN"])
    async with AmoCRMClient(subdomain=os.environ["AMO_SUBDOMAIN"], auth=auth) as amo:
        account = await amo.account.get()
        print(f"Аккаунт {account.name!r}, id {account.id}")

        [created] = await amo.leads.create([Lead(name="Заявка с сайта", price=5000)])
        print(f"Создана сделка {created.id}")

        day_ago = int(time.time()) - 24 * 60 * 60
        async for lead in amo.leads.iterate(filter={"updated_at": {"from": day_ago}}):
            print(lead.id, lead.name, lead.price, lead.status_id)


asyncio.run(main())
```

```sh
export AMO_SUBDOMAIN=example              # для example.amocrm.ru
export AMO_LONG_LIVED_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOi...
python quickstart.py
```

Что уходит в amoCRM:

1. `GET /api/v4/account` с заголовком `Authorization: Bearer <токен>`.
2. `POST /api/v4/leads` с телом `[{"name": "Заявка с сайта", "price": 5000}]`. В ответ приходит
   по одному `EntityRef` на сделку, с её новым `id`.
3. `GET /api/v4/leads?page=1&limit=250&filter[updated_at][from]=...`, а затем следующие
   страницы, пока цикл просит ещё. Обход заканчивается на странице без ссылки `next` или на
   пустом ответе (204).

Запросы уходят не чаще 7 в секунду. Для аккаунта Kommo передайте `domain="kommo.com"`, для
международного amoCRM — `domain="amocrm.com"`; `base_url=` принимает любой другой адрес,
например прокси или тестовый сервер. Долгосрочный токен никогда не обновляется: если amoCRM
ответил 401, клиент выбрасывает `AuthError`.

## OAuth2: токены, хранилище и обновление

Интеграция, которую устанавливают в другие аккаунты, работает через OAuth2. В настройках
интеграции вы получаете ID интеграции (`client_id`) и секретный ключ (`client_secret`) и
указываете адрес перенаправления (redirect URI). Когда владелец аккаунта разрешает доступ, amoCRM
перенаправляет его на этот адрес с параметрами `code`, `referer` (адрес аккаунта, например
`example.amocrm.ru`) и `state`. Код одноразовый и действует 20 минут.

<!-- sample: oauth -->
```python
import os

from aioamocrm import AmoCRMClient, JsonFileTokenStorage, OAuth2

oauth = OAuth2(
    client_id=os.environ["AMO_CLIENT_ID"],
    client_secret=os.environ["AMO_CLIENT_SECRET"],
    redirect_uri=os.environ["AMO_REDIRECT_URI"],
    storage=JsonFileTokenStorage("tokens/example.json"),
)


async def save_first_tokens(code: str) -> None:
    """Один раз, с кодом из редиректа, не позже чем через 20 минут."""
    async with AmoCRMClient(subdomain="example", auth=oauth) as amo:
        tokens = await amo.exchange_code(code)
        print(f"Access-токен действует до {tokens.expires_at:.0f} (Unix-время)")


async def print_account() -> None:
    """Все следующие запуски: клиент сам обновляет и сохраняет токены."""
    async with AmoCRMClient(subdomain="example", auth=oauth) as amo:
        account = await amo.account.get()
        print(account.name, account.subdomain)
```

`exchange_code` отправляет на `https://example.amocrm.ru/oauth2/access_token` запрос с
`grant_type=authorization_code`, кодом, ID интеграции, секретным ключом и адресом
перенаправления, а ответ (`access_token`, `refresh_token`, `expires_in`) сохраняет как `TokenSet`
с абсолютным временем `expires_at`.
[`examples/oauth_flow.py`](https://github.com/sinnercode228/aioamocrm/blob/main/examples/oauth_flow.py) —
тот же сценарий в виде консольного скрипта.

Что дальше происходит с токенами:

- **Когда обновляется.** Access-токен живёт 24 часа. `OAuth2` обновляет его за
  `refresh_margin` секунд (по умолчанию 60) до `expires_at`, а также один раз после ответа 401.
  Второй 401 на тот же запрос выбрасывается как `AuthError`. Принудительно обновить токены можно
  вызовом `oauth.refresh(TokenEndpoint(http=amo.http, url=amo.token_url))`, но обычно это не
  нужно.
- **Ротация.** Каждое обновление возвращает новый refresh-токен, старый перестаёт работать. Новая
  пара сохраняется через `storage.save()` раньше, чем ею воспользуется любой запрос. Если
  `save()` упал, запрос, который вызвал обновление, завершается этой же ошибкой, новые токены
  остаются в памяти, и следующее обновление пойдёт с ними, а не с уже потраченными из
  хранилища.
- **Параллельные запросы.** Запросы, которым одновременно понадобилось обновление, ждут на одной
  блокировке: уходит одно обновление, и его результат получают все.
- **Несколько процессов, одно хранилище.** Перед обновлением `OAuth2` перечитывает хранилище.
  Если другой процесс тем временем сохранил более свежие токены, используются они, и
  refresh-токен не тратится. Если два процесса обновляют токены одновременно, amoCRM
  принимает refresh-токен один раз; процесс, которому отказали, перечитывает хранилище и берёт
  токены победителя, если тот уже успел их сохранить. Чтобы закрыть это окно полностью, дайте
  процессам хранилище, у которого `load` и `save` работают под общей блокировкой (например,
  блокировкой строки в базе), или обновляйте токены только из одного процесса.
- **Без повторов.** Запросы к эндпоинту токенов отправляются один раз. Код одноразовый, а
  обновление, ответ на которое потерялся, refresh-токен уже потратило, так что повтор ничем не
  поможет.
- **Срок жизни refresh-токена.** По документации refresh-токен живёт 3 месяца. Если интеграция
  дольше не делала ни одного запроса, владельцу аккаунта придётся разрешить доступ заново.
- **Аккаунт переехал.** Если владелец сменил поддомен аккаунта или аккаунт переехал на другой
  домен, эндпоинт токенов по старому адресу отклоняет обновление с кодом 401 или 404.
  Официальный PHP SDK в этом случае берёт `api_domain` из access-токена (это JWT, поле есть в
  токенах, выданных с 21.08.2024) и спрашивает
  `GET https://{api_domain}/oauth2/account/current/subdomain`, передав refresh-токен в заголовке
  `X-Refresh-Token`. `OAuth2` делает тот же запрос и выбрасывает `AccountMovedError` — это
  `AuthError`, у которого в `domain` лежит новый хост, например `newname.amocrm.ru`.
  Refresh-токен к этому моменту не потрачен: разделите хост так же, как `referer` ниже, создайте
  клиент для него с тем же `OAuth2` и хранилищем и сохраните хост рядом с токенами. Если
  `api_domain` в токене нет или ответ называет тот же хост, отказ приходит обычным `AuthError`.

Параметр `referer` приходит в адресе, который может подделать кто угодно. Разделите его на
поддомен и домен (`subdomain, _, domain = referer.partition(".")`) и передайте оба в
`AmoCRMClient`: клиент принимает только `amocrm.ru`, `amocrm.com` и `kommo.com`, поэтому
поддельный `referer` не заставит его отправить секретный ключ на чужой хост. Никогда не
передавайте `referer` как `base_url`.

### Хранилище токенов

| Хранилище | Где лежат токены |
| --- | --- |
| `MemoryTokenStorage` | В памяти процесса, пропадают при выходе. Используется по умолчанию; подходит для тестов и разовых скриптов. |
| `JsonFileTokenStorage(path)` | В JSON-файле с правами 0600. Каждое сохранение пишет временный файл в том же каталоге, сбрасывает его на диск и переименовывает поверх старого, поэтому после сбоя в файле лежит либо старая пара, либо новая. Файл, в котором не удаётся прочитать токены, даёт `AuthError`. |
| Свой класс | Любой объект с `async def load()` и `async def save(tokens)`: это протокол `TokenStorage`. |

Один экземпляр `OAuth2` и одно хранилище относятся к одному аккаунту. Хранилище для многих
аккаунтов в одном файле SQLite:

<!-- sample: token-storage -->
```python
import asyncio
import json
import sqlite3
from contextlib import closing

from aioamocrm import TokenSet


class SqliteTokenStorage:
    """Токены многих аккаунтов в одной таблице, по строке на аккаунт."""

    def __init__(self, path: str, account: str) -> None:
        self.path = path
        self.account = account
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS amo_tokens (account TEXT PRIMARY KEY, data TEXT)"
            )

    async def load(self) -> TokenSet | None:
        return await asyncio.to_thread(self._load)

    async def save(self, tokens: TokenSet) -> None:
        await asyncio.to_thread(self._save, tokens)

    def _load(self) -> TokenSet | None:
        with closing(sqlite3.connect(self.path)) as db:
            query = "SELECT data FROM amo_tokens WHERE account = ?"
            row = db.execute(query, (self.account,)).fetchone()
        return TokenSet.from_dict(json.loads(row[0])) if row else None

    def _save(self, tokens: TokenSet) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:  # второй "db" делает commit
            db.execute(
                "INSERT INTO amo_tokens VALUES (?, ?)"
                " ON CONFLICT (account) DO UPDATE SET data = excluded.data",
                (self.account, json.dumps(tokens.to_dict())),
            )
```

`TokenSet.to_dict()` содержит токены как есть, так что держите эту таблицу подальше от логов и
бэкапов, которые читает много людей. `repr()` у `TokenSet` токены не показывает.

## Списки: постраничная выборка и фильтры

У этих ресурсов есть `iterate()` — асинхронный итератор по всем элементам — и `get_page()` для
одной страницы: `amo.leads`, `amo.contacts`, `amo.companies`, `amo.tasks`, `amo.users`,
примечания каждого типа сущностей (`amo.leads.notes`...) и описания дополнительных полей каждого
типа (`amo.leads.custom_fields`...). `amo.pipelines.list()` сразу возвращает все воронки с их
этапами.

<!-- sample: pagination -->
```python
async for lead in amo.leads.iterate(
    filter={
        "statuses": [{"pipeline_id": 3177727, "status_id": 142}],  # успешные, в одной воронке
        "updated_at": {"from": 1735689600},
    },
    order={"updated_at": "desc"},
    with_=["contacts"],
):
    main = [contact.id for contact in lead.embedded.contacts if contact.is_main]
    print(lead.id, lead.name, main)

page = await amo.contacts.get_page(2, limit=50, query="+7912")
print(page.number, len(page), page.has_next)
for contact in page:
    print(contact.id, contact.name)
```

Аргументы превращаются в параметры запроса amoCRM, вложенные в скобочной нотации PHP:

| Аргумент | В запросе | Пример |
| --- | --- | --- |
| `filter` | `filter[...]` | `{"id": [1, 2]}` даёт `filter[id][0]=1&filter[id][1]=2` |
| `order` | `order[поле]` | `{"updated_at": "desc"}` даёт `order[updated_at]=desc` |
| `with_` | `with` через запятую | `["contacts", "loss_reason"]` даёт `with=contacts,loss_reason` |
| `query` | `query` | полнотекстовый поиск: `"+7912"` |
| `limit` | `limit` | от 1 до 250, по умолчанию 250; другое значение даёт `ValueError` ещё до запроса |
| `params` | всё остальное | кодируется так же |

`True` и `False` превращаются в `1` и `0`, значения `None` пропускаются. Первый запрос цикла выше
выглядит так (в самом запросе скобки закодированы через `%5B` и `%5D`):

```text
/api/v4/leads?page=1&limit=250&filter[statuses][0][pipeline_id]=3177727
    &filter[statuses][0][status_id]=142&filter[updated_at][from]=1735689600
    &order[updated_at]=desc&with=contacts
```

Какие фильтры принимает эндпоинт, решает amoCRM; клиент кодирует всё, что вы передали.

Как `iterate()` обходит страницы:

1. Запрашивает `page=1&limit=250` с вашими параметрами и отдаёт элементы по одному. Следующая
   страница запрашивается, только когда цикл попросит элемент после последнего.
2. Останавливается на ответе 204 (так amoCRM отвечает на пустой список), на пустой странице и
   на странице без `_links.next`. Если элементов на странице меньше `limit`, но ссылка `next`
   есть, обход идёт дальше: эндпоинт, который отдаёт страницы меньше запрошенного, всё равно
   будет прочитан до конца.
3. Идёт по `_links.next.href`, только если ссылка ведёт в `/api/v4/` этого же аккаунта и
   указывает на следующую страницу, и добавляет обратно те ваши параметры, которые ссылка
   потеряла (`with`, `limit`). Иначе сам запрашивает страницу с этим номером и теми же
   параметрами. Ссылка `next`, которая не двигается вперёд, не заставит его зациклиться или
   пропустить страницы, а ссылка на другой хост никогда не получит токен.

Страницы нумерованные, поэтому сущность, изменившаяся во время обхода, может переехать на уже
прочитанную страницу или на ещё не прочитанную. Для синхронизации фильтруйте по `updated_at` от
последней контрольной точки, сортируйте по `updated_at` по возрастанию и убирайте повторы по
`id`.

`get_page()` возвращает `Page` с полями `items`, `number`, `next_url` и `has_next`. Итерация по
странице идёт по её элементам, `len(page)` считает их. Ответ 204 даёт пустую страницу.

### Эндпоинты без своего ресурса

`amo.request()` отправляет любой запрос к API с той же авторизацией, интервалами, повторами и
разбором ошибок и возвращает разобранный JSON или `None` для пустого ответа:

<!-- sample: request -->
```python
data = await amo.request("GET", "events", params={"limit": 100})
events = data["_embedded"]["events"] if data else []  # None: ответ 204, событий нет
for event in events:
    print(event["type"], event["entity_id"])
```

`path` — путь относительно `/api/v4/` или абсолютный адрес внутри `/api/v4/` этого аккаунта,
как в `_links`; любой другой абсолютный адрес даёт `ValueError`. `params` кодируется так же, как
`filter` выше, `json=` — тело запроса, а `retry=` работает, как описано в разделе
[Ограничение частоты и повторы](#ограничение-частоты-и-повторы).

## Сделка с контактом и компанией одним запросом

`POST /api/v4/leads/complex` создаёт сделку вместе с новым контактом и новой компанией за один
запрос. Положите их в `embedded`, не больше одного контакта и одной компании на сделку:

<!-- sample: complex -->
```python
from aioamocrm import Company, Contact, Embedded, Lead, Tag

contact = Contact(name="Иван Петров")
contact.set_field("PHONE", "+79123456789", enum_code="WORK")
contact.set_field("EMAIL", "ivan@example.com", enum_code="WORK")

lead = Lead(
    name="Заказ с сайта № 1042",
    price=5000,
    pipeline_id=3177727,
    embedded=Embedded(
        contacts=[contact],
        companies=[Company(name="ООО «Ромашка»")],
        tags=[Tag(name="сайт")],
    ),
)
[result] = await amo.leads.create_complex([lead])
print(result.id, result.contact_id, result.company_id, result.merged)
```

В теле запроса уходит `to_api()` сделки: только заданные поля, а в `_embedded` — контакт,
компания и тег. У каждого `ComplexLeadResult` есть `id`, `contact_id`, `company_id`,
`request_id` и `merged`: последнее говорит, склеил ли amoCRM контакт с уже существующим, которого
нашёл контроль дублей. В ответе есть место только для одного контакта и одной компании на
сделку, поэтому сделка с двумя контактами или двумя компаниями даёт `ValueError` ещё до отправки;
остальные контакты привяжите потом через `link()`. Документация разрешает не больше 50 сделок на запрос,
и это значение `batch_size` по умолчанию: список из 120 сделок уйдёт тремя запросами по 50, 50
и 20.

Чтобы связать уже существующие сущности, например привязать к сделке имеющийся контакт,
используйте `link()`:

<!-- sample: link -->
```python
await amo.leads.link(19619, "contacts", 15009433, metadata={"is_main": True})

for link in await amo.leads.links(19619, filter={"to_entity_type": "contacts"}):
    print(link.to_entity_type, link.to_entity_id, link.metadata)
```

`metadata` уходит как есть: `{"is_main": True}` делает контакт основным в сделке,
`{"catalog_id": 1, "quantity": 2}` привязывает элемент каталога. `unlink()` принимает те же
аргументы.

### Пачки и частичные сбои

`create()`, `update()`, `create_complex()`, `notes.create()`, `tasks.create()` и
`tasks.update()` принимают сколько угодно элементов и отправляют их по `batch_size` за запрос (по
умолчанию 50, максимум 250). Если запрос с очередной частью не прошёл, следующие части не
отправляются, а исключение говорит, где всё остановилось:

<!-- sample: batch-errors -->
```python
from aioamocrm import Lead, ValidationError

leads = [Lead(name=f"Импорт {n}", price=100 * n) for n in range(120)]
try:
    await amo.leads.create(leads)  # 3 запроса: 50, 50 и 20 сделок
except ValidationError as exc:
    offset = exc.batch_offset or 0  # индекс первой сделки отклонённой части во входном списке
    print(len(exc.completed), offset)  # что сохранили предыдущие части и где всё встало
    for issue in exc.errors:
        # request_id считается от начала части, если вы не задали свой
        print(offset + int(issue.request_id or 0), issue.path, issue.code, issue.detail)
```

Любая ошибка `AmoCRMError` из пакетной записи несёт `batch_offset` и `completed` (`EntityRef` или
`ComplexLeadResult` частей, сохранённых до неё). Что делать дальше, зависит от ошибки:

- `ValidationError` (400): amoCRM отклонил запрос. Исправьте элементы из `errors` и отправьте
  `leads[exc.batch_offset:]` ещё раз.
- `NetworkError` после отправки запроса, `ServerError` или таймаут: неизвестно, сохранил ли
  amoCRM эту часть. Прежде чем отправлять её снова, поищите сделки (например, по полю, которое
  заполняет только ваш импорт).
- `AmoCRMError` со `status_code` 200: часть сохранена, но ответ не удалось разобрать. Исходный
  ответ лежит в `body`; эту часть повторно не отправляйте.

## Дополнительные поля

Значения дополнительных полей лежат в `custom_fields_values`. Методы ниже находят поле по
`field_id` (`int`) или по `field_code` (`str`, регистр не важен):

<!-- sample: fields-read -->
```pycon
>>> from aioamocrm import Contact
>>> contact = Contact.model_validate(
...     {
...         "id": 3,
...         "name": "Иван Петров",
...         "custom_fields_values": [
...             {
...                 "field_id": 3,
...                 "field_code": "PHONE",
...                 "field_type": "multitext",
...                 "values": [
...                     {"value": "+79123456789", "enum_id": 1, "enum_code": "WORK"},
...                     {"value": "+79990001122", "enum_id": 3, "enum_code": "MOB"},
...                 ],
...             },
...             {
...                 "field_id": 1234,
...                 "field_name": "Источник",
...                 "field_code": None,
...                 "field_type": "select",
...                 "values": [{"value": "Сайт", "enum_id": 4567}],
...             },
...         ],
...     }
... )
>>> contact.field_value("PHONE")
'+79123456789'
>>> contact.field_value("phone", enum_code="MOB")
'+79990001122'
>>> contact.field_values("PHONE")
['+79123456789', '+79990001122']
>>> contact.field_value(1234)
'Сайт'
>>> contact.field_value("EMAIL", "нет почты")
'нет почты'
```

`field(key)` возвращает запись `CustomFieldValues` целиком или `None`. Эти методы есть у сделок,
контактов и компаний, а `field_value()` и `field_values()` — ещё и у событий вебхуков.

`set_field()` заменяет значения поля или добавляет поле: по `field_id`, если ключ `int`, и по
`field_code`, если ключ `str`. `to_api()` собирает тело запроса из заданных полей:

<!-- sample: fields-write -->
```pycon
>>> from aioamocrm import Contact
>>> update = Contact(id=3)
>>> update.set_field("POSITION", "Закупщик")
>>> update.set_field(1234, "Реклама")
>>> update.to_api()
{'id': 3, 'custom_fields_values': [{'field_code': 'POSITION', 'values': [{'value': 'Закупщик'}]},
 {'field_id': 1234, 'values': [{'value': 'Реклама'}]}]}
```

Для поля-списка amoCRM принимает текст варианта в `value` или его `enum_id`. Для значений с
`enum_code` (`"WORK"`, `"MOB"` у телефонов) код передаётся именованным аргументом:
`set_field("PHONE", number, enum_code="WORK")`.

`set_field()` заменяет значения целиком, поэтому, чтобы добавить телефон, старые номера нужно
отправить вместе с новым:

<!-- sample: fields-update -->
```python
from aioamocrm import Contact, CustomFieldValue

contact = await amo.contacts.get(3)
entry = contact.field("PHONE")
phones = list(entry.values or []) if entry else []  # сохраняет value, enum_id и enum_code
phones.append(CustomFieldValue(value="+79001112233", enum_code="WORK"))

update = Contact(id=contact.id)  # только то, что меняется
update.set_field("PHONE", *phones)
await amo.contacts.update([update])
```

Отправляйте частичные модели, как `update` выше. Модель, полученная из API, отправит обратно все
поля, с которыми её прочитали; `to_api()` убирает только то, что amoCRM вычисляет сам (`_links`,
`account_id`, `is_deleted`, `closest_task_at`, а также `field_name` и `field_type` у значений
полей). Чтобы точно управлять телом запроса, например отправить `null`, передайте `dict` вместо
модели.

Описания полей с id вариантов списка:

<!-- sample: custom-fields -->
```python
async for field in amo.leads.custom_fields.iterate():
    print(field.id, field.type, field.code, field.name)

source = await amo.contacts.custom_fields.get(1234)
print(source.enum_id("Реклама"))  # id варианта или None
```

О моделях в целом: все поля необязательные, потому что состав ответа зависит от эндпоинта,
параметра `with` и аккаунта. Поля, которых нет в модели, сохраняются в `model_extra`. Модели
также принимают то, что выдаёт `json_encode` в PHP: `[]` вместо пустого объекта, `null` вместо
пустого списка и числа в текстовых полях (сделка с названием `2024`). `Lead.price` принимает
не только `int`, но и `float` — для аккаунтов, где у валюты есть копейки: `Lead(price=99.5)`
отправит `"price": 99.5`.

## Примечания и задачи

<!-- sample: notes-tasks -->
```python
import time

from aioamocrm import Note, Task

lead_id = 19619
await amo.leads.notes.create([Note.common(lead_id, "Просит перезвонить после 18:00")])
async for note in amo.leads.notes.iterate(entity_id=lead_id):
    print(note.created_at, note.note_type, note.text)

call = Task(
    text="Перезвонить клиенту",
    entity_type="leads",
    entity_id=lead_id,
    task_type_id=1,  # 1 — звонок, 2 — встреча
    complete_till=int(time.time()) + 3600,
)
await amo.tasks.create([call])
open_tasks = {"is_completed": False, "entity_type": "leads", "entity_id": [lead_id]}
async for task in amo.tasks.iterate(filter=open_tasks):
    print(task.id, task.text, task.complete_till)

await amo.tasks.complete(7087, result="Договорились о встрече в понедельник")
```

Примечания привязаны к типу сущностей: `amo.leads.notes`, `amo.contacts.notes`,
`amo.companies.notes`. `Note.common(entity_id, text)` — обычное текстовое примечание; другие
типы создаются как `Note(entity_id=..., note_type="...", params={...})` с теми `params`, которые
документация описывает для этого типа. `note.text` читает `params.text`.
`iterate(entity_id=...)` перебирает примечания одной сущности, `iterate()` без него — примечания
всех сущностей этого типа, а `get(note_id, entity_id=...)` возвращает одно примечание. Оно
запрашивается через свою сущность, поэтому нужны оба id.

Задаче нужны `text` и `complete_till` (Unix-время). `tasks.update()` принимает задачи с их `id`,
а `tasks.complete(task_id, result=...)` отправляет `PATCH /api/v4/tasks/{id}` с `is_completed` и
текстом результата.

## Вебхуки

Вебхуки аккаунта amoCRM присылает телом `application/x-www-form-urlencoded` с ключами в
скобочной нотации PHP, и в одном запросе может прийти несколько сущностей и действий.
`parse_webhook()` принимает тело как есть и не зависит от фреймворка:

<!-- sample: webhook-parse -->
```pycon
>>> from urllib.parse import urlencode
>>> from aioamocrm import parse_webhook
>>> body = urlencode(
...     {
...         "leads[status][0][id]": "25399013",
...         "leads[status][0][status_id]": "142",
...         "leads[status][0][pipeline_id]": "1300",
...         "leads[status][0][old_status_id]": "32392156",
...         "contacts[update][0][id]": "15009433",
...         "contacts[update][0][custom_fields][0][id]": "3",
...         "contacts[update][0][custom_fields][0][code]": "PHONE",
...         "contacts[update][0][custom_fields][0][values][0][value]": "+79123456789",
...         "account[subdomain]": "example",
...         "account[id]": "29085955",
...     }
... )
>>> webhook = parse_webhook(body)
>>> webhook.subdomain, webhook.account_id
('example', 29085955)
>>> [status, contact] = webhook.events
>>> status.entity, status.action, status.id
('leads', 'status', 25399013)
>>> status.payload
{'id': '25399013', 'status_id': '142', 'pipeline_id': '1300', 'old_status_id': '32392156'}
>>> contact.entity, contact.action, contact.field_value("PHONE")
('contacts', 'update', '+79123456789')
```

У каждого `WebhookEvent` есть `entity` (`"leads"`, `"contacts"`, `"companies"`...), `action`
(`"add"`, `"update"`, `"delete"`, `"restore"`, `"status"`, `"responsible"`, `"note"`...) и
`payload` — поля сущности ровно в том виде, в каком они пришли. Все значения — строки, так что
нужное приводите к типам сами (`event.id` уже делает это для id). У действия `note` само
примечание лежит в `payload["note"]`, а `element_id` в нём указывает на сущность.
`webhook.data` — всё тело в виде вложенных словарей и списков.

**Эти вебхуки не подписаны.** amoCRM не присылает ни подписи, ни секретного заголовка, поэтому
отправить событие на ваш адрес может любой, кто этот адрес узнал. Отсюда три правила:

1. Добавьте в адрес длинный случайный секрет и сравнивайте его через `hmac.compare_digest`.
2. Считайте содержимое вебхука уведомлением. Перед любым действием, которое что-то значит
   (деньги, смена статуса в другой системе), получите сущность по id через API и опирайтесь на
   то, что ответил API.
3. Отвечайте 2xx сразу, а работу делайте потом. amoCRM повторяет вебхуки, на которые адрес
   ответил ошибкой или медленно, и отключает адреса, которые делают так постоянно.

С FastAPI:

<!-- sample: webhook-fastapi -->
```python
import hmac
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from aioamocrm import Webhook, parse_webhook

SECRET = os.environ["AMO_WEBHOOK_SECRET"].encode()
app = FastAPI()


def handle(webhook: Webhook) -> None:
    for event in webhook.events:
        if (event.entity, event.action) == ("leads", "status"):
            old, new = event.payload.get("old_status_id"), event.payload.get("status_id")
            print(f"сделка {event.id}: статус {old} -> {new}")


@app.post("/webhooks/amocrm/{secret}")
async def amocrm_webhook(
    secret: str, request: Request, background: BackgroundTasks
) -> dict[str, int]:
    if not hmac.compare_digest(secret.encode(), SECRET):
        raise HTTPException(status_code=404)
    try:
        webhook = parse_webhook(await request.body())
    except ValueError:
        raise HTTPException(status_code=400, detail="malformed webhook body") from None
    background.add_task(handle, webhook)  # выполнится после отправки ответа
    return {"events": len(webhook.events)}
```

```sh
export AMO_WEBHOOK_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn app:app
# в настройках вебхуков аккаунта укажите https://ваш-хост/webhooks/amocrm/<секрет>
```

Фоновые задачи FastAPI выполняются в том же процессе, поэтому события пропадут, если он
остановится раньше, чем их обработает. Если это важно, записывайте разобранные события в
очередь или таблицу прямо в обработчике запроса и обрабатывайте их отдельным воркером; Relay
делает именно так.
[`examples/webhook_fastapi.py`](https://github.com/sinnercode228/aioamocrm/blob/main/examples/webhook_fastapi.py) —
более длинная версия с обработкой смены статуса, изменения контакта и примечаний.

`parse_webhook()` выбрасывает `ValueError`, если тело не в UTF-8, в нём больше `max_fields` полей
(по умолчанию 10 000) или вложенность глубже 32 уровней. Тело из 10 000 полей разбирается
примерно за 0,05 с. В aiohttp тело запроса — это `await request.read()`, в Django —
`request.body`.

## Ограничение частоты и повторы

### Интервал между запросами

Документация разрешает интеграции 7 запросов в секунду, а аккаунту — 50 в секунду на все его
интеграции. На превышение amoCRM отвечает HTTP 429, а за повторяющийся флуд блокирует IP
(HTTP 403). Поэтому каждый клиент отправляет запросы не чаще 7 в секунду:

- Запросы встают в очередь в порядке поступления. Каждый ждёт, пока с момента реальной отправки
  предыдущего пройдёт `1 / rate` секунд. Заблокированный цикл событий задерживает очередь, но
  потом не выпускает пачку запросов разом, а время простоя не копится в запас. Замер на
  настоящих часах: 29 одновременных запросов при `rate=7` дали не больше 7 отправок в любом
  окне длиной в секунду, в том числе сразу после остановки цикла событий на 1,5 с.
- `rate_limit=` принимает число запросов в секунду, `RateLimiter`, общий для нескольких клиентов
  в одном цикле событий (`shared = RateLimiter(7)`, затем `rate_limit=shared` у каждого), или
  `None` — без интервалов.
- Лимитер считает только запросы своего процесса. Два процесса одной интеграции по 7 в секунду
  дают 14; разделите лимит между ними.

<!-- sample: limits -->
```python
import asyncio
import os

from aioamocrm import AmoCRMClient, LongLivedToken, RetryPolicy

# Одну интеграцию делят два воркера, поэтому каждому половина от 7 в секунду.
async with AmoCRMClient(
    subdomain="example",
    auth=LongLivedToken(os.environ["AMO_LONG_LIVED_TOKEN"]),
    rate_limit=3.5,
    retry=RetryPolicy(max_attempts=6, backoff_max=10.0, max_retry_after=30.0),
) as worker:
    found = await asyncio.gather(*(worker.leads.get(i) for i in (19619, 19620, 19621)))
print([lead.name for lead in found])
```

### Что повторяется

| Что случилось | `GET`, `HEAD`, `OPTIONS` | `POST`, `PATCH` | `POST`, `PATCH` с `retry=True` |
| --- | --- | --- | --- |
| Не удалось подключиться (`ConnectError`, `ConnectTimeout`, `PoolTimeout`): ничего не отправлено | повтор | повтор | повтор |
| 429 Too Many Requests | повтор | `RateLimitError` | повтор |
| 500, 502, 503, 504 | повтор | `ServerError` | повтор |
| Таймаут или обрыв соединения после отправки запроса | повтор | `NetworkError` | повтор |
| 401 при `OAuth2` | одно обновление токенов и ещё одна попытка | так же | так же |
| 401 при `LongLivedToken`, любой другой 4xx, любой 3xx | ошибка | ошибка | ошибка |

Пауза перед следующей попыткой берётся из заголовка `Retry-After`, если он есть в ответе (в
секундах или датой HTTP). Иначе это экспоненциальная задержка со случайным разбросом: попытка
*n* ждёт случайное время от половины до целого
`min(backoff_max, backoff_base * 2 ** (n - 1))`, то есть с настройками по умолчанию 0,25–0,5 с,
затем 0,5–1 с, затем 1–2 с. `max_attempts` считает все попытки вместе с первой (по умолчанию 4).
`Retry-After` длиннее `max_retry_after` (60 с) клиент не пережидает: ошибка выбрасывается сразу,
а `RateLimitError.retry_after` показывает, сколько просил подождать amoCRM.

**Почему `POST` по умолчанию не повторяется.** В API amoCRM нет ключей идемпотентности.
`POST /api/v4/leads`, который упал по таймауту, мог уже создать сделки, и повтор создаст их
второй раз; дубли сделок и контактов потом склеивают вручную. Ответ 429, скорее всего, значит,
что amoCRM ничего не сделал, но документация этого не обещает, поэтому запись, получившая 429,
тоже не отправляется повторно:

<!-- sample: retry-write -->
```python
from aioamocrm import Lead, RateLimitError

try:
    await amo.leads.create([Lead(name="Заявка с сайта")])
except RateLimitError as exc:
    # Повторно не отправлено: amoCRM не сообщает, применил ли он запись, на которую ответил 429.
    print(f"429; Retry-After: {exc.retry_after} с; сохранено до этого: {len(exc.completed)}")
```

Передайте `retry=True` в запись, если дубль допустим или вы проверяете его перед повтором:
`await amo.leads.create(leads, retry=True)`. Этот флаг включает повторы сразу для 429, 5xx и
таймаутов. В `amo.request()` значение `retry=False` выключает повторы и для `GET`. Ошибки
подключения повторяются всегда: до amoCRM в этом случае ничего не дошло.

Поля `RetryPolicy`: `max_attempts`, `backoff_base`, `backoff_max`, `max_retry_after`,
`retry_statuses` (коды 5xx, которые повторяются помимо 429) и `idempotent_methods`. Передаётся
как `AmoCRMClient(retry=RetryPolicy(...))`.

## Ошибки

```text
AmoCRMError                 любой ответ вне 2xx, включая 3xx; базовый класс для всех ниже
├── AuthError               401 или токены, которые не удалось получить или обновить
│   └── AccountMovedError   обновление отклонено, потому что аккаунт переехал; хост в .domain
├── PaymentRequiredError    402: аккаунт не оплачен
├── ForbiddenError          403: нет прав, аккаунт заблокирован или IP заблокирован за флуд
├── NotFoundError           404 или 204 на запрос сущности по id
├── ValidationError         400 или 422; в .errors все ValidationIssue
├── RateLimitError          429; в .retry_after значение Retry-After в секундах, если оно было
├── ServerError             5xx после всех повторов
└── NetworkError            нет HTTP-ответа: не удалось подключиться, таймаут, обрыв
```

<!-- sample: errors -->
```python
from aioamocrm import AmoCRMError, NotFoundError

for lead_id in (1, 2):
    try:
        lead = await amo.leads.get(lead_id)
    except NotFoundError:
        print(lead_id, "нет такой сделки")
    except AmoCRMError as exc:
        print(exc.status_code, exc.method, exc.url, exc.title, exc.detail)
    else:
        print(lead_id, lead.name)
```

У каждой ошибки есть `status_code` (`None`, если ответа не было), `title` и `detail` из тела
ошибки amoCRM, `body` (разобранный ответ), `method`, `url`, а у пакетных записей ещё
`batch_offset` и `completed`. Сообщение выглядит так:
`HTTP 402 Payment Required: Account is not paid (GET /api/v4/leads/2)`. Если в теле есть ещё и
`hint`, как в ошибках эндпоинта токенов, он идёт в скобках после `detail`:
`The refresh token is invalid. (Token has been revoked)`. Ни сообщение, ни `url` не содержат
строку запроса, в которой могут быть поисковые фразы, и ни одна ошибка не содержит токен.

У `ValidationIssue` есть `request_id`, `code`, `path` (например,
`custom_fields_values.0.field_id`) и `detail`.

Редиректы клиент не выполняет никогда, даже с клиентом из `http=`, созданным с
`follow_redirects=True`: после 301, 302 или 303 запись ушла бы повторно как `GET` без тела, а
после 307 или 308 тело (у эндпоинта токенов это секретный ключ и refresh-токен) отправилось бы
туда, куда указывает редирект. Ответ 3xx выбрасывается как `AmoCRMError` с адресом, куда он вёл
(без строки запроса). Обычно это значит, что неверен поддомен или домен либо аккаунт переехал.

Неверные аргументы падают ещё до запроса: `ValueError` на `limit` вне диапазона от 1 до 250, на
пустой токен и на абсолютный адрес за пределами аккаунта, `TypeError` на id, который не `int`.
Ошибки вашего `TokenStorage` доходят до вас без изменений.

### Логи

Модули пишут в `logging.getLogger("aioamocrm")` и его дочерние логгеры. На уровне DEBUG — одна
строка на запрос (`GET /api/v4/leads -> 200`, путь без строки запроса), на INFO — обмен кода на
токены, обновления токенов и повтор после 401, на WARNING — каждый повтор с номером попытки и
паузой. Токены, секретный ключ, строки запросов и тела в лог не попадают никогда. Сам httpx
пишет каждый запрос в свой логгер `httpx` на уровне INFO, с полным адресом вместе со строкой
запроса, но без заголовков.

## Проверено только по документации

За тестами нет аккаунта amoCRM. Все запросы и ответы замоканы через respx, а тела ответов
написаны по [документации amoCRM API v4](https://www.amocrm.ru/developers/content/crm_platform/api-reference).
Пока писалась эта версия, сайт документации был недоступен, поэтому форматы взяты из
документации в том виде, в каком я её знал, и ни один ответ не записан с живого аккаунта. Вот
что держится только на документации:

**Лимиты и транспорт**

- 7 запросов в секунду на интеграцию и 50 на аккаунт; 429 при превышении, затем 403 и блокировка
  IP за повторяющийся флуд. Отсюда значение `rate_limit=7` по умолчанию.
- `Retry-After` в ответе 429: документация его не обещает. Если заголовок есть, клиент его
  соблюдает.
- Применил ли amoCRM запись, на которую ответил 429: документация молчит. Поэтому без
  `retry=True` записи после 429 не повторяются.
- Ответ 3xx для переехавшего аккаунта (например, на `kommo.com`) — предположение; клиент
  выбрасывает любой 3xx, который получил.
- Аккаунты Kommo на `kommo.com` отдают тот же API v4.

**Списки**

- `204 No Content` для пустого списка и для сущности, запрошенной по несуществующему id (404
  обрабатывается так же).
- `page` и `limit` (не больше 250), `_page` и `_links.next.href` в ответах со списками.
- Имена фильтров из этого README (`filter[statuses][0][pipeline_id]`,
  `filter[updated_at][from]`, `filter[is_completed]`, `filter[entity_type]`,
  `filter[entity_id]`) и то, что `with=contacts` добавляет id контактов с признаком `is_main`.

**Запись**

- Не больше 50 сделок на `POST /leads/complex`, до 250 элементов в остальных запросах записи.
- Ответы на запись: `_embedded.<сущность>` с `id` и `request_id` для каждого элемента; у
  `/leads/complex` — JSON-массив из `id`, `contact_id`, `company_id`, `request_id` (список) и
  `merged`.
- `request_id` возвращается без изменений (отправленное число возвращается числом), а если его не
  задали, равен позиции элемента в запросе.
- Тела ошибок: `title`, `type`, `status`, `detail`; при 400 — `validation-errors` с `request_id`
  и `errors` из `code`, `path` и `detail` для каждого отклонённого элемента, причём отклоняется
  весь запрос. 402 для неоплаченного аккаунта.
- Связи: `POST /{сущность}/{id}/link` с `to_entity_id`, `to_entity_type` и `metadata`
  (`is_main`, `catalog_id`, `quantity`), в ответе `_embedded.links`.
- Выполнение задачи через `PATCH /tasks/{id}` с `is_completed` и `result.text`.
- Поле-список принимает текст варианта в `value` или его `enum_id`.

**OAuth2**

- Эндпоинт `/oauth2/access_token` на домене аккаунта, JSON-тела запросов к нему и поля ответа
  `token_type`, `expires_in`, `access_token`, `refresh_token`.
- Access-токен на 24 часа, одноразовый код авторизации на 20 минут, refresh-токен на 3 месяца и
  смена refresh-токена при каждом обновлении.
- Долгосрочный токен передаётся как bearer-токен, так же как access-токен.

**Данные**

- Привычки `json_encode` из PHP: `[]` вместо пустого объекта, `"result": []` у задач без
  результата, числа в текстовых полях.
- Статус 142 — «Успешно реализовано», 143 — «Закрыто и не реализовано» в любой воронке; тип
  задачи 1 — звонок, 2 — встреча; коды полей `PHONE`, `EMAIL` и `POSITION` и коды значений вроде
  `WORK` и `MOB`.

**Вебхуки**

- Формат тела, в том числе примечания в `leads[note][0][note][...]` и поля-даты в виде
  `values][0]=` без `[value]`; все значения — строки.
- Отсутствие у вебхуков аккаунта какой-либо подписи.
- Повторная отправка и отключение адресов, которые постоянно отвечают ошибкой или медленно.

Что проверено здесь, без amoCRM: запросы, которые строит клиент (пути, кодирование параметров,
тела), каждое решение о повторе из таблицы выше, интервалы на настоящих часах, постраничный
обход со ссылками `next`, которые теряют параметры или стоят на месте, обновление токенов при
упавшем сохранении и при двух процессах с общим хранилищем, разбор вебхуков из 10 000 полей, а
также то, что ни access-токен, ни refresh-токен, ни секретный ключ, ни долгосрочный токен не
появляются в логах на уровне DEBUG, в `repr()`, в сообщениях об ошибках и в трейсбеках.

Если вы запустите библиотеку на живом аккаунте и какой-то ответ окажется другим, откройте,
пожалуйста, issue с запросом и ответом, убрав из них токены.

## Ограничения и планы

Ограничения версии 0.1.0:

- Типизированные ресурсы есть для сделок, контактов, компаний, примечаний, задач, воронок и
  этапов, описаний дополнительных полей, пользователей и аккаунта. Покупатели, каталоги,
  неразобранное, события, звонки, источники, чаты, подписки на вебхуки, виджеты и файлы доступны
  через `amo.request()` и возвращаются обычным JSON. Методов удаления нет.
- Только async, синхронного клиента нет.
- Лимитер выдерживает интервалы внутри одного процесса и одного цикла событий. Между процессами
  и потоками он ничего не согласует.
- Гонка двух процессов, обновляющих токены одновременно, сужена, но не закрыта: если ответ на
  отклонённое обновление пришёл раньше, чем победитель сохранил свои токены, этот процесс один
  раз выбросит `AuthError`, а следующий его запрос подхватит сохранённые токены. Хранилище с
  общей блокировкой закрывает гонку.
- `retry=True` включает повторы сразу для 429, 5xx и таймаутов; отдельного переключателя только
  для 429 нет.
- После `AccountMovedError` клиент остаётся на старом хосте: для нового домена аккаунта нужно
  создать новый клиент.
- `Retry-After` в устаревшем формате даты asctime игнорируется, и клиент ждёт по своей
  экспоненциальной схеме. Секунды и даты в форматах RFC 1123 и RFC 850 работают.
- Содержимое вебхуков — строки во вложенных словарях, без отдельных моделей для каждой
  сущности.

Что дальше, по порядку:

1. Записать ответы с живого аккаунта, заменить ими тестовые ответы, написанные по документации,
   и исправить всё, что разойдётся.
2. Отдельный переключатель повтора записей только после 429 — новым полем `RetryPolicy` со
   значением по умолчанию, чтобы существующий код вёл себя как раньше.
3. Ресурсы для событий, неразобранного, покупателей и каталогов.
4. Подписки на вебхуки через `/api/v4/webhooks`.
5. Пример хранилища токенов с общей блокировкой (блокировка строки в PostgreSQL или Redis) для
   нескольких процессов.
6. Переводить живой клиент на новый хост аккаунта после `AccountMovedError` и обновлять токены
   уже там, как это делает официальный PHP SDK, а хост хранить в `TokenSet`.

## Как тестировать свой код

Клиент отправляет всё через httpx, поэтому его мокает [respx](https://lundberg.github.io/respx/).
С pytest и pytest-asyncio (`asyncio_mode = "auto"`):

<!-- sample: testing -->
```python
import pytest
import respx

from aioamocrm import AmoCRMClient, LongLivedToken, RetryPolicy


async def won_total(amo: AmoCRMClient, pipeline_id: int) -> int:
    """Тестируемый код: сумма успешных сделок в одной воронке."""
    won = {"statuses": [{"pipeline_id": pipeline_id, "status_id": 142}]}
    return sum([lead.price or 0 async for lead in amo.leads.iterate(filter=won)])


@pytest.mark.respx(base_url="https://example.amocrm.ru")
async def test_won_total(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/api/v4/leads").respond(
        200,
        json={
            "_page": 1,
            "_links": {"self": {"href": "https://example.amocrm.ru/api/v4/leads?page=1"}},
            "_embedded": {"leads": [{"id": 1, "price": 1000}, {"id": 2, "price": None}]},
        },
    )
    async with AmoCRMClient(
        subdomain="example",
        auth=LongLivedToken("test-token"),
        rate_limit=None,  # без интервалов между замоканными запросами
        retry=RetryPolicy(max_attempts=1),  # замоканный 503 сразу даёт ошибку, без пауз
    ) as amo:
        assert await won_total(amo, 3177727) == 1000
    params = route.calls.last.request.url.params
    assert params["filter[statuses][0][status_id]"] == "142"
```

Что ещё пригодится:

- `respond(204)` — так amoCRM отвечает на пустой список; `respond(429, headers={"Retry-After":
  "1"})` проверяет, как ваш код обрабатывает `RateLimitError`.
- Для кода с OAuth2 начинайте с известных токенов и замороженных часов:
  `OAuth2(..., storage=MemoryTokenStorage(TokenSet(access_token="a", refresh_token="r",
  expires_at=now + 3600)), clock=lambda: now)`, и мокайте `POST /oauth2/access_token`.
- `AmoCRMClient(http=httpx.AsyncClient(transport=...))` принимает ваш транспорт, например
  `httpx.MockTransport`, если он вам удобнее respx.
- Обработчикам вебхуков HTTP не нужен вовсе: соберите тело через `urlencode` и вызовите
  `parse_webhook()`, как в разделе [Вебхуки](#вебхуки).
- В [`tests/payloads.py`](https://github.com/sinnercode228/aioamocrm/blob/main/tests/payloads.py)
  есть ответы для каждого ресурса в формате примеров из документации (MIT, копируйте).

## Релиз

Версии следуют [семантическому версионированию](https://semver.org/lang/ru/), у каждого релиза
есть запись в [CHANGELOG.md](https://github.com/sinnercode228/aioamocrm/blob/main/CHANGELOG.md).

1. Укажите версию в `pyproject.toml` и в `__version__` в `src/aioamocrm/_version.py`;
   `tests/test_version.py` упадёт, если они разойдутся.
2. В `CHANGELOG.md` замените `Unreleased` в заголовке версии на дату релиза.
3. Сделайте коммит и отправьте тег: `git tag v0.1.0 && git push origin v0.1.0`.

[`release.yml`](https://github.com/sinnercode228/aioamocrm/blob/main/.github/workflows/release.yml)
проверяет, что тег совпадает с версией в `pyproject.toml`, прогоняет тесты, собирает sdist и
wheel, запускает `twine check --strict` и загружает их из задачи в окружении GitHub `pypi`.
Загрузка идёт через trusted publishing на PyPI: GitHub выдаёт этой задаче короткоживущий
OIDC-токен, и токен PyPI нигде не хранится.

Владелец настраивает это один раз:

1. На PyPI откройте <https://pypi.org/manage/account/publishing/> и добавьте ожидающего
   издателя (pending publisher): имя проекта `aioamocrm`, владелец `sinnercode228`, репозиторий
   `aioamocrm`, workflow `release.yml`, окружение `pypi`. Издатель должен быть именно
   ожидающим, потому что до первой загрузки проекта на PyPI нет; первая загрузка делает его
   издателем проекта.
2. На GitHub создайте окружение `pypi` в *Settings → Environments*. Добавьте в него
   обязательных ревьюеров (required reviewers), если каждый релиз должен ждать подтверждения.

## Участие в разработке

```sh
git clone https://github.com/sinnercode228/aioamocrm
cd aioamocrm
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check && ruff format --check
mypy --strict src && mypy        # обычный mypy проверяет ещё тесты и примеры
pytest --cov
```

Каждое изменение поведения приходит с тестом, который до него падал, а исправление ошибки
начинается с теста, который её показывает. Код остаётся чистым для `mypy --strict`, у публичных
объектов есть docstring, логирование идёт через `logging.getLogger(__name__)`, и токены не
попадают в лог. Тестовые ответы следуют документации; ответ, записанный с настоящего аккаунта
(без токенов), очень пригодится — укажите, откуда он.

Примеры из README — это тесты: каждый блок Python после метки `<!-- sample: имя -->` выполняется
в `tests/test_readme.py` против моков respx, и в README.md и README.ru.md должны быть одни и те
же примеры. Об ошибках и с вопросами — в
[issues на GitHub](https://github.com/sinnercode228/aioamocrm/issues).

## Лицензия

MIT, см. [LICENSE](https://github.com/sinnercode228/aioamocrm/blob/main/LICENSE).
