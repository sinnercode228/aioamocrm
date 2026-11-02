"""The examples run against mocked amoCRM endpoints."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, JsonFileTokenStorage, Webhook, parse_webhook

from . import payloads
from .conftest import TOKEN_URL
from .test_webhooks import CONTACT_UPDATE, LEAD_STATUS, NOTE_ADD, form

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


TILDA_KEY = "tilda-key"
TILDA_FORM = [
    ("Name", "Анна Смирнова"),
    ("Phone", "8 (900) 123-45-67"),
    ("Email", "Anna@Example.com"),
    ("Comment", "Перезвоните после 18:00"),
    ("formid", "form123"),
    ("formname", "Обратный звонок"),
    ("tranid", "1234567:890"),
    ("utm_source", "yandex"),
    ("COOKIES", "utm_source=google; utm_campaign=spring; _ym_uid=1"),
    ("api_key", TILDA_KEY),
]


def tilda_body(pairs: list[tuple[str, str]] = TILDA_FORM) -> bytes:
    return urlencode(pairs).encode()


def test_tilda_reads_the_form_fields() -> None:
    tilda = load("tilda_lead")
    submission = tilda.read_submission(tilda.parse_form(tilda_body()))
    assert submission.name == "Анна Смирнова"
    assert submission.phone == "+79001234567"
    assert submission.email == "anna@example.com"
    assert submission.comment == "Перезвоните после 18:00"
    assert submission.form_name == "Обратный звонок"
    assert submission.tranid == "1234567:890"
    # A utm_ field of the form wins over the same mark in COOKIES.
    assert submission.utm == {"utm_source": "yandex", "utm_campaign": "spring"}


@pytest.mark.parametrize(
    ("raw", "phone"),
    [
        ("8 (900) 123-45-67", "+79001234567"),
        ("+7 900 123 45 67", "+79001234567"),
        ("9001234567", "+79001234567"),
        ("+44 20 7946 0958", "+442079460958"),
        ("доб. 12", "доб. 12"),
        ("", None),
    ],
)
def test_tilda_normalizes_phones(raw: str, phone: str | None) -> None:
    assert load("tilda_lead").normalize_phone(raw) == phone


async def test_tilda_creates_a_lead_with_a_new_contact(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    # The search finds a contact with another phone and email: full-text search is fuzzy.
    search = mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact(3)]))
    )
    complex_ = mock.post("/api/v4/leads/complex").mock(
        return_value=httpx.Response(
            200, json=[{"id": 101, "contact_id": 202, "request_id": ["0"], "merged": False}]
        )
    )
    notes = mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(200, json=payloads.created("notes", [303]))
    )

    result = await load("tilda_lead").handle_form(amo, tilda_body(), api_key=TILDA_KEY)

    assert (result.lead_id, result.contact_id, result.new_contact) == (101, 202, True)
    assert result.problems == []
    assert search.calls.last.request.url.params["query"] == "+79001234567"
    [lead] = json.loads(complex_.calls.last.request.content)
    assert lead["name"] == "Обратный звонок"
    assert lead["_embedded"]["tags"] == [{"name": "tilda"}]
    [contact] = lead["_embedded"]["contacts"]
    assert contact["name"] == "Анна Смирнова"
    assert contact["custom_fields_values"] == [
        {"field_code": "PHONE", "values": [{"value": "+79001234567", "enum_code": "WORK"}]},
        {"field_code": "EMAIL", "values": [{"value": "anna@example.com", "enum_code": "WORK"}]},
    ]
    [note] = json.loads(notes.calls.last.request.content)
    assert note["entity_id"] == 101
    assert note["note_type"] == "common"
    assert note["params"]["text"] == (
        "Перезвоните после 18:00\n\n"
        "Form: Обратный звонок (form123), request 1234567:890\n"
        "utm_campaign: spring\n"
        "utm_source: yandex"
    )


async def test_tilda_attaches_an_existing_contact(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact(3)]))
    )
    leads = mock.post("/api/v4/leads").mock(
        return_value=httpx.Response(200, json=payloads.created("leads", [101]))
    )
    link = mock.post("/api/v4/leads/101/link").mock(
        return_value=httpx.Response(
            200,
            json={
                "_embedded": {
                    "links": [
                        {
                            "entity_id": 101,
                            "entity_type": "leads",
                            "to_entity_id": 3,
                            "to_entity_type": "contacts",
                            "metadata": {"is_main": True},
                        }
                    ]
                }
            },
        )
    )
    mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(200, json=payloads.created("notes", [303]))
    )
    pairs = [(k, "8 912 345-67-89" if k == "Phone" else v) for k, v in TILDA_FORM]

    result = await load("tilda_lead").handle_form(amo, tilda_body(pairs), api_key=TILDA_KEY)

    assert (result.lead_id, result.contact_id, result.new_contact) == (101, 3, False)
    assert result.problems == []
    [lead] = json.loads(leads.calls.last.request.content)
    assert lead == {"name": "Обратный звонок", "_embedded": {"tags": [{"name": "tilda"}]}}
    assert json.loads(link.calls.last.request.content) == [
        {"to_entity_id": 3, "to_entity_type": "contacts", "metadata": {"is_main": True}}
    ]


async def test_tilda_finds_a_contact_by_email_when_there_is_no_phone(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    search = mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact(3)]))
    )
    mock.post("/api/v4/leads").mock(
        return_value=httpx.Response(200, json=payloads.created("leads", [101]))
    )
    mock.post("/api/v4/leads/101/link").mock(return_value=httpx.Response(200, json={}))
    notes = mock.post("/api/v4/leads/notes")
    pairs = [("Email", "IVAN@example.com"), ("formname", ""), ("api_key", TILDA_KEY)]

    result = await load("tilda_lead").handle_form(amo, tilda_body(pairs), api_key=TILDA_KEY)

    assert (result.lead_id, result.contact_id) == (101, 3)
    assert search.calls.last.request.url.params["query"] == "ivan@example.com"
    assert not notes.called  # nothing to note: no comment, form name or marks


async def test_tilda_creates_a_lead_without_a_contact_when_the_form_has_none(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    search = mock.get("/api/v4/contacts")
    leads = mock.post("/api/v4/leads").mock(
        return_value=httpx.Response(200, json=payloads.created("leads", [101]))
    )
    notes = mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(200, json=payloads.created("notes", [303]))
    )
    pairs = [("Comment", "Сколько стоит доставка?"), ("api_key", TILDA_KEY)]

    result = await load("tilda_lead").handle_form(amo, tilda_body(pairs), api_key=TILDA_KEY)

    assert (result.lead_id, result.contact_id, result.new_contact) == (101, None, False)
    assert not search.called
    [lead] = json.loads(leads.calls.last.request.content)
    assert lead == {"name": "Заявка с сайта", "_embedded": {"tags": [{"name": "tilda"}]}}
    [note] = json.loads(notes.calls.last.request.content)
    assert note["params"]["text"] == "Сколько стоит доставка?"


async def test_tilda_keeps_the_lead_when_the_note_fails(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.get("/api/v4/contacts").mock(return_value=httpx.Response(204))
    mock.post("/api/v4/leads/complex").mock(
        return_value=httpx.Response(200, json=[{"id": 101, "contact_id": 202}])
    )
    notes = mock.post("/api/v4/leads/notes").mock(return_value=httpx.Response(500))

    result = await load("tilda_lead").handle_form(amo, tilda_body(), api_key=TILDA_KEY)

    assert result.lead_id == 101
    assert notes.call_count == 1  # a write is not resent
    [problem] = result.problems
    assert problem.startswith("note: ")


async def test_tilda_keeps_the_lead_when_the_link_fails(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact(3)]))
    )
    mock.post("/api/v4/leads").mock(
        return_value=httpx.Response(200, json=payloads.created("leads", [101]))
    )
    mock.post("/api/v4/leads/101/link").mock(return_value=httpx.Response(502))
    notes = mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(200, json=payloads.created("notes", [303]))
    )
    pairs = [("Email", "ivan@example.com"), ("Comment", "Нужен счёт"), ("api_key", TILDA_KEY)]

    result = await load("tilda_lead").handle_form(amo, tilda_body(pairs), api_key=TILDA_KEY)

    assert (result.lead_id, result.contact_id) == (101, 3)
    [problem] = result.problems
    assert problem.startswith("link: ")
    assert notes.called  # the note still goes to the lead


async def test_tilda_answers_the_test_request_and_refuses_a_wrong_key(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    tilda = load("tilda_lead")
    ping = tilda_body([("test", "test"), ("api_key", TILDA_KEY)])
    assert await tilda.handle_form(amo, ping, api_key=TILDA_KEY) is None
    with pytest.raises(PermissionError):
        await tilda.handle_form(amo, tilda_body(), api_key="other")
    with pytest.raises(PermissionError):
        await tilda.handle_form(amo, tilda_body(), api_key="")
    assert not mock.calls


async def test_tilda_main_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMO_SUBDOMAIN", "example")
    monkeypatch.setenv("AMO_LONG_LIVED_TOKEN", "long-lived")
    monkeypatch.setenv("TILDA_API_KEY", TILDA_KEY)
    mock.get("/api/v4/contacts").mock(return_value=httpx.Response(204))
    complex_ = mock.post("/api/v4/leads/complex").mock(
        return_value=httpx.Response(200, json=[{"id": 101, "contact_id": 202}])
    )
    mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(200, json=payloads.created("notes", [303]))
    )

    await load("tilda_lead").main(tilda_body())

    assert complex_.calls.last.request.headers["Authorization"] == "Bearer long-lived"
    assert capsys.readouterr().out == "lead 101, new contact 202\n"


def test_webhook_dedupe_keys_name_one_change() -> None:
    dedupe = load("webhook_dedupe")
    [status] = parse_webhook(form(LEAD_STATUS)).events
    [note] = parse_webhook(form(NOTE_ADD)).events
    webhook = parse_webhook(form(LEAD_STATUS))
    assert dedupe.event_key(webhook, status) == "29085955:leads:25399013:status:1413554500"
    assert dedupe.event_key(webhook, note) == "29085955:leads:76787983:note:1413554700"


def test_webhook_dedupe_forgets_keys_after_the_ttl() -> None:
    now = [1000.0]
    keys = load("webhook_dedupe").RecentKeys(ttl=60, clock=lambda: now[0])
    assert keys.claim("a")
    assert not keys.claim("a")
    now[0] += 61
    assert keys.claim("a")
    assert len(keys) == 1


def test_webhook_dedupe_skips_a_resent_webhook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.txt"
    first.write_bytes(form(LEAD_STATUS + CONTACT_UPDATE + NOTE_ADD))
    resent = tmp_path / "resent.txt"
    resent.write_bytes(form(LEAD_STATUS))
    moved_again = [
        (k, "1413554900" if k.endswith("[last_modified]") else v) for k, v in LEAD_STATUS
    ]
    later = tmp_path / "later.txt"
    later.write_bytes(form(moved_again))

    load("webhook_dedupe").main([str(first), str(resent), str(later)])

    assert capsys.readouterr().out.splitlines() == [
        "new: 29085955:leads:25399013:status:1413554500",
        "new: 29085955:leads:76787983:note:1413554700",
        "new: 29085955:contacts:15009433:update:1413554600",
        "duplicate: 29085955:leads:25399013:status:1413554500",
        "new: 29085955:leads:25399013:status:1413554900",
    ]
