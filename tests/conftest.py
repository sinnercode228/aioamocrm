from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

import aioamocrm._client
from aioamocrm import AmoCRMClient, LongLivedToken

BASE = "https://example.amocrm.ru"
API = f"{BASE}/api/v4"
TOKEN_URL = f"{BASE}/oauth2/access_token"
TOKEN = "long-lived-test-token"


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make retry backoff instant and record every delay the client asked for."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(aioamocrm._client, "_sleep", fake_sleep)
    return recorded


@pytest.fixture
async def amo(sleeps: list[float]) -> AsyncIterator[AmoCRMClient]:
    async with AmoCRMClient(
        subdomain="example", auth=LongLivedToken(TOKEN), rate_limit=None
    ) as client:
        yield client
