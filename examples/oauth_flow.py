"""Exchange an amoCRM authorization code for tokens and save them to a JSON file.

1. Create an integration in amoCRM (Settings, Integrations) and set its redirect URI.
2. Let the account owner allow access. amoCRM redirects to the redirect URI with
   ``?code=...&referer=example.amocrm.ru&state=...``.
3. Within 20 minutes (the code lifetime given in the amoCRM documentation) run::

       export AMO_CLIENT_ID=... AMO_CLIENT_SECRET=... AMO_REDIRECT_URI=https://app.example.com/cb
       python examples/oauth_flow.py --referer example.amocrm.ru --code def50200...

The tokens land in ``tokens.json`` (``--tokens`` to change it), written atomically with mode
0600. Later runs, such as ``sync_leads.py``, use the same file: the client refreshes the access
token before it expires and saves the new refresh token every time.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from aioamocrm import AmoCRMClient, JsonFileTokenStorage, OAuth2


def oauth_from_env(tokens_path: str) -> OAuth2:
    return OAuth2(
        client_id=os.environ["AMO_CLIENT_ID"],
        client_secret=os.environ["AMO_CLIENT_SECRET"],
        redirect_uri=os.environ["AMO_REDIRECT_URI"],
        storage=JsonFileTokenStorage(tokens_path),
    )


async def main(referer: str, code: str, tokens_path: str) -> None:
    # The referer comes from a redirect anyone can forge. Splitting it into subdomain and
    # domain lets the client check both (amocrm.ru, amocrm.com or kommo.com only), so the
    # client secret is never sent to some other host.
    subdomain, _, domain = referer.strip().partition(".")
    oauth = oauth_from_env(tokens_path)
    async with AmoCRMClient(subdomain=subdomain, domain=domain, auth=oauth) as amo:
        tokens = await amo.exchange_code(code)
        valid_until = datetime.fromtimestamp(tokens.expires_at, tz=timezone.utc)
        print(
            f"Tokens saved to {tokens_path}; the access token is valid until {valid_until:%c} UTC"
        )
        account = await amo.account.get()
        print(f"Connected to account {account.name!r} ({account.subdomain}, id {account.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--referer", required=True, help="the referer parameter of the redirect")
    parser.add_argument("--code", required=True, help="the code parameter of the redirect")
    parser.add_argument("--tokens", default="tokens.json", help="where to save the tokens")
    args = parser.parse_args()
    asyncio.run(main(args.referer, args.code, args.tokens))
