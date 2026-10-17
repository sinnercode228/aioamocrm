# aioamocrm

English · [Русский](https://github.com/sinnercode228/aioamocrm/blob/main/README.ru.md)

[![CI](https://github.com/sinnercode228/aioamocrm/actions/workflows/ci.yml/badge.svg)](https://github.com/sinnercode228/aioamocrm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aioamocrm.svg)](https://pypi.org/project/aioamocrm/)

Async Python client for the amoCRM API v4, and for Kommo, which serves the same API on
`kommo.com`. It covers the parts every integration ends up writing by hand: OAuth2 tokens that
rotate on every refresh, the limit of 7 requests per second, retries that never send a write
twice unless you ask, pagination with filters, custom fields, and the form-encoded webhooks
amoCRM posts to your server. Python 3.10 or newer, built on httpx and pydantic 2, `mypy --strict`
clean, ships `py.typed`.

It grew out of the amoCRM connector in [Relay](https://github.com/sinnercode228/integration-hub),
which creates leads with their contacts and notes and receives account webhooks.

Version 0.1.0 is not on PyPI yet. The test suite runs against mocked HTTP only, with answers
written from the amoCRM documentation; there was no amoCRM account behind it.
[Checked against the documentation only](#checked-against-the-documentation-only) lists exactly
what that covers.

## Why another client

What PyPI had in October 2026. The dates are the last upload of any file, from the PyPI JSON API
(`https://pypi.org/pypi/<name>/json`):

| Package | Last release | What it is |
| --- | --- | --- |
| [amocrm](https://pypi.org/project/amocrm/) | 0.7.7, 2017-08-04 | Sync, on `requests`; calls the old `private/api/v2` API |
| [amoapi](https://pypi.org/project/amoapi/) | 0.0.5, 2021-12-01 | "Simple python wrapper for some parts of the amocrm API", in its own words |
| [amocrm-client](https://pypi.org/project/amocrm-client/) | 0.2.5, 2022-09-02 | Async, API v4; pins `aiohttp==3.7.4` and `httpx>=0.21,<0.22` |
| [amocrm-api](https://pypi.org/project/amocrm-api/) | 2.6.1, 2023-06-27 | Sync, on `requests`, API v4 |
| [amocrm-sdk](https://pypi.org/project/amocrm-sdk/) | 0.4.0, 2026-03-17 | Sync, on `requests`, API v4 |
| [amocrm-api-wrapper](https://pypi.org/project/amocrm-api-wrapper/) | 1.0.0, 2026-08-06 | Sync and async clients on httpx, API v4, `py.typed`; answers are plain `dict`s |

Of the packages that existed before 2026, amocrm, amocrm-client and amocrm-api last released
in 2017, 2022 and 2023, and amoapi in 2021. Of the two that appeared in 2026,
amocrm-api-wrapper covers more endpoints than this library (chats, catalogs, customers,
widgets and more); if you need those today, look at it.

What aioamocrm does, in mechanisms:

- **Async only**, one `AmoCRMClient` per account, used as `async with`.
- **Typed answers.** Leads, contacts, companies, notes, tasks, pipelines, custom field
  definitions, users and the account are pydantic models. Fields amoCRM adds later are kept,
  not rejected. Custom fields are read and set by `field_id` or by code (`"PHONE"`).
- **Refresh tokens that survive.** amoCRM issues a new refresh token on every refresh and the
  old one stops working. The new set goes to your `TokenStorage` before it is used, concurrent
  requests share one refresh, and tokens another process saved are picked up from the storage.
- **No duplicate writes by default.** `POST` and `PATCH` are not resent after a 429, a 5xx or
  a timeout unless the call passes `retry=True`. A batch that fails says which chunk failed and
  what was saved before it.
- **Request spacing** at 7 per second per client, in arrival order.
- **Webhook parsing** of PHP bracket bodies (`leads[status][0][id]=...`) into events.

## Install

```sh
pip install aioamocrm
```

Until 0.1.0 is published, install from GitHub:
`pip install "aioamocrm @ git+https://github.com/sinnercode228/aioamocrm"`.

It needs Python 3.10 or newer, `httpx>=0.25,<1` and `pydantic>=2.5,<3`. CI runs Python 3.10 to
3.13 with the latest versions of both, and Python 3.10 with the lowest supported ones (httpx
0.25.0, pydantic 2.5.0).

## Quick start

A long-lived token is the shortest way in: create a private integration in the account
settings and generate a long-lived token in it. The client sends it as a bearer token with
every request. Save this as `quickstart.py`:

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
        print(f"Account {account.name!r}, id {account.id}")

        [created] = await amo.leads.create([Lead(name="Website order", price=5000)])
        print(f"Created lead {created.id}")

        day_ago = int(time.time()) - 24 * 60 * 60
        async for lead in amo.leads.iterate(filter={"updated_at": {"from": day_ago}}):
            print(lead.id, lead.name, lead.price, lead.status_id)


asyncio.run(main())
```

```sh
export AMO_SUBDOMAIN=example              # for example.amocrm.ru
export AMO_LONG_LIVED_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOi...
python quickstart.py
```

What it sends:

1. `GET /api/v4/account` with `Authorization: Bearer <token>`.
2. `POST /api/v4/leads` with `[{"name": "Website order", "price": 5000}]`. The answer gives one
   `EntityRef` per lead, with its new `id`.
3. `GET /api/v4/leads?page=1&limit=250&filter[updated_at][from]=...`, then the next pages
   while the loop asks for more items. The walk ends at a page without a `next` link or an
   empty answer (204).

The requests go out at most 7 per second. For a Kommo account pass `domain="kommo.com"`, for an
international amoCRM account `domain="amocrm.com"`; `base_url=` takes any other address, such
as a proxy or a test server. A long-lived token is never refreshed: when amoCRM answers 401,
the client raises `AuthError`.

## OAuth2: tokens, storage and refresh

An integration that other accounts install uses OAuth2. In the integration settings you get the
integration ID (`client_id`) and the secret key (`client_secret`) and register the redirect URI.
When the account owner allows access, amoCRM redirects to that URI with `code`, `referer` (the
account's address, such as `example.amocrm.ru`) and `state`. The code works once, for 20
minutes.

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
    """Run once, with the code from the redirect, within 20 minutes."""
    async with AmoCRMClient(subdomain="example", auth=oauth) as amo:
        tokens = await amo.exchange_code(code)
        print(f"Access token valid until {tokens.expires_at:.0f} (Unix time)")


async def print_account() -> None:
    """Every later run: the client refreshes the tokens and saves them itself."""
    async with AmoCRMClient(subdomain="example", auth=oauth) as amo:
        account = await amo.account.get()
        print(account.name, account.subdomain)
```

`exchange_code` posts `grant_type=authorization_code` with the code, the client ID, the secret
and the redirect URI to `https://example.amocrm.ru/oauth2/access_token`, and saves the answer
(`access_token`, `refresh_token`, `expires_in`) as a `TokenSet` with an absolute `expires_at`.
[`examples/oauth_flow.py`](https://github.com/sinnercode228/aioamocrm/blob/main/examples/oauth_flow.py)
is the same flow as a command-line script.

What happens to the tokens after that:

- **When it refreshes.** The access token lives 24 hours. `OAuth2` refreshes it
  `refresh_margin` seconds (60 by default) before `expires_at`, and once after a 401. A second
  401 for the same request is raised as `AuthError`. To force a refresh, call
  `oauth.refresh(TokenEndpoint(http=amo.http, url=amo.token_url))`; normally you never need to.
- **Rotation.** Every refresh returns a new refresh token, and the old one stops working. The
  new set is saved with `storage.save()` before any request uses it. If `save()` raises, the
  request that caused the refresh fails with that error, the new tokens stay in memory, and the
  next refresh uses them, not the spent ones still in the storage.
- **Concurrency.** Requests that need a refresh at the same time wait on one lock; one refresh
  goes out and all of them use its result.
- **Several processes, one storage.** Before refreshing, `OAuth2` reads the storage again. If
  another process saved newer tokens in the meantime, they are used, and no refresh token is
  spent. When two processes refresh at the same moment, amoCRM accepts the refresh token once;
  the process that was refused reads the storage again and takes the winner's tokens if they
  are saved by then. To close that window entirely, give the processes a storage whose `load`
  and `save` run under a shared lock (a database row lock, for example), or refresh from one
  process only.
- **Never retried.** Token requests are sent once. A code works once, and a refresh whose
  answer was lost has already spent the refresh token, so sending it again cannot help.
- **Expiry of the refresh token.** The documentation gives a refresh token 3 months. An
  integration that makes no request for longer has to be authorized by the account owner
  again.

The `referer` parameter of the redirect comes from a URL anyone can forge. Split it into
subdomain and domain (`subdomain, _, domain = referer.partition(".")`) and pass both to
`AmoCRMClient`: the client accepts only `amocrm.ru`, `amocrm.com` and `kommo.com`, so a forged
referer cannot make it send the client secret to another host. Never pass the referer as
`base_url`.

### Token storage

| Storage | Where the tokens live |
| --- | --- |
| `MemoryTokenStorage` | In the process; lost when it exits. The default, for tests and one-off scripts. |
| `JsonFileTokenStorage(path)` | A JSON file, created with mode 0600. Each save writes a temporary file in the same directory, flushes it to disk and renames it over the old one, so a crash leaves either the old set or the new one. A file that is not a valid token set raises `AuthError`. |
| Your own class | Anything with `async def load()` and `async def save(tokens)`: the `TokenStorage` protocol. |

One `OAuth2` instance and one storage belong to one account. A storage for many accounts in one
SQLite file:

<!-- sample: token-storage -->
```python
import asyncio
import json
import sqlite3
from contextlib import closing

from aioamocrm import TokenSet


class SqliteTokenStorage:
    """Token sets of many accounts in one table, one row per account."""

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
        with closing(sqlite3.connect(self.path)) as db, db:  # the second "db" commits
            db.execute(
                "INSERT INTO amo_tokens VALUES (?, ?)"
                " ON CONFLICT (account) DO UPDATE SET data = excluded.data",
                (self.account, json.dumps(tokens.to_dict())),
            )
```

`TokenSet.to_dict()` holds the raw tokens, so keep that table away from logs and backups that
many people read. `repr()` of a `TokenSet` leaves the tokens out.

## Lists: pagination and filters

These have `iterate()`, an async iterator over every item, and `get_page()` for one page:
`amo.leads`, `amo.contacts`, `amo.companies`, `amo.tasks`, `amo.users`, the notes of each
entity type (`amo.leads.notes`...) and the custom field definitions of each entity type
(`amo.leads.custom_fields`...). `amo.pipelines.list()` returns every pipeline with its
statuses at once.

<!-- sample: pagination -->
```python
async for lead in amo.leads.iterate(
    filter={
        "statuses": [{"pipeline_id": 3177727, "status_id": 142}],  # won, in one pipeline
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

The arguments become amoCRM's query parameters, nested in PHP bracket notation:

| Argument | Query | Example |
| --- | --- | --- |
| `filter` | `filter[...]` | `{"id": [1, 2]}` gives `filter[id][0]=1&filter[id][1]=2` |
| `order` | `order[field]` | `{"updated_at": "desc"}` gives `order[updated_at]=desc` |
| `with_` | `with`, comma separated | `["contacts", "loss_reason"]` gives `with=contacts,loss_reason` |
| `query` | `query` | full-text search: `"+7912"` |
| `limit` | `limit` | 1 to 250, default 250; any other value raises `ValueError` before a request is made |
| `params` | anything else | encoded the same way |

`True` and `False` become `1` and `0`, and `None` values are left out. The first request of the
loop above asks for (brackets percent-encoded on the wire):

```text
/api/v4/leads?page=1&limit=250&filter[statuses][0][pipeline_id]=3177727
    &filter[statuses][0][status_id]=142&filter[updated_at][from]=1735689600
    &order[updated_at]=desc&with=contacts
```

Which filters an endpoint accepts is up to amoCRM; the client encodes whatever you pass.

How `iterate()` walks:

1. It requests `page=1&limit=250` and your parameters, and yields the items one by one. The
   next page is requested only when the loop asks for the item after the last one.
2. It stops at a 204 (amoCRM's answer for an empty list), at a page with no items, or at a
   page without `_links.next`. A page with fewer than `limit` items that still links a next
   one does not end the walk, so an endpoint that serves smaller pages than you asked for is
   read to the end.
3. It follows `_links.next.href` only when the link points into this account's `/api/v4/` and
   names the page after the current one, adding back any of your parameters the link left out
   (`with`, `limit`). Otherwise it asks for that page number itself, with the same parameters.
   A next link that does not move forward cannot make it loop or skip pages, and a link to
   another host never gets the token.

Pages are numbered, so an entity that changes during the walk can move to a page already read
or one not read yet. For a sync job, filter by `updated_at` from your last checkpoint, order by
`updated_at` ascending, and deduplicate by `id`.

`get_page()` returns a `Page`: `items`, `number`, `next_url` and `has_next`. Iterating a page
iterates its items, and `len(page)` counts them. A 204 gives an empty page.

### Endpoints without a resource

`amo.request()` sends any API request with the same authorization, spacing, retries and error
mapping, and returns the decoded JSON, or `None` for an empty answer:

<!-- sample: request -->
```python
data = await amo.request("GET", "events", params={"limit": 100})
events = data["_embedded"]["events"] if data else []  # None: 204, nothing to list
for event in events:
    print(event["type"], event["entity_id"])
```

`path` is relative to `/api/v4/`, or an absolute URL under this account's `/api/v4/` as found
in `_links`; any other absolute URL raises `ValueError`. `params` is encoded like `filter`
above, `json=` is the body, and `retry=` works as described in
[Rate limiting and retries](#rate-limiting-and-retries).

## Creating leads with contacts and companies

`POST /api/v4/leads/complex` creates a lead together with a new contact and a new company in
one request. Put them in `embedded`, one contact and one company per lead at most:

<!-- sample: complex -->
```python
from aioamocrm import Company, Contact, Embedded, Lead, Tag

contact = Contact(name="Ivan Petrov")
contact.set_field("PHONE", "+79123456789", enum_code="WORK")
contact.set_field("EMAIL", "ivan@example.com", enum_code="WORK")

lead = Lead(
    name="Website order #1042",
    price=5000,
    pipeline_id=3177727,
    embedded=Embedded(
        contacts=[contact],
        companies=[Company(name="Romashka LLC")],
        tags=[Tag(name="website")],
    ),
)
[result] = await amo.leads.create_complex([lead])
print(result.id, result.contact_id, result.company_id, result.merged)
```

The body sent is the lead's `to_api()`: only the fields you set, with `_embedded` holding the
contact, the company and the tag. Each `ComplexLeadResult` has `id`, `contact_id`, `company_id`,
`request_id` and `merged`, which tells whether amoCRM merged the contact into an existing one
found by its duplicate control. The answer has room for one contact and one company per lead,
so a lead that embeds two of either raises `ValueError` before anything is sent; link more
contacts with `link()` afterwards. The documentation allows at most 50 leads per request, which
is the default `batch_size`: a list of 120 leads goes out as 50, 50 and 20.

To link entities that already exist, such as an existing contact to a lead, use `link()`:

<!-- sample: link -->
```python
await amo.leads.link(19619, "contacts", 15009433, metadata={"is_main": True})

for link in await amo.leads.links(19619, filter={"to_entity_type": "contacts"}):
    print(link.to_entity_type, link.to_entity_id, link.metadata)
```

`metadata` is sent as given: `{"is_main": True}` makes the contact the lead's main one,
`{"catalog_id": 1, "quantity": 2}` links a catalog element. `unlink()` takes the same
arguments.

### Batches and partial failure

`create()`, `update()`, `create_complex()`, `notes.create()`, `tasks.create()` and
`tasks.update()` take any number of items and send them `batch_size` per request (50 by
default, up to 250). When a chunk fails, the chunks after it are not sent, and the exception
says where it stopped:

<!-- sample: batch-errors -->
```python
from aioamocrm import Lead, ValidationError

leads = [Lead(name=f"Import {n}", price=100 * n) for n in range(120)]
try:
    await amo.leads.create(leads)  # 3 requests: 50, 50 and 20 leads
except ValidationError as exc:
    offset = exc.batch_offset or 0  # input index of the first lead of the refused chunk
    print(len(exc.completed), offset)  # what the earlier chunks saved, and where it stopped
    for issue in exc.errors:
        # request_id counts from the start of the chunk, unless you set your own
        print(offset + int(issue.request_id or 0), issue.path, issue.code, issue.detail)
```

Every `AmoCRMError` raised by a batched write carries `batch_offset` and `completed` (the
`EntityRef`s or `ComplexLeadResult`s of the chunks saved before it). What to do next depends on
the error:

- `ValidationError` (400): amoCRM refused the request. Fix the items listed in `errors` and send
  `leads[exc.batch_offset:]` again.
- `NetworkError` after the request was sent, `ServerError`, or a timeout: nobody knows whether
  amoCRM saved the chunk. Look the leads up (by a field only your import sets, for example)
  before sending it again.
- `AmoCRMError` with `status_code` 200: the chunk was saved, but the answer could not be read.
  The raw answer is in `body`; do not send that chunk again.

## Custom fields

Custom field values live in `custom_fields_values`. The helpers find a field by its `field_id`
(an `int`) or its `field_code` (a `str`, compared without case):

<!-- sample: fields-read -->
```pycon
>>> from aioamocrm import Contact
>>> contact = Contact.model_validate(
...     {
...         "id": 3,
...         "name": "Ivan Petrov",
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
...                 "field_name": "Source",
...                 "field_code": None,
...                 "field_type": "select",
...                 "values": [{"value": "Website", "enum_id": 4567}],
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
'Website'
>>> contact.field_value("EMAIL", "no email")
'no email'
```

`field(key)` returns the whole `CustomFieldValues` entry, or `None`. Leads, contacts and
companies all have these methods, and webhook events have `field_value()` and `field_values()`
too.

`set_field()` replaces the values of a field, or adds the field: by `field_id` for an `int` key,
by `field_code` for a `str` key. `to_api()` builds the write body from the fields you set:

<!-- sample: fields-write -->
```pycon
>>> from aioamocrm import Contact
>>> update = Contact(id=3)
>>> update.set_field("POSITION", "Buyer")
>>> update.set_field(1234, "Advertising")
>>> update.to_api()
{'id': 3, 'custom_fields_values': [{'field_code': 'POSITION', 'values': [{'value': 'Buyer'}]},
 {'field_id': 1234, 'values': [{'value': 'Advertising'}]}]}
```

For a select field amoCRM takes the option's text as `value` or its `enum_id`. For values with an
`enum_code` (`"WORK"`, `"MOB"` for phones), pass the code as a keyword:
`set_field("PHONE", number, enum_code="WORK")`.

`set_field()` replaces all values of the field, so adding a phone means sending the old ones
with the new one:

<!-- sample: fields-update -->
```python
from aioamocrm import Contact, CustomFieldValue

contact = await amo.contacts.get(3)
entry = contact.field("PHONE")
phones = list(entry.values or []) if entry else []  # keeps value, enum_id and enum_code
phones.append(CustomFieldValue(value="+79001112233", enum_code="WORK"))

update = Contact(id=contact.id)  # only the fields that change
update.set_field("PHONE", *phones)
await amo.contacts.update([update])
```

Send partial models like `update` above. A model fetched from the API sends back every field
it was read with; `to_api()` drops only the parts amoCRM computes (`_links`, `account_id`,
`is_deleted`, `closest_task_at`, and `field_name` / `field_type` of field values). To control
the body exactly, for example to send `null`, pass a `dict` instead of a model.

The field definitions, with the ids of select options:

<!-- sample: custom-fields -->
```python
async for field in amo.leads.custom_fields.iterate():
    print(field.id, field.type, field.code, field.name)

source = await amo.contacts.custom_fields.get(1234)
print(source.enum_id("Advertising"))  # the option's id, or None
```

About the models in general: every field is optional, because what amoCRM returns depends on
the endpoint, `with` and the account. Fields the model does not declare are kept in
`model_extra`. The models also accept what PHP's `json_encode` produces: `[]` for an empty
object, `null` for an empty list, and numbers in text fields (a lead named `2024`).
`Lead.price` takes a `float` as well as an `int`, for accounts with minor currency units:
`Lead(price=99.5)` sends `"price": 99.5`.

## Notes and tasks

<!-- sample: notes-tasks -->
```python
import time

from aioamocrm import Note, Task

lead_id = 19619
await amo.leads.notes.create([Note.common(lead_id, "Asked to call back after 6 pm")])
async for note in amo.leads.notes.iterate(entity_id=lead_id):
    print(note.created_at, note.note_type, note.text)

call = Task(
    text="Call the client back",
    entity_type="leads",
    entity_id=lead_id,
    task_type_id=1,  # 1 is a call, 2 a meeting
    complete_till=int(time.time()) + 3600,
)
await amo.tasks.create([call])
open_tasks = {"is_completed": False, "entity_type": "leads", "entity_id": [lead_id]}
async for task in amo.tasks.iterate(filter=open_tasks):
    print(task.id, task.text, task.complete_till)

await amo.tasks.complete(7087, result="Meeting set for Monday")
```

Notes belong to an entity type: `amo.leads.notes`, `amo.contacts.notes`, `amo.companies.notes`.
`Note.common(entity_id, text)` is a plain text note; other types are
`Note(entity_id=..., note_type="...", params={...})` with the `params` the documentation gives
for that type. `note.text` reads `params.text`. `iterate(entity_id=...)` lists one entity's
notes, `iterate()` without it the notes of every entity of that type, and
`get(note_id, entity_id=...)` one note: it is fetched through its entity, so both ids are
needed.

A task needs `text` and `complete_till` (Unix time). `tasks.update()` takes tasks with their
`id`, and `tasks.complete(task_id, result=...)` sends `PATCH /api/v4/tasks/{id}` with
`is_completed` and the result text.

## Webhooks

amoCRM posts account webhooks as `application/x-www-form-urlencoded` bodies with PHP bracket
keys, and one request can carry several entities and actions. `parse_webhook()` takes the raw
body and works with any framework:

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

Each `WebhookEvent` has `entity` (`"leads"`, `"contacts"`, `"companies"`...), `action`
(`"add"`, `"update"`, `"delete"`, `"restore"`, `"status"`, `"responsible"`, `"note"`...) and
`payload`, the entity's fields exactly as sent: every value is a string, so convert what you
need (`event.id` already does it for the id). For a `note` action the note is under
`payload["note"]`, with `element_id` naming the entity. `webhook.data` is the whole body as
nested dicts and lists.

**These webhooks are not signed.** amoCRM sends no signature and no secret header, so anyone
who learns the URL can post events to it. Three rules follow:

1. Put a long random secret in the URL and compare it with `hmac.compare_digest`.
2. Treat the payload as a notification. Before anything that matters (money, a status change in
   another system), fetch the entity by id through the API and act on what the API says.
3. Answer with a 2xx right away and do the work afterwards. amoCRM resends webhooks that fail
   or answer slowly and switches off endpoints that keep doing so.

With FastAPI:

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
            print(f"lead {event.id}: status {old} -> {new}")


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
    background.add_task(handle, webhook)  # runs after the answer is sent
    return {"events": len(webhook.events)}
```

```sh
export AMO_WEBHOOK_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn app:app
# register https://your-host/webhooks/amocrm/<the secret> in the account's webhook settings
```

FastAPI's background tasks run in the same process, so events are lost if it stops before they
are handled. When that matters, write the parsed events to a queue or a table in the endpoint
and handle them in a worker; that is what Relay does.
[`examples/webhook_fastapi.py`](https://github.com/sinnercode228/aioamocrm/blob/main/examples/webhook_fastapi.py)
is a longer version with handlers for status changes, contact updates and notes.

`parse_webhook()` raises `ValueError` for a body that is not UTF-8, has more than `max_fields`
fields (10,000 by default) or nests deeper than 32 levels. A body with 10,000 fields parses in
about 0.05 s. In aiohttp the raw body is `await request.read()`, in Django `request.body`.

## Rate limiting and retries

### Request spacing

The documentation allows an integration 7 requests per second, and an account 50 per second
across all its integrations. Going over is answered with HTTP 429, and repeated flooding gets
the IP blocked (HTTP 403). Each client therefore spaces its requests at most 7 per second:

- Callers queue in arrival order. Each one waits until `1 / rate` seconds have passed since the
  previous one actually went out. A blocked event loop delays the queue but never releases a
  burst afterwards, and idle time does not build up credit. Measured on a real clock: 29
  concurrent requests at rate 7 gave at most 7 starts in any one-second window, also right
  after a 1.5 s stall of the event loop.
- `rate_limit=` takes a number of requests per second, a `RateLimiter` shared by several
  clients in one event loop (`shared = RateLimiter(7)`, then `rate_limit=shared` for each), or
  `None` for no spacing.
- The limiter counts only its own process. Two processes of one integration at 7 per second
  each make 14; give each a share.

<!-- sample: limits -->
```python
import asyncio
import os

from aioamocrm import AmoCRMClient, LongLivedToken, RetryPolicy

# Two worker processes share one integration, so each gets half of the 7 per second.
async with AmoCRMClient(
    subdomain="example",
    auth=LongLivedToken(os.environ["AMO_LONG_LIVED_TOKEN"]),
    rate_limit=3.5,
    retry=RetryPolicy(max_attempts=6, backoff_max=10.0, max_retry_after=30.0),
) as worker:
    found = await asyncio.gather(*(worker.leads.get(i) for i in (19619, 19620, 19621)))
print([lead.name for lead in found])
```

### What is retried

| What happened | `GET`, `HEAD`, `OPTIONS` | `POST`, `PATCH` | `POST`, `PATCH` with `retry=True` |
| --- | --- | --- | --- |
| Could not connect (`ConnectError`, `ConnectTimeout`, `PoolTimeout`): nothing was sent | retried | retried | retried |
| 429 Too Many Requests | retried | `RateLimitError` | retried |
| 500, 502, 503, 504 | retried | `ServerError` | retried |
| Timeout or broken connection after the request was sent | retried | `NetworkError` | retried |
| 401 with `OAuth2` | one refresh, one more try | the same | the same |
| 401 with `LongLivedToken`, any other 4xx, any 3xx | raised | raised | raised |

The wait before the next attempt is the `Retry-After` header when the answer has one (seconds or
an HTTP date). Otherwise it is exponential backoff with jitter: attempt *n* waits a random time
between half and all of `min(backoff_max, backoff_base * 2 ** (n - 1))`, so 0.25 to 0.5 s, then
0.5 to 1 s, then 1 to 2 s with the defaults. `max_attempts` counts every try, the first one
included (4 by default). A `Retry-After` longer than `max_retry_after` (60 s) is not waited out:
the error is raised at once, and `RateLimitError.retry_after` tells how long amoCRM asked for.

**Why `POST` is not retried by default.** The amoCRM API has no idempotency keys. A
`POST /api/v4/leads` that timed out may have created the leads, and sending it again creates
them a second time; duplicate leads and contacts are what amoCRM users end up merging by hand.
A 429 most likely means amoCRM did nothing, but the documentation does not promise that, so a
write answered with 429 is not resent either:

<!-- sample: retry-write -->
```python
from aioamocrm import Lead, RateLimitError

try:
    await amo.leads.create([Lead(name="Website order")])
except RateLimitError as exc:
    # Not sent again: amoCRM does not say whether a write it answered with 429 was applied.
    print(f"429; Retry-After: {exc.retry_after} s; saved before it: {len(exc.completed)}")
```

Pass `retry=True` to a write when a duplicate is acceptable or you check for one before
resending: `await amo.leads.create(leads, retry=True)`. It turns on retries for 429, 5xx and
timeouts together. In `amo.request()`, `retry=False` turns them off for a `GET` too. Failures
to connect are retried in every case, since nothing reached amoCRM.

`RetryPolicy` fields: `max_attempts`, `backoff_base`, `backoff_max`, `max_retry_after`,
`retry_statuses` (5xx statuses retried besides 429) and `idempotent_methods`. Pass it as
`AmoCRMClient(retry=RetryPolicy(...))`.

## Errors

```text
AmoCRMError                 any answer outside 2xx, 3xx included; base of everything below
├── AuthError               401, or tokens that could not be obtained or refreshed
├── PaymentRequiredError    402: the account is not paid
├── ForbiddenError          403: no rights, a blocked account, or an IP blocked for flooding
├── NotFoundError           404, or 204 for an entity asked for by id
├── ValidationError         400 or 422; .errors lists every ValidationIssue
├── RateLimitError          429; .retry_after holds Retry-After in seconds, if sent
├── ServerError             5xx after the retries
└── NetworkError            no HTTP answer: connection failure, timeout, broken stream
```

<!-- sample: errors -->
```python
from aioamocrm import AmoCRMError, NotFoundError

for lead_id in (1, 2):
    try:
        lead = await amo.leads.get(lead_id)
    except NotFoundError:
        print(lead_id, "no such lead")
    except AmoCRMError as exc:
        print(exc.status_code, exc.method, exc.url, exc.title, exc.detail)
    else:
        print(lead_id, lead.name)
```

Every error has `status_code` (`None` without an answer), `title` and `detail` from amoCRM's
error body, `body` (the decoded answer), `method`, `url`, and for batched writes `batch_offset`
and `completed`. The message reads like
`HTTP 402 Payment Required: Account is not paid (GET /api/v4/leads/2)`. When the body also has a
`hint`, as the token endpoint's errors do, it follows the detail in brackets:
`The refresh token is invalid. (Token has been revoked)`. Neither the message nor `url` includes
the query string, which may hold search terms, and no error carries a token.

A `ValidationIssue` has `request_id`, `code`, `path` (such as `custom_fields_values.0.field_id`)
and `detail`.

Redirects are never followed, also when you pass an `http=` client built with
`follow_redirects=True`: a 301, 302 or 303 would replay a write as a `GET` without its body,
and a 307 or 308 would send the body (to the token endpoint, that is the client secret and the
refresh token) wherever it points. A 3xx is raised as `AmoCRMError` naming where it pointed
(without the query), which usually means a wrong subdomain or domain, or an account that
moved.

Wrong arguments fail before any request: `ValueError` for a `limit` outside 1 to 250, a blank
token or an absolute URL outside the account, `TypeError` for an id that is not an `int`.
Errors raised by your own `TokenStorage` reach you unchanged.

### Logging

The modules log to `logging.getLogger("aioamocrm")` and its children. At DEBUG there is one
line per request (`GET /api/v4/leads -> 200`, the path without the query), at INFO token
exchanges, refreshes and retries after a 401, at WARNING every retry with its attempt number and
delay. Tokens, the client secret, query strings and bodies are never logged. httpx logs each
request on its own `httpx` logger at INFO, with the full URL including the query string, but no
headers.

## Checked against the documentation only

There is no amoCRM account behind the test suite. Every request and answer is mocked with respx,
with bodies written from the
[amoCRM API v4 documentation](https://www.amocrm.ru/developers/content/crm_platform/api-reference).
The documentation site was not reachable while this version was written, so the shapes come
from the documentation as I knew it, and none of them was recorded from a live account. These
are the behaviours that rest on it:

**Limits and transport**

- 7 requests per second per integration and 50 per account; 429 when exceeded, then 403 and an
  IP block for repeated flooding. The default `rate_limit=7` comes from there.
- `Retry-After` on a 429: the documentation does not promise it. It is honoured when present.
- Whether a write answered with 429 was applied: the documentation does not say. That is why
  writes are not resent after a 429 without `retry=True`.
- A 3xx for an account that moved (to `kommo.com`, say) is an assumption; the client raises any
  3xx it gets.
- Kommo accounts on `kommo.com` serve the same API v4.

**Lists**

- `204 No Content` for an empty list, and for an entity fetched by an id that does not exist (a
  404 is handled the same way).
- `page` and `limit` (at most 250), `_page`, and `_links.next.href` in list answers.
- The filter names used in this README (`filter[statuses][0][pipeline_id]`,
  `filter[updated_at][from]`, `filter[is_completed]`, `filter[entity_type]`,
  `filter[entity_id]`), and `with=contacts` embedding contact ids with `is_main`.

**Writes**

- At most 50 leads per `POST /leads/complex`, up to 250 items in other writes.
- Write answers: `_embedded.<entity>` with `id` and `request_id` per item; for
  `/leads/complex` a JSON array of `id`, `contact_id`, `company_id`, `request_id` (a list) and
  `merged`.
- `request_id` comes back unchanged (a number sent comes back as a number), and is the item's
  position in the request when the caller did not set one.
- Error bodies: `title`, `type`, `status`, `detail`; on a 400, `validation-errors` with
  `request_id` and `errors` of `code`, `path` and `detail` per refused item, and the whole
  request refused. 402 for an account that is not paid.
- Links: `POST /{entity}/{id}/link` with `to_entity_id`, `to_entity_type` and `metadata`
  (`is_main`, `catalog_id`, `quantity`), answered with `_embedded.links`.
- Completing a task with `PATCH /tasks/{id}` and `is_completed` plus `result.text`.
- A select field accepting the option text as `value` or its `enum_id`.

**OAuth2**

- The token endpoint `/oauth2/access_token` on the account's domain, its JSON request bodies,
  and the answer fields `token_type`, `expires_in`, `access_token`, `refresh_token`.
- The 24-hour access token, the 20-minute single-use authorization code, the 3-month refresh
  token, and the rotation of the refresh token on every refresh.
- Long-lived tokens sent as a bearer token, like an access token.

**Data**

- PHP's `json_encode` habits: `[]` for empty objects, `"result": []` on tasks without a result,
  numbers in text fields.
- Status 142 is "won" and 143 "lost" in every pipeline; task type 1 is a call and 2 a meeting;
  field codes `PHONE`, `EMAIL` and `POSITION`, with enum codes such as `WORK` and `MOB`.

**Webhooks**

- The body format, including notes under `leads[note][0][note][...]` and date fields sent as
  `values][0]=` without `[value]`; all values strings.
- No signature of any kind on account webhooks.
- Resending, and switching off endpoints that keep failing or answering slowly.

What was checked here, without amoCRM: the requests the client builds (paths, query encoding,
bodies), every retry decision in the table above, the spacing on a real clock, pagination with
next links that drop parameters or stand still, token refresh with failed saves and with two
processes sharing a storage, the webhook parser on 10,000-field bodies, and that no access
token, refresh token, client secret or long-lived token shows up in logs at DEBUG, in `repr()`s,
error messages or tracebacks.

If you run it against a real account and an answer differs, please open an issue with the
request and the answer, tokens removed.

## Limits and roadmap

Limits of 0.1.0:

- Typed resources cover leads, contacts, companies, notes, tasks, pipelines and statuses, custom
  field definitions, users and the account. Customers, catalogs, unsorted leads, events, calls,
  sources, chats, webhook subscriptions, widgets and files go through `amo.request()` and come
  back as plain JSON. There are no delete methods.
- Async only; there is no sync client.
- The limiter spaces requests within one process and one event loop. It does not coordinate
  processes or threads.
- The race between two processes refreshing at once is narrowed, not closed: if the refused
  refresh's answer arrives before the winner has saved, that process raises `AuthError` once,
  and its next request picks up the saved tokens. A storage with a shared lock closes it.
- `retry=True` turns on retries for 429, 5xx and timeouts together; there is no switch for 429
  alone.
- A `Retry-After` in the obsolete asctime date format is ignored, and the client backs off
  instead. Seconds, RFC 1123 and RFC 850 dates work.
- Webhook payloads are strings in nested dicts, without models per entity.

Next, in this order:

1. Record answers from a live account, replace the fixtures written from the documentation, and
   fix whatever differs.
2. A separate switch for resending writes after a 429 only, as a new `RetryPolicy` field with a
   default, so existing code keeps its behaviour.
3. Resources for events, unsorted leads, customers and catalogs.
4. Webhook subscriptions through `/api/v4/webhooks`.
5. A token storage recipe with a shared lock (a PostgreSQL row lock or Redis) for several
   processes.

## Testing your code

The client sends everything through httpx, so [respx](https://lundberg.github.io/respx/) mocks
it. With pytest and pytest-asyncio (`asyncio_mode = "auto"`):

<!-- sample: testing -->
```python
import pytest
import respx

from aioamocrm import AmoCRMClient, LongLivedToken, RetryPolicy


async def won_total(amo: AmoCRMClient, pipeline_id: int) -> int:
    """The code under test: the sum of won leads in one pipeline."""
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
        rate_limit=None,  # no spacing between mocked requests
        retry=RetryPolicy(max_attempts=1),  # a mocked 503 raises at once instead of backing off
    ) as amo:
        assert await won_total(amo, 3177727) == 1000
    params = route.calls.last.request.url.params
    assert params["filter[statuses][0][status_id]"] == "142"
```

More things that help:

- `respond(204)` is how amoCRM answers an empty list; `respond(429, headers={"Retry-After":
  "1"})` tests your handling of `RateLimitError`.
- For OAuth2 code, start from known tokens and a fixed clock:
  `OAuth2(..., storage=MemoryTokenStorage(TokenSet(access_token="a", refresh_token="r",
  expires_at=now + 3600)), clock=lambda: now)`, and mock `POST /oauth2/access_token`.
- `AmoCRMClient(http=httpx.AsyncClient(transport=...))` takes your own transport, such as
  `httpx.MockTransport`, if you prefer it to respx.
- Webhook handlers need no HTTP at all: build the body with `urlencode` and call
  `parse_webhook()`, as in [Webhooks](#webhooks).
- [`tests/payloads.py`](https://github.com/sinnercode228/aioamocrm/blob/main/tests/payloads.py)
  has answers shaped like the documentation's examples for every resource (MIT, copy it).

## Releasing

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), and every release
has an entry in [CHANGELOG.md](https://github.com/sinnercode228/aioamocrm/blob/main/CHANGELOG.md).

1. Set the version in `pyproject.toml` and `__version__` in `src/aioamocrm/_version.py`;
   `tests/test_version.py` fails if the two differ.
2. In `CHANGELOG.md`, replace `Unreleased` in the version's heading with the release date.
3. Commit, then push a tag: `git tag v0.1.0 && git push origin v0.1.0`.

[`release.yml`](https://github.com/sinnercode228/aioamocrm/blob/main/.github/workflows/release.yml)
checks that the tag matches the version in `pyproject.toml`, runs the tests, builds the sdist
and the wheel, runs `twine check --strict`, and uploads them from a job in the GitHub environment
`pypi`. The upload uses PyPI trusted publishing: GitHub gives that job a short-lived OIDC token,
and no PyPI token is stored anywhere.

The owner sets this up once:

1. On PyPI, open <https://pypi.org/manage/account/publishing/> and add a pending publisher with
   project name `aioamocrm`, owner `sinnercode228`, repository `aioamocrm`, workflow
   `release.yml` and environment `pypi`. It has to be a pending one because the project does not
   exist on PyPI before the first upload; that upload turns it into the project's publisher.
2. On GitHub, create the environment `pypi` under *Settings → Environments*. Add required
   reviewers to it if every release should wait for a click.

## Contributing

```sh
git clone https://github.com/sinnercode228/aioamocrm
cd aioamocrm
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check && ruff format --check
mypy --strict src && mypy        # plain mypy also checks tests and examples
pytest --cov
```

Every change in behaviour comes with a test that failed before it, and a bug fix starts with the
test that shows the bug. The code stays `mypy --strict` clean, public objects have docstrings,
logging goes through `logging.getLogger(__name__)`, and nothing logs a token. Test answers
follow the documentation; an answer recorded from a real account (tokens removed) is welcome,
with a note on where it came from.

The README samples are tests: every Python block after a `<!-- sample: name -->` marker runs in
`tests/test_readme.py` against respx mocks, and README.md and README.ru.md must carry the same
samples. Bugs and questions go to
[GitHub issues](https://github.com/sinnercode228/aioamocrm/issues).

## License

MIT, see [LICENSE](https://github.com/sinnercode228/aioamocrm/blob/main/LICENSE).
