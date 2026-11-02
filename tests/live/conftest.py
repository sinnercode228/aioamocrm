"""The client the live tests use, built from the ``AMOCRM_*`` environment variables.

``AMOCRM_SUBDOMAIN`` and ``AMOCRM_LONG_LIVED_TOKEN`` name the account and its long-lived token;
``AMOCRM_DOMAIN`` is ``amocrm.ru`` when unset or empty. The prefix differs from the ``AMO_*``
variables of the README and the examples on purpose: a shell set up for those never writes to
an account by running pytest.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from aioamocrm import AmoCRMClient, LongLivedToken

SUBDOMAIN = os.environ.get("AMOCRM_SUBDOMAIN", "")
TOKEN = os.environ.get("AMOCRM_LONG_LIVED_TOKEN", "")
DOMAIN = os.environ.get("AMOCRM_DOMAIN") or "amocrm.ru"

#: Applied to every live test module with ``pytestmark``.
LIVE = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (SUBDOMAIN and TOKEN),
        reason="set AMOCRM_SUBDOMAIN and AMOCRM_LONG_LIVED_TOKEN to run the live tests",
    ),
]


@pytest.fixture
async def live_amo() -> AsyncIterator[AmoCRMClient]:
    """An open client of the live account, with the default rate limit and retries."""
    async with AmoCRMClient(subdomain=SUBDOMAIN, domain=DOMAIN, auth=LongLivedToken(TOKEN)) as amo:
        yield amo
