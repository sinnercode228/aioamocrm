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
  adoption of tokens renewed by another process sharing the storage.
- `RateLimiter`: requests spaced at 7 per second by default, configurable or shared between
  clients.
- `RetryPolicy`: exponential backoff with jitter and `Retry-After`, a bounded number of
  attempts; 429 and connection failures are retried for every method, 5xx and timeouts only
  for idempotent methods unless the call passes `retry=True`.
- Typed errors: `AmoCRMError` and `AuthError`, `PaymentRequiredError`, `ForbiddenError`,
  `NotFoundError`, `ValidationError` (with every `ValidationIssue`), `RateLimitError`,
  `ServerError`, `NetworkError`. Batched writes report `batch_offset` and the `completed`
  results of the chunks saved before a failure.
- Lists: `iterate()` async iterators that follow `_links.next` and stop on 204, on a short
  page or without a next link, and `get_page()`; `filter`, `order`, `with_`, `query` encoded in
  PHP bracket notation. An empty list (204 No Content) is an empty result.
- Resources: leads, contacts and companies (get, create and update in batches, link, unlink,
  links), `leads.create_complex` (`POST /leads/complex`), notes per entity type, tasks
  (create, update, complete), pipelines and statuses, custom fields per entity type, users,
  account.
- Pydantic v2 models that keep unknown fields, with `field()`, `field_value()`,
  `field_values()` and `set_field()` for custom fields by id or code (`PHONE` / `EMAIL` with
  `enum_code`), and `to_api()` write bodies without the read-only parts.
- `parse_webhook()`: amoCRM form-encoded webhook bodies (`leads[add][0][id]`,
  `contacts[update][0][custom_fields][0][values][0][value]`, `account[subdomain]`...) into
  `WebhookEvent`s with `entity`, `action` and `payload`, framework-agnostic.
- Examples: `oauth_flow.py`, `sync_leads.py`, `webhook_fastapi.py`.

[0.1.0]: https://github.com/sinnercode228/aioamocrm/releases/tag/v0.1.0
