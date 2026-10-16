# aioamocrm

Async client for the amoCRM / Kommo API v4: OAuth2 with refresh-token rotation, a
per-client rate limiter, retries with backoff, pagination, typed models with custom
field helpers and a parser for amoCRM webhooks.

Full documentation is coming with the first release.

## Checked against the documentation only

There is no amoCRM account behind the test suite: every request and answer is mocked with
bodies written from the amoCRM API v4 documentation. These behaviours come from that
documentation and have not been checked against a live account yet:

- the 7 requests per second limit per integration, and HTTP 429 (then 403) when it is exceeded;
- `204 No Content` for an empty list, and for an entity fetched by an id that does not exist
  (a 404 is handled the same way);
- `page` / `limit` (at most 250) pagination with `_links.next`;
- at most 50 leads per `POST /leads/complex` (also used as the default batch size elsewhere);
- the OAuth2 token endpoint `/oauth2/access_token`, its JSON bodies, the 24-hour access token,
  the 20-minute authorization code and the rotation of the refresh token on every refresh;
- the shape of error answers (`title`, `detail`, `validation-errors` with `request_id`,
  `code`, `path`, `detail`) and HTTP 402 for an unpaid account;
- `Retry-After`: the documentation does not promise it; it is honoured when present;
- the webhook body format, including notes under `leads[note][0][note][...]`, and the absence
  of any signature on account webhooks;
- `"result": []` on tasks without a result.
