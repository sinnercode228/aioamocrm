# Security policy

aioamocrm holds the keys to an amoCRM or Kommo account: the client secret of an integration,
access tokens and the refresh token that renews them. A hole in it can hand those to someone
else, or let a crafted webhook body stall the server that parses it. Please report such problems
privately, not in a public issue.

## Supported versions

Fixes go to the latest minor release only. While the version is 0.x, a fix comes out as a new
patch release of that minor version.

| Version | Gets fixes |
| --- | --- |
| 0.1.x | yes |

## Reporting a vulnerability

Use either of these:

- **GitHub private vulnerability reporting.** Open the
  [Security tab](https://github.com/sinnercode228/aioamocrm/security) of the repository and
  choose *Report a vulnerability*. The report and the discussion stay private until the advisory
  is published.
- **Telegram:** [@sinnercode](https://t.me/sinnercode), the author. Use it if the button above
  is not there or you would rather talk first.

A useful report has:

- the aioamocrm, httpx, pydantic and Python versions, and the auth and token storage you use
  (`LongLivedToken`, `OAuth2` with `MemoryTokenStorage`, `JsonFileTokenStorage` or your own);
- what an attacker has to control (a redirect to your OAuth2 callback, a webhook request, an
  answer from the network, a file on the same machine) and what they get;
- the steps, or better a test: the test suite mocks amoCRM with respx, so a test can show the
  problem without an account.

Leave out real tokens, client secrets and account data. The fix is released as a new version
with an entry in [CHANGELOG.md](https://github.com/sinnercode228/aioamocrm/blob/main/CHANGELOG.md),
and you are credited there unless you ask not to be.

## Scope

In scope, the code in `src/aioamocrm`:

- **Tokens and the client secret.** Any way to make the client send an access token, the
  refresh token or the client secret to a host other than the account's own: through a forged
  `referer`, an absolute URL passed to a request, a redirect, or the `api_domain` lookup after a
  refused refresh. A token or the secret showing up in a log line, an exception, an error's
  `url` or `body`, or a `repr()`. A refresh that spends the refresh token and loses the new one.
- **Token storage.** `JsonFileTokenStorage` leaving a token file or its temporary file readable
  by other users (it creates them with mode 0600), or a save that can leave a half-written file
  in place of the old one.
- **Webhook parsing.** A body that gets past the limits of `parse_webhook()` (`max_fields`,
  10,000 by default, and 32 levels of nesting), or takes far longer or far more memory to parse
  than its size suggests.

Out of scope:

- That amoCRM does not sign its webhooks. The README says so and gives the rules that follow:
  a long random secret in the URL, compared with `hmac.compare_digest`, and the entity fetched
  through the API before anything that matters. A flaw in that advice is in scope.
- Bugs in amoCRM or Kommo themselves, httpx, pydantic or FastAPI; please report them to their
  owners. If aioamocrm uses one of them in a way that makes it exploitable, that part is in
  scope.
- Your own code: where you keep the client secret and the tokens, your own `TokenStorage`, and
  your webhook endpoint.
- The behaviour the README lists under
  [Limits and roadmap](https://github.com/sinnercode228/aioamocrm/blob/main/README.md#limits-and-roadmap),
  such as the narrowed race between two processes refreshing at once.
- The scripts in `examples/`, unless they show a flaw in the library. A problem in an example
  itself is welcome as an ordinary issue.
