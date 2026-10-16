"""Defensive branches: odd inputs and odd answers."""

from __future__ import annotations

import httpx
import pytest
import respx

from aioamocrm import (
    AmoCRMClient,
    AuthError,
    JsonFileTokenStorage,
    LongLivedToken,
    MemoryTokenStorage,
    OAuth2,
    RateLimiter,
    Task,
    TokenEndpoint,
    TokenSet,
    ValidationError,
    parse_webhook,
)
from aioamocrm._errors import error_from_response, parse_retry_after

from .conftest import TOKEN_URL


def test_reprs_hide_nothing_secret_and_name_the_target() -> None:
    assert repr(AmoCRMClient(subdomain="example", auth=LongLivedToken("t"))) == (
        "AmoCRMClient(base_url='https://example.amocrm.ru')"
    )
    assert repr(RateLimiter(7)) == "RateLimiter(7)"
    assert repr(JsonFileTokenStorage("state/x.json")) == "JsonFileTokenStorage('state/x.json')"


def test_owns_url_with_garbage() -> None:
    amo = AmoCRMClient(subdomain="example", auth=LongLivedToken("t"))
    assert amo.owns_url("https://[::1") is False
    assert amo.owns_url("https://example.amocrm.ru/api/v4/leads") is True


async def test_items_must_be_models_or_mappings(amo: AmoCRMClient) -> None:
    with pytest.raises(TypeError, match="model or a mapping"):
        await amo.leads.create(["not a lead"])  # type: ignore[list-item]


async def test_task_updates_need_ids(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.patch("/api/v4/tasks")
    with pytest.raises(ValueError, match="id"):
        await amo.tasks.update([Task(text="no id")])
    assert not route.called


async def test_an_odd_complex_answer_is_an_empty_result(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.post("/api/v4/leads/complex").mock(return_value=httpx.Response(200, json={"odd": 1}))
    assert await amo.leads.create_complex([{"name": "x"}]) == []


async def test_blank_codes_are_refused() -> None:
    auth = OAuth2("id", "secret", "https://app.example.com/cb", MemoryTokenStorage())
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="code"):
            await auth.exchange_code(TokenEndpoint(http=http, url=TOKEN_URL), "  ")


async def test_a_401_before_any_tokens_is_an_auth_error() -> None:
    auth = OAuth2("id", "secret", "https://app.example.com/cb", MemoryTokenStorage())
    async with httpx.AsyncClient() as http:
        with pytest.raises(AuthError, match="exchange_code"):
            await auth.handle_unauthorized(TokenEndpoint(http=http, url=TOKEN_URL), "stale")


async def test_a_token_answer_that_is_not_an_object() -> None:
    auth = OAuth2("id", "secret", "https://app.example.com/cb", MemoryTokenStorage())
    with respx.mock() as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=["token"]))
        async with httpx.AsyncClient() as http:
            with pytest.raises(AuthError, match="not a JSON object"):
                await auth.exchange_code(TokenEndpoint(http=http, url=TOKEN_URL), "code")


@pytest.mark.parametrize(
    "data",
    [
        {"access_token": "a", "refresh_token": "r", "expires_at": "soon"},
        {"access_token": "a", "refresh_token": "r", "expires_at": 1, "token_type": 5},
    ],
)
def test_stored_token_sets_are_validated(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TokenSet.from_dict(data)


def test_retry_after_dates_without_a_zone_are_ignored() -> None:
    assert parse_retry_after("Fri, 16 Oct 2026 12:00:00") is None


def test_validation_errors_that_are_not_a_list() -> None:
    request = httpx.Request("POST", "https://example.amocrm.ru/api/v4/leads")
    for body in ({"validation-errors": {"0": "x"}}, {"validation-errors": [{"errors": "x"}]}):
        error = error_from_response(httpx.Response(400, json=body, request=request))
        assert isinstance(error, ValidationError)
        assert error.errors == []


def test_webhook_custom_fields_sent_as_objects() -> None:
    # Non-numeric keys keep objects as objects; the helpers read them too.
    event = parse_webhook(
        b"contacts[add][0][id]=1"
        b"&contacts[add][0][custom_fields][phone][id]=3"
        b"&contacts[add][0][custom_fields][phone][code]=PHONE"
        b"&contacts[add][0][custom_fields][phone][values][main][value]=%2B7900"
        b"&contacts[add][0][custom_fields][odd][id]=4"
        b"&contacts[add][0][custom_fields][odd][values]=none"
    ).events[0]
    assert event.field_value("PHONE") == "+7900"
    assert event.field_values(4) == []
    with pytest.raises(TypeError):
        event.field_value(True)


def test_webhook_actions_that_are_not_objects_are_skipped() -> None:
    webhook = parse_webhook(b"leads[add]=1&leads[update][0][id]=2")
    assert [(e.action, e.id) for e in webhook.events] == [("update", 2)]
