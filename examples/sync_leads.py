"""Print the leads changed in the last 24 hours with their contacts.

Authorization, either one:

* a long-lived token: ``AMO_LONG_LIVED_TOKEN=...``
* OAuth2 tokens saved by ``oauth_flow.py``: ``AMO_CLIENT_ID``, ``AMO_CLIENT_SECRET``,
  ``AMO_REDIRECT_URI`` and ``AMO_TOKENS`` (default ``tokens.json``)

Run::

    AMO_SUBDOMAIN=example AMO_LONG_LIVED_TOKEN=... python examples/sync_leads.py

The leads come page by page (250 per request) through the client's rate limiter, filtered by
``updated_at`` and with ``with=contacts``, which embeds the ids of each lead's contacts. The
contacts themselves are then fetched in groups by ``filter[id]``.
"""

from __future__ import annotations

import asyncio
import os
import time

from aioamocrm import (
    AmoCRMClient,
    Auth,
    Contact,
    JsonFileTokenStorage,
    Lead,
    LongLivedToken,
    OAuth2,
)

DAY = 24 * 60 * 60
# Ids per contacts request: keeps the query string of filter[id][n]=... reasonably short.
CONTACT_IDS_PER_REQUEST = 100


def auth_from_env() -> Auth:
    token = os.environ.get("AMO_LONG_LIVED_TOKEN")
    if token:
        return LongLivedToken(token)
    return OAuth2(
        client_id=os.environ["AMO_CLIENT_ID"],
        client_secret=os.environ["AMO_CLIENT_SECRET"],
        redirect_uri=os.environ["AMO_REDIRECT_URI"],
        storage=JsonFileTokenStorage(os.environ.get("AMO_TOKENS", "tokens.json")),
    )


async def changed_leads(amo: AmoCRMClient, since: int) -> list[Lead]:
    return [
        lead
        async for lead in amo.leads.iterate(
            filter={"updated_at": {"from": since}},
            order={"updated_at": "asc"},
            with_=["contacts"],
        )
    ]


async def contacts_by_id(amo: AmoCRMClient, ids: list[int]) -> dict[int, Contact]:
    found: dict[int, Contact] = {}
    for start in range(0, len(ids), CONTACT_IDS_PER_REQUEST):
        chunk = ids[start : start + CONTACT_IDS_PER_REQUEST]
        async for contact in amo.contacts.iterate(filter={"id": chunk}):
            if contact.id is not None:
                found[contact.id] = contact
    return found


async def main(now: float | None = None) -> None:
    since = int(now if now is not None else time.time()) - DAY
    async with AmoCRMClient(
        subdomain=os.environ["AMO_SUBDOMAIN"],
        domain=os.environ.get("AMO_DOMAIN", "amocrm.ru"),
        auth=auth_from_env(),
    ) as amo:
        leads = await changed_leads(amo, since)
        ids = sorted({c.id for lead in leads for c in lead.embedded.contacts if c.id is not None})
        contacts = await contacts_by_id(amo, ids)

    print(f"{len(leads)} leads changed in the last 24 hours")
    for lead in leads:
        print(f"#{lead.id} {lead.name!r}: {lead.price or 0}, status {lead.status_id}")
        for ref in lead.embedded.contacts:
            contact = contacts.get(ref.id) if ref.id is not None else None
            if contact is None:
                continue
            main_mark = " (main)" if ref.is_main else ""
            phone = contact.field_value("PHONE", "no phone")
            email = contact.field_value("EMAIL", "no email")
            print(f"    {contact.name}{main_mark}: {phone}, {email}")


if __name__ == "__main__":
    asyncio.run(main())
