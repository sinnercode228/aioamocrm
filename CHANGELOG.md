# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- `AmoCRMClient` for one amoCRM / Kommo account, addressed by `subdomain` and `domain`
  (`amocrm.ru`, `amocrm.com`, `kommo.com`) or by `base_url`; an async context manager that
  owns its `httpx.AsyncClient` or uses the one it is given. Absolute URLs outside the account's
  `/api/v4/` are refused, so the token never leaves the account.
- Authorization: `LongLivedToken`, and `OAuth2` with `exchange_code`, refresh shortly before
  expiry (`refresh_margin`, 60 s by default) and once after a 401, one shared refresh for
  concurrent requests, the rotated refresh token saved through a `TokenStorage`
  (`MemoryTokenStorage`, `JsonFileTokenStorage` with atomic writes and mode 0600), and
  adoption of tokens renewed by another process sharing the storage, also when that process
  spent the refresh token first and this refresh was refused. A refresh refused with 401 or
  404 is looked up the way the official PHP SDK does it
  (`GET https://{api_domain}/oauth2/account/current/subdomain` with `X-Refresh-Token`), and
  `AccountMovedError` names the account's new host without spending the refresh token.
- `RateLimiter`: requests spaced at 7 per second by default, in arrival order, each measured
  from the moment the previous one went, so a stalled event loop never releases a burst;
  configurable or shared between clients.
- `RetryPolicy`: exponential backoff with jitter and `Retry-After`, a bounded number of
  attempts; failures to connect are retried for every method, 429, 5xx and timeouts only for
  idempotent methods unless the call passes `retry=True`, so a write is never sent twice
  without the caller asking for it.
- Typed errors: `AmoCRMError` and `AuthError` (with `AccountMovedError`),
  `PaymentRequiredError`, `ForbiddenError`, `NotFoundError`, `ValidationError` (with every
  `ValidationIssue`), `RateLimitError`, `ServerError`, `NetworkError`. Messages carry amoCRM's
  `title`, `detail` and `hint`. Any answer outside 2xx is an error: redirects are never
  followed, also by an `http=` client built with `follow_redirects=True` (a write would be
  replayed as a `GET`, or its body sent to another address), and are raised naming where they
  pointed; the same holds for the token endpoint. A `202 Accepted` whose body is not JSON is a
  success, as in the official PHP SDK. Batched writes report `batch_offset` and the `completed`
  results of the chunks saved before a failure.
- Lists: `iterate()` async iterators that follow `_links.next` (only when it names the next
  page number, so a stuck link can neither loop nor skip pages) and stop on 204, on an empty
  page or without a next link (a short page that links a next one is followed), and
  `get_page()`; `filter`, `order`, `with_`, `query` encoded in PHP bracket notation. An empty
  list (204 No Content) is an empty result.
- Resources: leads, contacts and companies (get, create and update in batches, link, unlink,
  links), `leads.create_complex` (`POST /leads/complex`, one contact and one company per lead,
  checked before sending), notes per entity type (one note is fetched through its entity:
  `notes.get(note_id, entity_id=...)`), tasks (create, update, complete), pipelines and
  statuses, custom fields per entity type, users, account.
- Pydantic v2 models that keep unknown fields, with `field()`, `field_value()`,
  `field_values()` and `set_field()` for custom fields by id or code (`PHONE` / `EMAIL` with
  `enum_code`), and `to_api()` write bodies without the read-only parts. `Lead.price` takes a
  `float` for prices with minor currency units.
- `parse_webhook()`: amoCRM form-encoded webhook bodies (`leads[add][0][id]`,
  `contacts[update][0][custom_fields][0][values][0][value]`, `account[subdomain]`...) into
  `WebhookEvent`s with `entity`, `action` and `payload`, framework-agnostic.
- Examples: `oauth_flow.py`, `sync_leads.py`, `webhook_fastapi.py`.
- Documentation: `README.md` in English and `README.ru.md` in Russian, with the same sections
  and samples: quick start with a long-lived token, OAuth2 and token storage (an SQLite
  storage for many accounts), pagination and filters, leads with contacts and companies,
  custom fields, notes and tasks, webhooks with FastAPI, rate limiting and retries, errors,
  what was checked and how, limits and roadmap, testing with respx, and the release process.
  Every Python sample in both files runs in `tests/test_readme.py` against respx mocks
  (`pycon` samples as doctests), so a sample that stops working fails the build. The sdist
  includes `README.ru.md`.
- Request and answer shapes cross-checked against the official amoCRM PHP SDK
  ([amocrm/amocrm-api-php](https://github.com/amocrm/amocrm-api-php) at `a0072fe`). The READMEs
  list what it confirms with links to its code, what it made me fix (the changes to
  `notes.get`, `iterate()`, redirects, error hints, `202 Accepted` answers, `/leads/complex`,
  `Lead.price`, the tasks filter and `AccountMovedError` above), and what still rests on the
  documentation alone.

[0.1.0]: https://github.com/sinnercode228/aioamocrm/releases/tag/v0.1.0
