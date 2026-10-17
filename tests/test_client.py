from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, AmoCRMError, LongLivedToken, NotFoundError, RateLimiter

from .conftest import API, BASE, TOKEN


@pytest.mark.parametrize(
    ("domain", "origin"),
    [
        ("amocrm.ru", "https://acme.amocrm.ru"),
        ("amocrm.com", "https://acme.amocrm.com"),
        ("kommo.com", "https://acme.kommo.com"),
    ],
)
async def test_subdomain_and_domain_build_the_base_url(domain: str, origin: str) -> None:
    async with AmoCRMClient(subdomain="Acme", domain=domain, auth=LongLivedToken("t")) as amo:
        assert amo.base_url == origin
        assert amo.api_url == f"{origin}/api/v4/"
        assert amo.token_url == f"{origin}/oauth2/access_token"


async def test_an_explicit_base_url_is_used_as_is() -> None:
    async with AmoCRMClient(base_url="http://127.0.0.1:8080/amo/", auth=LongLivedToken("t")) as amo:
        assert amo.base_url == "http://127.0.0.1:8080/amo"
        assert amo.api_url == "http://127.0.0.1:8080/amo/api/v4/"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"subdomain": "acme", "base_url": "https://acme.amocrm.ru"},
        {"subdomain": "acme", "domain": "example.com"},
        {"subdomain": "acme.evil.com"},
        {"subdomain": "-acme"},
        {"base_url": "acme.amocrm.ru"},
        {"base_url": "ftp://acme.amocrm.ru"},
        {"base_url": "https://acme.amocrm.ru?x=1"},
    ],
)
def test_bad_addresses_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        AmoCRMClient(auth=LongLivedToken("t"), **kwargs)  # type: ignore[arg-type]


async def test_long_lived_token_is_sent_as_bearer(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/account").mock(
        return_value=httpx.Response(200, json={"id": 1, "subdomain": "example"})
    )
    assert await amo.request("GET", "account") == {"id": 1, "subdomain": "example"}
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"].startswith("aioamocrm/")


