"""Retries against mocked 429 / 5xx / transport failures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aioamocrm import (
    AmoCRMClient,
    LongLivedToken,
    NetworkError,
    NotFoundError,
    RateLimitError,
    RetryPolicy,
    ServerError,
)

TOO_MANY = {"title": "Too Many Requests", "status": 429, "detail": "Too many requests"}
OK = httpx.Response(200, json={"id": 1})


@pytest.fixture
async def amo(sleeps: list[float]) -> AsyncIterator[AmoCRMClient]:
    policy = RetryPolicy(max_attempts=3, backoff_base=1.0, backoff_max=10.0, max_retry_after=30.0)
    async with AmoCRMClient(
        subdomain="example", auth=LongLivedToken("t"), rate_limit=None, retry=policy
    ) as client:
        yield client


async def test_429_with_retry_after_waits_that_long(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = mock.get("/api/v4/account").mock(
        side_effect=[httpx.Response(429, json=TOO_MANY, headers={"Retry-After": "2"}), OK]
    )
    assert await amo.request("GET", "account") == {"id": 1}
    assert route.call_count == 2
    assert sleeps == [2.0]


async def test_429_without_retry_after_backs_off_with_jitter(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = mock.get("/api/v4/account").mock(
        side_effect=[httpx.Response(429, json=TOO_MANY), httpx.Response(429, json=TOO_MANY), OK]
    )
    assert await amo.request("GET", "account") == {"id": 1}
    assert route.call_count == 3
    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.0
    assert 1.0 <= sleeps[1] <= 2.0


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_429_is_not_retried_for_writes_by_default(
    amo: AmoCRMClient, mock: respx.MockRouter, method: str
) -> None:
    # Nothing in the documentation promises that a 429 write was not applied, and amoCRM has
    # no idempotency keys: sending it again could create the lead twice.
    route = mock.route(method=method, path="/api/v4/leads").mock(
        side_effect=[httpx.Response(429, json=TOO_MANY, headers={"Retry-After": "1"}), OK]
    )
    with pytest.raises(RateLimitError) as caught:
        await amo.request(method, "leads", json=[{"name": "x"}])
    assert route.call_count == 1
    assert caught.value.retry_after == 1.0


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_429_is_retried_for_writes_when_the_caller_opts_in(
    amo: AmoCRMClient, mock: respx.MockRouter, method: str, sleeps: list[float]
) -> None:
    route = mock.route(method=method, path="/api/v4/leads").mock(
        side_effect=[httpx.Response(429, json=TOO_MANY, headers={"Retry-After": "1"}), OK]
    )
    assert await amo.request(method, "leads", json=[{"name": "x"}], retry=True) == {"id": 1}
    assert route.call_count == 2
    assert sleeps == [1.0]


async def test_get_can_opt_out_of_429_retries(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/account").mock(side_effect=[httpx.Response(429), OK])
    with pytest.raises(RateLimitError):
        await amo.request("GET", "account", retry=False)
    assert route.call_count == 1


async def test_429_gives_up_after_the_last_attempt(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = mock.get("/api/v4/account").mock(
        return_value=httpx.Response(429, json=TOO_MANY, headers={"Retry-After": "1"})
    )
    with pytest.raises(RateLimitError) as caught:
        await amo.request("GET", "account")
    assert route.call_count == 3
    assert sleeps == [1.0, 1.0]
    assert caught.value.retry_after == 1.0


async def test_a_retry_after_longer_than_allowed_is_raised_at_once(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = mock.get("/api/v4/account").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"})
    )
    with pytest.raises(RateLimitError) as caught:
        await amo.request("GET", "account")
    assert route.call_count == 1
    assert sleeps == []
    assert caught.value.retry_after == 3600.0


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_is_retried_for_get(
    amo: AmoCRMClient, mock: respx.MockRouter, status: int
) -> None:
    route = mock.get("/api/v4/account").mock(side_effect=[httpx.Response(status), OK])
    assert await amo.request("GET", "account") == {"id": 1}
    assert route.call_count == 2


async def test_5xx_retry_after_is_honoured(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float]
) -> None:
    mock.get("/api/v4/account").mock(
        side_effect=[httpx.Response(503, headers={"Retry-After": "4"}), OK]
    )
    await amo.request("GET", "account")
    assert sleeps == [4.0]


async def test_5xx_gives_up_after_the_last_attempt(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/account").mock(return_value=httpx.Response(502))
    with pytest.raises(ServerError) as caught:
        await amo.request("GET", "account")
    assert caught.value.status_code == 502
    assert route.call_count == 3


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_5xx_is_not_retried_for_writes_by_default(
    amo: AmoCRMClient, mock: respx.MockRouter, method: str
) -> None:
    route = mock.route(method=method, path="/api/v4/leads").mock(
        side_effect=[httpx.Response(500), OK]
    )
    with pytest.raises(ServerError):
        await amo.request(method, "leads", json=[{"name": "x"}])
    assert route.call_count == 1


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_5xx_is_retried_for_writes_when_the_caller_opts_in(
    amo: AmoCRMClient, mock: respx.MockRouter, method: str
) -> None:
    route = mock.route(method=method, path="/api/v4/leads").mock(
        side_effect=[httpx.Response(500), OK]
    )
    assert await amo.request(method, "leads", json=[{"name": "x"}], retry=True) == {"id": 1}
    assert route.call_count == 2


async def test_get_can_opt_out_of_5xx_retries(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/account").mock(side_effect=[httpx.Response(500), OK])
    with pytest.raises(ServerError):
        await amo.request("GET", "account", retry=False)
    assert route.call_count == 1


async def test_4xx_other_than_429_is_not_retried(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/leads/5").mock(return_value=httpx.Response(404))
    with pytest.raises(NotFoundError):
        await amo.request("GET", "leads/5")
    assert route.call_count == 1


async def test_read_timeouts_are_retried_for_get(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/account").mock(side_effect=[httpx.ReadTimeout("slow"), OK])
    assert await amo.request("GET", "account") == {"id": 1}
    assert route.call_count == 2


async def test_read_timeouts_are_not_retried_for_post(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/leads").mock(side_effect=[httpx.ReadTimeout("slow"), OK])
    with pytest.raises(NetworkError, match="ReadTimeout"):
        await amo.request("POST", "leads", json=[])
    assert route.call_count == 1


async def test_connect_errors_are_retried_even_for_post(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/leads").mock(side_effect=[httpx.ConnectError("refused"), OK])
    assert await amo.request("POST", "leads", json=[]) == {"id": 1}
    assert route.call_count == 2


async def test_network_errors_give_up_after_the_last_attempt(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/account").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(NetworkError) as caught:
        await amo.request("GET", "account")
    assert route.call_count == 3
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


async def test_a_single_attempt_policy_never_retries(
    sleeps: list[float], mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/account").mock(return_value=httpx.Response(429))
    async with AmoCRMClient(
        subdomain="example",
        auth=LongLivedToken("t"),
        rate_limit=None,
        retry=RetryPolicy(max_attempts=1),
    ) as amo:
        with pytest.raises(RateLimitError):
            await amo.request("GET", "account")
    assert route.call_count == 1
    assert sleeps == []


async def test_429_with_asctime_retry_after_waits_that_long(
    amo: AmoCRMClient, mock: respx.MockRouter, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seven seconds before Sun Nov 6 08:49:37 1994 UTC; no wall-clock dependence.
    monkeypatch.setattr("aioamocrm._errors.time.time", lambda: 784111770.0)
    route = mock.get("/api/v4/account").mock(
        side_effect=[
            httpx.Response(429, json=TOO_MANY, headers={"Retry-After": "Sun Nov  6 08:49:37 1994"}),
            OK,
        ]
    )
    assert await amo.request("GET", "account") == {"id": 1}
    assert route.call_count == 2
    assert sleeps == [7.0]
