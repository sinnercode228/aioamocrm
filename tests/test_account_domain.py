"""A refresh refused with 401 or 404 because the account's domain changed.

The official PHP SDK (amocrm/amocrm-api-php, ``AmoCRMOAuth::getAccessTokenByRefreshToken``)
treats such a refusal as a possible change of subdomain: it reads ``api_domain`` from the
access-token JWT, asks ``GET https://{api_domain}/oauth2/account/current/subdomain`` with the
refresh token in ``X-Refresh-Token``, and refreshes again at the domain it is given.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx

from aioamocrm import (
    AccountMovedError,
    AmoCRMClient,
    AuthError,
    MemoryTokenStorage,
    OAuth2,
    TokenEndpoint,
    TokenSet,
)

TOKEN_URL = "https://example.amocrm.ru/oauth2/access_token"
DISCOVERY = "https://api-a.amocrm.ru/oauth2/account/current/subdomain"
NOW = 1_700_000_000.0
MOVED = {"id": 29085955, "subdomain": "newname", "domain": "newname.amocrm.ru"}
REFUSED = {"title": "Not Found", "type": "https://httpstatus.es/404", "status": 404}


def jwt(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'typ': 'JWT', 'alg': 'RS256'})}.{part(claims)}.c2lnbmF0dXJl"


def expired(access_token: str) -> TokenSet:
    return TokenSet(access_token=access_token, refresh_token="refresh-1", expires_at=NOW)


ACCESS = jwt({"aud": "client-id", "api_domain": "api-a.amocrm.ru", "exp": int(NOW)})


def oauth(storage: MemoryTokenStorage) -> OAuth2:
    return OAuth2(
        "client-id", "client-secret", "https://app.example.com/cb", storage, clock=lambda: NOW
    )


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def endpoint() -> AsyncIterator[TokenEndpoint]:
    async with httpx.AsyncClient() as http:
        yield TokenEndpoint(http=http, url=TOKEN_URL)


@pytest.mark.parametrize("status", [401, 404])
async def test_a_refused_refresh_names_the_accounts_new_domain(
    endpoint: TokenEndpoint, mock: respx.MockRouter, status: int
) -> None:
    refresh = mock.post(TOKEN_URL).respond(status, json=REFUSED)
    discovery = mock.get(DISCOVERY).respond(200, json={**MOVED, "top_level_domain": "ru"})
    storage = MemoryTokenStorage(expired(ACCESS))

    with pytest.raises(AccountMovedError) as caught:
        await oauth(storage).get_token(endpoint)

    error = caught.value
    assert isinstance(error, AuthError)
    assert error.domain == "newname.amocrm.ru"
    assert error.status_code == status
    assert "newname.amocrm.ru" in str(error)
    assert "refresh-1" not in str(error)
    assert refresh.call_count == 1
    sent = discovery.calls.last.request
    assert sent.headers["X-Refresh-Token"] == "refresh-1"
    assert "Authorization" not in sent.headers
    # The refused request went to the wrong host, so the refresh token is still good.
    assert await storage.load() == expired(ACCESS)


async def test_a_client_raises_the_new_domain_after_a_401(mock: respx.MockRouter) -> None:
    api = mock.get("https://example.amocrm.ru/api/v4/account").respond(401)
    mock.post(TOKEN_URL).respond(401, json={"title": "Unauthorized", "status": 401})
    mock.get(DISCOVERY).respond(200, json=MOVED)
    valid = TokenSet(access_token=ACCESS, refresh_token="refresh-1", expires_at=NOW + 3600)
    auth = oauth(MemoryTokenStorage(valid))

    async with AmoCRMClient(subdomain="example", auth=auth, rate_limit=None) as amo:
        with pytest.raises(AccountMovedError) as caught:
            await amo.account.get()

    assert caught.value.domain == "newname.amocrm.ru"
    assert api.call_count == 1


@pytest.mark.parametrize(
    ("access_token", "refusal", "answer"),
    [
        # Tokens issued before 21.08.2024 carry no api_domain, and opaque tokens none at all.
        ("access-1", 404, httpx.Response(200, json=MOVED)),
        (jwt({"aud": "client-id"}), 404, httpx.Response(200, json=MOVED)),
        ("eyJhbGciOiJSUzI1NiJ9.bm90IGpzb24.c2ln", 404, httpx.Response(200, json=MOVED)),
        # An api_domain outside amoCRM and Kommo never gets the refresh token.
        (jwt({"api_domain": "api-a.example.com"}), 404, httpx.Response(200, json=MOVED)),
        (jwt({"api_domain": "evil.com/x?.amocrm.ru"}), 404, httpx.Response(200, json=MOVED)),
        # Any other refusal is about the token, not the domain.
        (ACCESS, 400, httpx.Response(200, json=MOVED)),
    ],
)
async def test_no_lookup_without_a_trusted_api_domain_or_for_other_refusals(
    endpoint: TokenEndpoint,
    mock: respx.MockRouter,
    access_token: str,
    refusal: int,
    answer: httpx.Response,
) -> None:
    mock.post(TOKEN_URL).respond(refusal, json=REFUSED)
    lookups = mock.route(method="GET").mock(return_value=answer)

    with pytest.raises(AuthError) as caught:
        await oauth(MemoryTokenStorage(expired(access_token))).get_token(endpoint)

    assert type(caught.value) is AuthError
    assert caught.value.status_code == refusal
    assert not lookups.called


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={**MOVED, "domain": "example.amocrm.ru"}),  # not moved
        httpx.Response(200, json={**MOVED, "domain": "newname.example.com"}),
        httpx.Response(200, json={**MOVED, "domain": None}),
        httpx.Response(200, json=[MOVED]),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(401, json={"title": "Unauthorized"}),
        httpx.Response(302, headers={"Location": "https://elsewhere.example.com/"}),
    ],
)
async def test_an_unhelpful_lookup_keeps_the_original_error(
    endpoint: TokenEndpoint, mock: respx.MockRouter, answer: httpx.Response
) -> None:
    mock.post(TOKEN_URL).respond(404, json=REFUSED)
    elsewhere = mock.get("https://elsewhere.example.com/")
    discovery = mock.get(DISCOVERY).mock(return_value=answer)

    with pytest.raises(AuthError) as caught:
        await oauth(MemoryTokenStorage(expired(ACCESS))).get_token(endpoint)

    assert type(caught.value) is AuthError
    assert caught.value.status_code == 404
    assert discovery.called
    assert not elsewhere.called


async def test_a_lookup_that_cannot_connect_keeps_the_original_error(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    mock.post(TOKEN_URL).respond(404, json=REFUSED)
    mock.get(DISCOVERY).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(AuthError) as caught:
        await oauth(MemoryTokenStorage(expired(ACCESS))).get_token(endpoint)

    assert type(caught.value) is AuthError


async def test_no_lookup_for_a_client_behind_a_proxy(mock: respx.MockRouter) -> None:
    # With base_url pointing at a proxy there is no account host to compare the answer with.
    proxied = "https://amo-proxy.internal/oauth2/access_token"
    mock.post(proxied).respond(404, json=REFUSED)
    discovery = mock.get(DISCOVERY).respond(200, json=MOVED)

    async with httpx.AsyncClient() as http:
        endpoint = TokenEndpoint(http=http, url=proxied)
        with pytest.raises(AuthError) as caught:
            await oauth(MemoryTokenStorage(expired(ACCESS))).get_token(endpoint)

    assert type(caught.value) is AuthError
    assert not discovery.called