async def test_no_content_is_returned_as_none(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    assert await amo.request("GET", "leads") is None


async def test_paths_and_nested_params(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    await amo.request("GET", "/leads", params={"filter": {"id": [1, 2]}, "with": "contacts"})
    assert route.calls.last.request.url.params.multi_items() == [
        ("filter[id][0]", "1"),
        ("filter[id][1]", "2"),
        ("with", "contacts"),
    ]


async def test_json_body_is_sent(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.post("/api/v4/leads").mock(return_value=httpx.Response(200, json={}))
    await amo.request("POST", "leads", json=[{"name": "Deal"}])
    assert json.loads(route.calls.last.request.content) == [{"name": "Deal"}]
    assert route.calls.last.request.headers["Content-Type"] == "application/json"


async def test_invalid_json_is_an_error(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/account").mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(AmoCRMError, match="not valid JSON"):
        await amo.request("GET", "account")


async def test_errors_are_raised_typed(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/leads/1").mock(
        return_value=httpx.Response(404, json={"title": "Not Found", "status": 404})
    )
    with pytest.raises(NotFoundError):
        await amo.request("GET", "leads/1")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_a_redirect_is_an_error_not_an_empty_list(
    amo: AmoCRMClient, mock: respx.MockRouter, status: int
) -> None:
    # An account moved to another domain, or http:// instead of https://. The body is empty,
    # which must not read as "204, nothing there".
    moved = {"Location": "https://example.kommo.com/api/v4/leads?page=1&limit=250"}
    mock.get("/api/v4/leads").mock(return_value=httpx.Response(status, headers=moved))
    mock.get("/api/v4/leads/7").mock(return_value=httpx.Response(status, headers=moved))

    with pytest.raises(AmoCRMError) as caught:
        [lead async for lead in amo.leads.iterate()]
    assert caught.value.status_code == status
    assert "https://example.kommo.com/api/v4/leads" in str(caught.value)
    assert "limit=250" not in str(caught.value)

    with pytest.raises(AmoCRMError) as caught:
        await amo.leads.get(7)
    assert type(caught.value) is AmoCRMError
    assert caught.value.status_code == status


async def test_a_redirect_is_raised_even_with_a_client_that_follows_redirects(
    mock: respx.MockRouter,
) -> None:
    # httpx would replay this POST as a GET without its body and hand back the GET's answer
    # as if it were the POST's; the client must stop at the 302 whatever http= it was given.
    moved = {"Location": f"{API}/leads/elsewhere"}
    post = mock.post("/api/v4/leads").mock(return_value=httpx.Response(302, headers=moved))
    get = mock.get("/api/v4/leads/elsewhere").mock(
        return_value=httpx.Response(200, json={"_embedded": {"leads": []}})
    )
    async with httpx.AsyncClient(follow_redirects=True) as http:
        amo = AmoCRMClient(
            subdomain="example", auth=LongLivedToken(TOKEN), http=http, rate_limit=None
        )
        with pytest.raises(AmoCRMError) as caught:
            await amo.request("POST", "leads", json=[{"name": "Website order"}])

    assert caught.value.status_code == 302
    assert post.call_count == 1
    assert not get.called


async def test_a_redirect_without_a_location_is_still_an_error(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.get("/api/v4/account").mock(return_value=httpx.Response(304))
    with pytest.raises(AmoCRMError, match="HTTP 304"):
        await amo.request("GET", "account")


async def test_absolute_urls_must_stay_on_the_account(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    assert await amo.request("GET", f"{API}/leads?page=2") is None
    assert route.called
    for foreign in (
        "https://evil.example.com/api/v4/leads",
        "https://example.amocrm.ru.evil.com/api/v4/leads",
        "http://example.amocrm.ru/api/v4/leads",
        "https://example.amocrm.ru:444/api/v4/leads",
        "https://example.amocrm.ru/private/leads",
    ):
        with pytest.raises(ValueError, match="refusing"):
            await amo.request("GET", foreign)


async def test_an_owned_http_client_is_closed() -> None:
    amo = AmoCRMClient(subdomain="example", auth=LongLivedToken("t"))
    async with amo:
        pass
    assert amo.http.is_closed
    await amo.aclose()  # closing twice is harmless


async def test_a_given_http_client_is_left_open() -> None:
    async with httpx.AsyncClient() as http:
        async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t"), http=http) as amo:
            assert amo.http is http
        assert not http.is_closed


async def test_requests_go_through_the_rate_limiter(sleeps: list[float]) -> None:
    calls = 0

    class CountingLimiter(RateLimiter):
        async def acquire(self) -> None:
            nonlocal calls
            calls += 1

    limiter = CountingLimiter(3.0)
    with respx.mock(base_url=BASE) as mock:
        mock.get("/api/v4/account").mock(return_value=httpx.Response(200, json={}))
        async with AmoCRMClient(
            subdomain="example", auth=LongLivedToken("t"), rate_limit=limiter
        ) as amo:
            assert amo.limiter is limiter
            await amo.request("GET", "account")
            await amo.request("GET", "account")
    assert calls == 2


async def test_default_rate_limit_is_seven_per_second() -> None:
    async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t")) as amo:
        assert amo.limiter is not None
        assert amo.limiter.rate == 7.0
    async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t"), rate_limit=2) as amo:
        assert amo.limiter is not None
        assert amo.limiter.rate == 2.0
    async with AmoCRMClient(subdomain="example", auth=LongLivedToken("t"), rate_limit=None) as amo:
        assert amo.limiter is None


async def test_tokens_never_reach_the_logs(
    amo: AmoCRMClient, mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="aioamocrm")
    mock.get("/api/v4/leads").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"_embedded": {"leads": []}})]
    )
    await amo.request("GET", "leads", params={"query": "secret-search"})
    assert caplog.records, "the client should log its requests at DEBUG"
    for record in caplog.records:
        text = record.getMessage()
        assert TOKEN not in text
        assert "secret-search" not in text


async def test_params_are_merged_into_a_url_that_has_a_query(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    await amo.request("GET", f"{API}/leads?page=2&limit=50", params={"with": "contacts"})
    assert route.calls.last.request.url.params.multi_items() == [
        ("page", "2"),
        ("limit", "50"),
        ("with", "contacts"),
    ]
