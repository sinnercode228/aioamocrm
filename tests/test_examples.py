"""The examples run against mocked amoCRM endpoints."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

from aioamocrm import JsonFileTokenStorage, Webhook

from . import payloads
from .conftest import TOKEN_URL
from .test_webhooks import CONTACT_UPDATE, LEAD_STATUS, form

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_oauth_flow_saves_the_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock: respx.MockRouter
) -> None:
    monkeypatch.setenv("AMO_CLIENT_ID", "client-id")
    monkeypatch.setenv("AMO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AMO_REDIRECT_URI", "https://app.example.com/cb")
    token = mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token": "access-1",
                "refresh_token": "refresh-1",
            },
        )
    )
    account = mock.get("/api/v4/account").mock(
        return_value=httpx.Response(200, json=payloads.account())
    )
    path = tmp_path / "tokens.json"

    await load("oauth_flow").main("example.amocrm.ru", "code-1", str(path))

    assert json.loads(token.calls.last.request.content)["code"] == "code-1"
    assert account.calls.last.request.headers["Authorization"] == "Bearer access-1"
    saved = await JsonFileTokenStorage(path).load()
    assert saved is not None
    assert saved.refresh_token == "refresh-1"


async def test_oauth_flow_refuses_a_forged_referer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMO_CLIENT_ID", "client-id")
    monkeypatch.setenv("AMO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AMO_REDIRECT_URI", "https://app.example.com/cb")
    with pytest.raises(ValueError, match="domain"):
        await load("oauth_flow").main("attacker.example.com", "code", str(tmp_path / "t.json"))


async def test_sync_leads_prints_leads_with_contacts(
    monkeypatch: pytest.MonkeyPatch,
    mock: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AMO_SUBDOMAIN", "example")
    monkeypatch.setenv("AMO_LONG_LIVED_TOKEN", "long-lived")
    lead = payloads.lead(
        19619,
        _embedded={
            "tags": [],
            "companies": [],
            "contacts": [{"id": 3, "is_main": True, "_links": payloads.links("contacts/3")}],
        },
    )
    leads = mock.get("/api/v4/leads").mock(
        return_value=httpx.Response(200, json=payloads.page("leads", [lead]))
    )
    contacts = mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact(3)]))
    )

    await load("sync_leads").main(now=1_700_086_400)

    params = leads.calls.last.request.url.params
    assert params["filter[updated_at][from]"] == "1700000000"
    assert params["with"] == "contacts"
    assert contacts.calls.last.request.url.params["filter[id][0]"] == "3"
    out = capsys.readouterr().out
    assert "1 leads changed" in out
    assert "Иван Иванов (main): +79123456789, ivan@example.com" in out


async def test_fastapi_webhook_endpoint() -> None:
    pytest.importorskip("fastapi")
    handled: list[Webhook] = []
    app = load("webhook_fastapi").create_app("s3cret", handled.append)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        wrong = await client.post(
            "/webhooks/amocrm/guess", content=form(LEAD_STATUS), headers=headers
        )
        assert wrong.status_code == 404
        assert handled == []

        ok = await client.post(
            "/webhooks/amocrm/s3cret", content=form(LEAD_STATUS + CONTACT_UPDATE), headers=headers
        )
        assert ok.status_code == 200
        assert ok.json() == {"events": 2}
        assert [(e.entity, e.action) for e in handled[0].events] == [
            ("leads", "status"),
            ("contacts", "update"),
        ]

        bad = await client.post("/webhooks/amocrm/s3cret", content=b"name=%FF", headers=headers)
        assert bad.status_code == 400


async def test_an_app_without_a_secret_accepts_nothing() -> None:
    pytest.importorskip("fastapi")
    app = load("webhook_fastapi").create_app("")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/webhooks/amocrm/anything", content=form(LEAD_STATUS))
    assert response.status_code == 404


def test_the_default_handler_logs_every_kind_of_event(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("fastapi")
    from aioamocrm import parse_webhook

    from .test_webhooks import LEAD_ADD, NOTE_ADD

    caplog.set_level("INFO", logger="amocrm.webhooks")
    body = form(LEAD_STATUS + CONTACT_UPDATE + NOTE_ADD + LEAD_ADD)
    load("webhook_fastapi").handle(parse_webhook(body))
    messages = [record.getMessage() for record in caplog.records]
    assert "lead 25399013 moved from status 32392156 to 142" in messages
    assert "contact 15009433, phone +79123456789" in messages
    assert "note on leads 25399013: Клиент просит перезвонить" in messages
    assert "leads add 25399013" in messages
