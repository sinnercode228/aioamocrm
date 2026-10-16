"""OAuth2 as the client uses it: header, refresh before expiry, refresh on 401, one refresh."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aioamocrm import (
    AmoCRMClient,
    AuthError,
    LongLivedToken,
    MemoryTokenStorage,
    OAuth2,
    TokenSet,
)

from .conftest import TOKEN_URL

NOW = 1_700_000_000.0
UNAUTHORIZED = {
    "title": "Unauthorized",
    "type": "https://httpstatus.es/401",
    "status": 401,
    "detail": "Unauthorized",
}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


def answer(n: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "expires_in": 86400,
            "access_token": f"access-{n}",
            "refresh_token": f"refresh-{n}",
        },
    )


def bearer(request: httpx.Request) -> str:
    return request.headers["Authorization"]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def storage() -> MemoryTokenStorage:
    return MemoryTokenStorage(
        TokenSet(access_token="access-1", refresh_token="refresh-1", expires_at=NOW + 3600)
    )


@pytest.fixture
async def amo(
    sleeps: list[float], storage: MemoryTokenStorage, clock: Clock
) -> AsyncIterator[AmoCRMClient]:
    auth = OAuth2("client-id", "client-secret", "https://app.example.com/cb", storage, clock=clock)
    async with AmoCRMClient(subdomain="example", auth=auth, rate_limit=None) as client:
        yield client


async def test_oauth_access_token_is_sent_as_bearer(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/account").mock(return_value=httpx.Response(200, json={"id": 1}))
    await amo.request("GET", "account")
    assert bearer(route.calls.last.request) == "Bearer access-1"


async def test_refresh_happens_before_expiry(
    amo: AmoCRMClient, mock: respx.MockRouter, clock: Clock, storage: MemoryTokenStorage
) -> None:
    token = mock.post(TOKEN_URL).mock(return_value=answer(2))
    api = mock.get("/api/v4/account").mock(return_value=httpx.Response(200, json={}))

    clock.now = NOW + 3600 - 30  # 30 s left
    await amo.request("GET", "account")

    assert token.call_count == 1
    assert api.call_count == 1  # no request was wasted on the old token
    assert bearer(api.calls.last.request) == "Bearer access-2"
    saved = await storage.load()
    assert saved is not None
    assert saved.refresh_token == "refresh-2"


async def test_a_401_refreshes_and_retries_once(
    amo: AmoCRMClient, mock: respx.MockRouter, storage: MemoryTokenStorage
) -> None:
    token = mock.post(TOKEN_URL).mock(return_value=answer(2))
    api = mock.post("/api/v4/leads").mock(
        side_effect=[
            httpx.Response(401, json=UNAUTHORIZED),
            httpx.Response(200, json={"_embedded": {"leads": [{"id": 1}]}}),
        ]
    )

    result = await amo.request("POST", "leads", json=[{"name": "Deal"}])

    assert result == {"_embedded": {"leads": [{"id": 1}]}}
    assert token.call_count == 1
    assert [bearer(call.request) for call in api.calls] == ["Bearer access-1", "Bearer access-2"]
    saved = await storage.load()
    assert saved is not None
    assert saved.refresh_token == "refresh-2"


async def test_a_second_401_is_raised_without_another_refresh(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    token = mock.post(TOKEN_URL).mock(return_value=answer(2))
    api = mock.get("/api/v4/account").mock(return_value=httpx.Response(401, json=UNAUTHORIZED))

    with pytest.raises(AuthError) as caught:
        await amo.request("GET", "account")

    assert caught.value.status_code == 401
    assert token.call_count == 1
    assert api.call_count == 2


async def test_concurrent_requests_cause_one_refresh(
    amo: AmoCRMClient, mock: respx.MockRouter, clock: Clock
) -> None:
    async def slow_token(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return answer(2)

    token = mock.post(TOKEN_URL).mock(side_effect=slow_token)
    api = mock.get("/api/v4/account").mock(return_value=httpx.Response(200, json={}))
    clock.now = NOW + 3600  # expired

    await asyncio.gather(*(amo.request("GET", "account") for _ in range(8)))

    assert token.call_count == 1
    assert api.call_count == 8
    assert {bearer(call.request) for call in api.calls} == {"Bearer access-2"}


async def test_concurrent_401s_cause_one_refresh(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    async def slow_token(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return answer(2)

    def api_answer(request: httpx.Request) -> httpx.Response:
        if bearer(request) == "Bearer access-1":
            return httpx.Response(401, json=UNAUTHORIZED)
        return httpx.Response(200, json={})

    token = mock.post(TOKEN_URL).mock(side_effect=slow_token)
    api = mock.get("/api/v4/account").mock(side_effect=api_answer)

    await asyncio.gather(*(amo.request("GET", "account") for _ in range(6)))

    assert token.call_count == 1
    assert api.call_count == 12


async def test_refresh_failure_raises_auth_error(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "hint": "Token has been revoked",
                "title": "Invalid request",
                "status": 400,
                "detail": "The refresh token is invalid.",
            },
        )
    )
    api = mock.get("/api/v4/account").mock(return_value=httpx.Response(401, json=UNAUTHORIZED))

    with pytest.raises(AuthError, match="refresh token is invalid"):
        await amo.request("GET", "account")
    assert api.call_count == 1


async def test_long_lived_token_401_is_final(sleeps: list[float], mock: respx.MockRouter) -> None:
    api = mock.get("/api/v4/account").mock(return_value=httpx.Response(401, json=UNAUTHORIZED))
    async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t"), rate_limit=None) as amo:
        with pytest.raises(AuthError):
            await amo.request("GET", "account")
    assert api.call_count == 1


async def test_exchange_code_through_the_client(
    sleeps: list[float], mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(return_value=answer(1))
    storage = MemoryTokenStorage()
    auth = OAuth2("client-id", "client-secret", "https://app.example.com/cb", storage)
    async with AmoCRMClient(subdomain="example", auth=auth) as amo:
        tokens = await amo.exchange_code("code-1")
    assert tokens.access_token == "access-1"
    assert json.loads(route.calls.last.request.content)["code"] == "code-1"
    assert await storage.load() == tokens


async def test_exchange_code_needs_oauth2() -> None:
    async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t")) as amo:
        with pytest.raises(TypeError, match="OAuth2"):
            await amo.exchange_code("code")
