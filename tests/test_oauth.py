"""OAuth2 against a mocked ``/oauth2/access_token``, shaped like the amoCRM v4 documentation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from aioamocrm import (
    Auth,
    AuthError,
    LongLivedToken,
    MemoryTokenStorage,
    NetworkError,
    OAuth2,
    TokenEndpoint,
    TokenSet,
)

TOKEN_URL = "https://example.amocrm.ru/oauth2/access_token"
NOW = 1_700_000_000.0


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SlowStorage(MemoryTokenStorage):
    """Yields to the event loop inside load/save, so concurrent callers really interleave."""

    def __init__(self, tokens: TokenSet | None = None) -> None:
        super().__init__(tokens)
        self.saved: list[TokenSet] = []

    async def load(self) -> TokenSet | None:
        await asyncio.sleep(0)
        return await super().load()

    async def save(self, tokens: TokenSet) -> None:
        await asyncio.sleep(0.01)
        self.saved.append(tokens)
        await super().save(tokens)


def token_answer(n: int, expires_in: int = 86400) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "expires_in": expires_in,
            "access_token": f"access-{n}",
            "refresh_token": f"refresh-{n}",
        },
    )


@pytest.fixture
async def endpoint() -> AsyncIterator[TokenEndpoint]:
    async with httpx.AsyncClient() as http:
        yield TokenEndpoint(http=http, url=TOKEN_URL)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def oauth(storage: MemoryTokenStorage, clock: Clock | None = None) -> OAuth2:
    return OAuth2(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://app.example.com/amocrm/callback",
        storage=storage,
        clock=clock or Clock(),
    )


def valid_tokens(n: int = 1, expires_at: float = NOW + 3600) -> TokenSet:
    return TokenSet(access_token=f"access-{n}", refresh_token=f"refresh-{n}", expires_at=expires_at)


async def test_exchange_code_posts_the_documented_body_and_saves_the_tokens(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(return_value=token_answer(1))
    storage = MemoryTokenStorage()
    auth = oauth(storage)

    tokens = await auth.exchange_code(endpoint, "auth-code-123")

    assert json.loads(route.calls.last.request.content) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "grant_type": "authorization_code",
        "code": "auth-code-123",
        "redirect_uri": "https://app.example.com/amocrm/callback",
    }
    assert tokens == TokenSet(
        access_token="access-1", refresh_token="refresh-1", expires_at=NOW + 86400
    )
    assert await storage.load() == tokens
    assert await auth.get_token(endpoint) == "access-1"


async def test_a_valid_token_is_used_without_a_request(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL)
    auth = oauth(MemoryTokenStorage(valid_tokens()))
    assert await auth.get_token(endpoint) == "access-1"
    assert not route.called


async def test_the_token_is_refreshed_a_little_before_it_expires(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(return_value=token_answer(2))
    clock = Clock()
    storage = MemoryTokenStorage(valid_tokens(1, expires_at=NOW + 120))
    auth = oauth(storage, clock)

    assert await auth.get_token(endpoint) == "access-1"  # 120 s left: more than the margin
    assert not route.called

    clock.now = NOW + 61  # 59 s left: inside the default 60 s margin
    assert await auth.get_token(endpoint) == "access-2"
    assert json.loads(route.calls.last.request.content) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "grant_type": "refresh_token",
        "refresh_token": "refresh-1",
        "redirect_uri": "https://app.example.com/amocrm/callback",
    }


async def test_the_rotated_refresh_token_is_persisted_and_used_next_time(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(side_effect=[token_answer(2, 100), token_answer(3)])
    clock = Clock()
    storage = MemoryTokenStorage(valid_tokens(1, expires_at=NOW))
    auth = oauth(storage, clock)

    assert await auth.get_token(endpoint) == "access-2"
    saved = await storage.load()
    assert saved is not None
    assert saved.refresh_token == "refresh-2"

    clock.now = NOW + 100
    assert await auth.get_token(endpoint) == "access-3"
    second = json.loads(route.calls[1].request.content)
    assert second["refresh_token"] == "refresh-2"  # the rotated one, not refresh-1


async def test_concurrent_callers_share_one_refresh(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    async def slow_answer(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return token_answer(2)

    route = mock.post(TOKEN_URL).mock(side_effect=slow_answer)
    storage = SlowStorage(valid_tokens(1, expires_at=NOW))
    auth = oauth(storage)

    tokens = await asyncio.gather(*(auth.get_token(endpoint) for _ in range(10)))

    assert tokens == ["access-2"] * 10
    assert route.call_count == 1
    assert [t.access_token for t in storage.saved] == ["access-2"]


async def test_unauthorized_refreshes_once_per_rejected_token(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(return_value=token_answer(2))
    auth = oauth(MemoryTokenStorage(valid_tokens(1)))
    assert await auth.get_token(endpoint) == "access-1"

    results = await asyncio.gather(
        *(auth.handle_unauthorized(endpoint, "access-1") for _ in range(5))
    )

    assert results == [True] * 5
    assert route.call_count == 1
    assert await auth.get_token(endpoint) == "access-2"


async def test_tokens_renewed_by_another_process_are_adopted(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL)
    storage = MemoryTokenStorage(valid_tokens(1))
    auth = oauth(storage)
    assert await auth.get_token(endpoint) == "access-1"

    # Another worker sharing the storage refreshed in the meantime.
    await storage.save(valid_tokens(7, expires_at=NOW + 86400))

    assert await auth.handle_unauthorized(endpoint, "access-1") is True
    assert await auth.get_token(endpoint) == "access-7"
    assert not route.called


REVOKED = {
    "hint": "Token has been revoked",
    "title": "Invalid request",
    "type": "https://developers.amocrm.ru/v3/errors/OAuthProblemJson",
    "status": 400,
    "detail": "The refresh token is invalid.",
}


async def test_two_workers_refreshing_at_once_end_up_with_the_winners_tokens(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    # Both workers read refresh-1 from the shared storage before either saved a new set.
    # amoCRM accepts refresh-1 once; the loser's refusal arrives after the winner saved.
    spent: set[str] = set()

    async def single_use_refresh_tokens(request: httpx.Request) -> httpx.Response:
        refresh_token = json.loads(request.content)["refresh_token"]
        if refresh_token in spent:
            await asyncio.sleep(0.05)
            return httpx.Response(400, json=REVOKED)
        spent.add(refresh_token)
        await asyncio.sleep(0.01)
        return token_answer(2)

    route = mock.post(TOKEN_URL).mock(side_effect=single_use_refresh_tokens)
    storage = MemoryTokenStorage(valid_tokens(1, expires_at=NOW))
    winner, loser = oauth(storage), oauth(storage)

    tokens = await asyncio.gather(winner.get_token(endpoint), loser.get_token(endpoint))

    assert list(tokens) == ["access-2", "access-2"]
    assert route.call_count == 2
    assert await loser.get_token(endpoint) == "access-2"
    assert route.call_count == 2


async def test_a_token_request_does_not_follow_a_redirect_whatever_the_client(
    mock: respx.MockRouter,
) -> None:
    # A 307 would make httpx post the body, client secret and refresh token included, to
    # wherever the Location points.
    elsewhere = mock.post("https://elsewhere.example.com/token").mock(return_value=token_answer(2))
    mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            307, headers={"Location": "https://elsewhere.example.com/token"}
        )
    )
    storage = MemoryTokenStorage(valid_tokens(1, expires_at=NOW))
    async with httpx.AsyncClient(follow_redirects=True) as http:
        endpoint = TokenEndpoint(http=http, url=TOKEN_URL)
        with pytest.raises(AuthError) as caught:
            await oauth(storage).get_token(endpoint)

    assert caught.value.status_code == 307
    assert not elsewhere.called
    assert await storage.load() == valid_tokens(1, expires_at=NOW)


async def test_a_failed_refresh_raises_auth_error_and_keeps_the_old_tokens(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "hint": "Token has been revoked",
                "title": "Invalid request",
                "type": "https://developers.amocrm.ru/v3/errors/OAuthProblemJson",
                "status": 400,
                "detail": "The refresh token is invalid.",
            },
        )
    )
    old = valid_tokens(1, expires_at=NOW)
    storage = MemoryTokenStorage(old)
    auth = oauth(storage)

    with pytest.raises(AuthError) as caught:
        await auth.get_token(endpoint)

    assert caught.value.status_code == 400
    assert "The refresh token is invalid." in str(caught.value)
    assert "client-secret" not in str(caught.value)
    assert "refresh-1" not in str(caught.value)
    assert await storage.load() == old


class SaveFailsOnce(MemoryTokenStorage):
    """A storage whose first save fails, as a full disk or a database outage would."""

    def __init__(self, tokens: TokenSet | None = None) -> None:
        super().__init__(tokens)
        self.failures = 1

    async def save(self, tokens: TokenSet) -> None:
        if self.failures:
            self.failures -= 1
            msg = "disk full"
            raise OSError(msg)
        await super().save(tokens)


async def test_after_a_failed_save_the_next_refresh_uses_the_refresh_token_kept_in_memory(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    route = mock.post(TOKEN_URL).mock(side_effect=[token_answer(2, 100), token_answer(3)])
    clock = Clock()
    storage = SaveFailsOnce(valid_tokens(1, expires_at=NOW))
    auth = oauth(storage, clock)

    with pytest.raises(OSError, match="disk full"):
        await auth.get_token(endpoint)
    # refresh-1 is spent now; refresh-2 exists only in memory, the storage still has set 1.
    assert await auth.get_token(endpoint) == "access-2"

    clock.now = NOW + 100
    assert await auth.get_token(endpoint) == "access-3"
    assert json.loads(route.calls[1].request.content)["refresh_token"] == "refresh-2"
    saved = await storage.load()
    assert saved is not None
    assert saved.refresh_token == "refresh-3"


async def test_an_unreadable_token_answer_is_an_auth_error(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(AuthError, match="not JSON"):
        await oauth(MemoryTokenStorage()).exchange_code(endpoint, "code")


async def test_a_token_answer_without_tokens_is_an_auth_error(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(AuthError, match="access_token"):
        await oauth(MemoryTokenStorage()).exchange_code(endpoint, "code")


async def test_network_failure_on_the_token_endpoint(
    endpoint: TokenEndpoint, mock: respx.MockRouter
) -> None:
    mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(NetworkError):
        await oauth(MemoryTokenStorage(valid_tokens(1, expires_at=NOW))).get_token(endpoint)


async def test_no_tokens_yet_is_an_auth_error(endpoint: TokenEndpoint) -> None:
    with pytest.raises(AuthError, match="exchange_code"):
        await oauth(MemoryTokenStorage()).get_token(endpoint)


async def test_forced_refresh(endpoint: TokenEndpoint, mock: respx.MockRouter) -> None:
    mock.post(TOKEN_URL).mock(return_value=token_answer(5))
    auth = oauth(MemoryTokenStorage(valid_tokens(1)))
    tokens = await auth.refresh(endpoint)
    assert tokens.access_token == "access-5"
    assert await auth.get_token(endpoint) == "access-5"


async def test_long_lived_token_never_refreshes(endpoint: TokenEndpoint) -> None:
    auth = LongLivedToken("long-lived-secret")
    assert isinstance(auth, Auth)
    assert await auth.get_token(endpoint) == "long-lived-secret"
    assert await auth.handle_unauthorized(endpoint, "long-lived-secret") is False
    assert "long-lived-secret" not in repr(auth)


def test_secrets_stay_out_of_repr() -> None:
    auth = oauth(MemoryTokenStorage())
    assert isinstance(auth, Auth)
    assert "client-secret" not in repr(auth)
    assert "client-id" in repr(auth)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_id": "", "client_secret": "s", "redirect_uri": "https://x"},
        {"client_id": "i", "client_secret": "", "redirect_uri": "https://x"},
        {"client_id": "i", "client_secret": "s", "redirect_uri": ""},
        {"client_id": "i", "client_secret": "s", "redirect_uri": "https://x", "refresh_margin": -1},
    ],
)
def test_oauth_arguments_are_validated(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        OAuth2(**kwargs)  # type: ignore[arg-type]


def test_long_lived_token_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="token"):
        LongLivedToken("  ")
