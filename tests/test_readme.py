"""Every Python sample in README.md and README.ru.md runs against mocked amoCRM endpoints.

Each ``python`` or ``pycon`` block in the READMEs follows a ``<!-- sample: name -->`` marker.
Both READMEs carry the same samples (comments and texts translated); every sample has a test
here that mocks the endpoints it calls and checks the requests it sent. ``pycon`` blocks run as
doctests. Fragments (code with top-level ``await``) run with ``amo``, an open client of
``example.amocrm.ru``.
"""

from __future__ import annotations

import ast
import asyncio
import doctest
import inspect
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, JsonFileTokenStorage, LongLivedToken, TokenSet, TokenStorage

from . import payloads
from .conftest import BASE, TOKEN, TOKEN_URL

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.ru.md")

_MARKED = re.compile(r"<!-- sample: ([a-z0-9-]+) -->\n```(python|pycon)\n(.*?)\n```", re.S)
_FENCE = re.compile(r"^```(?:python|pycon)$", re.M)


def samples(readme: str) -> dict[str, str]:
    text = (ROOT / readme).read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for match in _MARKED.finditer(text):
        name = match.group(1)
        assert name not in found, f"{readme}: sample {name} appears twice"
        found[name] = match.group(3) + "\n"
    blocks = len(_FENCE.findall(text))
    assert len(found) == blocks, f"{readme}: {blocks - len(found)} Python blocks have no marker"
    return found


def sample(readme: str, name: str) -> str:
    return samples(readme)[name]


def body(call: Any) -> Any:
    return json.loads(call.request.content)


def run_script(code: str) -> dict[str, Any]:
    """Run a whole program, as ``python sample.py`` would."""
    namespace: dict[str, Any] = {"__name__": "__main__"}
    exec(compile(code, "<readme>", "exec"), namespace)  # noqa: S102 - the README's own code
    return namespace


def load_module(code: str) -> dict[str, Any]:
    """Run definitions only: the test calls what the sample defines."""
    namespace: dict[str, Any] = {"__name__": "readme_sample"}
    exec(compile(code, "<readme>", "exec"), namespace)  # noqa: S102 - the README's own code
    return namespace


def run_fragment(code: str) -> dict[str, Any]:
    """Run code with top-level ``await`` inside an open client named ``amo``."""
    compiled = compile(code, "<readme>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    namespace: dict[str, Any] = {"__name__": "readme_sample"}

    async def go() -> None:
        async with AmoCRMClient(
            subdomain="example", auth=LongLivedToken(TOKEN), rate_limit=None
        ) as amo:
            namespace["amo"] = amo
            result = eval(compiled, namespace)  # noqa: S307 - the README's own code
            if inspect.iscoroutine(result):
                await result

    asyncio.run(go())
    return namespace


def run_doctest(code: str, name: str) -> None:
    test = doctest.DocTestParser().get_doctest(code, {}, name, None, 0)
    runner = doctest.DocTestRunner(optionflags=doctest.NORMALIZE_WHITESPACE)
    report: list[str] = []
    failed, attempted = runner.run(test, out=report.append)
    assert attempted > 0
    assert failed == 0, "".join(report)


# ---- the README structure ----------------------------------------------------------------------


def test_both_readmes_carry_the_same_samples() -> None:
    english, russian = (set(samples(readme)) for readme in READMES)
    assert english == russian
    tested = {
        name.removeprefix("test_").replace("_", "-")
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    }
    assert english <= tested, f"samples without a test: {sorted(english - tested)}"


@pytest.mark.parametrize("readme", READMES)
def test_each_readme_links_the_other(readme: str) -> None:
    head = (ROOT / readme).read_text(encoding="utf-8").splitlines()[:5]
    other = next(name for name in READMES if name != readme)
    assert any(other in line for line in head)


# ---- the samples, in README order ---------------------------------------------------------------


@pytest.mark.parametrize("readme", READMES)
def test_quickstart(
    readme: str,
    mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AMO_SUBDOMAIN", "example")
    monkeypatch.setenv("AMO_LONG_LIVED_TOKEN", TOKEN)
    account = mock.get("/api/v4/account").respond(200, json=payloads.account())
    create = mock.post("/api/v4/leads").respond(200, json=payloads.created("leads", [9993813]))
    leads = mock.get("/api/v4/leads").respond(
        200, json=payloads.page("leads", [payloads.lead(19619), payloads.lead(19620)])
    )

    run_script(sample(readme, "quickstart"))

    assert account.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"
    [lead] = body(create.calls.last)
    assert set(lead) == {"name", "price"}
    params = leads.calls.last.request.url.params
    assert int(params["filter[updated_at][from]"]) > time.time() - 25 * 3600
    assert (params["page"], params["limit"]) == ("1", "250")
    out = capsys.readouterr().out
    assert "9993813" in out
    assert "19619" in out
    assert "19620" in out


@pytest.mark.parametrize("readme", READMES)
def test_oauth(
    readme: str,
    mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMO_CLIENT_ID", "client-id")
    monkeypatch.setenv("AMO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AMO_REDIRECT_URI", "https://app.example.com/amocrm/callback")
    issued = iter([("access-1", "refresh-1"), ("access-2", "refresh-2")])

    def token_answer(request: httpx.Request) -> httpx.Response:
        access, refresh = next(issued)
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token": access,
                "refresh_token": refresh,
            },
        )

    token = mock.post(TOKEN_URL).mock(side_effect=token_answer)
    account = mock.get("/api/v4/account").respond(200, json=payloads.account())
    code = sample(readme, "oauth")

    first_run = load_module(code)
    asyncio.run(first_run["save_first_tokens"]("def50200-code"))

    exchange = body(token.calls.last)
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "def50200-code"
    assert exchange["redirect_uri"] == "https://app.example.com/amocrm/callback"
    storage = JsonFileTokenStorage(tmp_path / "tokens" / "example.json")
    saved = asyncio.run(storage.load())
    assert saved is not None
    assert saved.refresh_token == "refresh-1"

    # A day later: the access token in the file has expired.
    expired = TokenSet(access_token="access-1", refresh_token="refresh-1", expires_at=1.0)
    asyncio.run(storage.save(expired))
    later_run = load_module(code)
    asyncio.run(later_run["print_account"]())

    refresh = body(token.calls.last)
    assert refresh["grant_type"] == "refresh_token"
    assert refresh["refresh_token"] == "refresh-1"
    assert account.calls.last.request.headers["Authorization"] == "Bearer access-2"
    renewed = asyncio.run(storage.load())
    assert renewed is not None
    assert renewed.refresh_token == "refresh-2"
    assert "example" in capsys.readouterr().out


@pytest.mark.parametrize("readme", READMES)
def test_token_storage(readme: str, tmp_path: Path) -> None:
    module = load_module(sample(readme, "token-storage"))
    storage_class = module["SqliteTokenStorage"]
    path = str(tmp_path / "tokens.db")
    first = storage_class(path, "example")
    second = storage_class(path, "other")
    assert isinstance(first, TokenStorage)
    tokens = TokenSet(access_token="a", refresh_token="r", expires_at=1_700_000_000.0)
    newer = TokenSet(access_token="a2", refresh_token="r2", expires_at=1_700_086_400.0)

    async def scenario() -> None:
        assert await first.load() is None
        await first.save(tokens)
        assert await first.load() == tokens
        assert await second.load() is None
        await first.save(newer)
        assert await storage_class(path, "example").load() == newer

    asyncio.run(scenario())


@pytest.mark.parametrize("readme", READMES)
def test_pagination(
    readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    contact_ref = {"id": 3, "is_main": True, "_links": payloads.links("contacts/3")}
    embedded = {"tags": [], "companies": [], "contacts": [contact_ref]}

    def leads_answer(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        count = 250 if number == 1 else 3
        items = [payloads.lead(number * 1000 + n, _embedded=embedded) for n in range(count)]
        page = payloads.page("leads", items, number=number, has_next=number == 1)
        return httpx.Response(200, json=page)

    leads = mock.get("/api/v4/leads").mock(side_effect=leads_answer)
    contacts = mock.get("/api/v4/contacts").respond(
        200, json=payloads.page("contacts", [payloads.contact(3)], number=2, limit=50)
    )

    run_fragment(sample(readme, "pagination"))

    assert leads.call_count == 2
    first = leads.calls[0].request.url.params
    assert first["filter[statuses][0][pipeline_id]"] == "3177727"
    assert first["filter[statuses][0][status_id]"] == "142"
    assert first["order[updated_at]"] == "desc"
    assert first["with"] == "contacts"
    second = leads.calls[1].request.url.params
    assert (second["page"], second["with"]) == ("2", "contacts")
    params = contacts.calls.last.request.url.params
    assert (params["page"], params["limit"], params["query"]) == ("2", "50", "+7912")
    out = capsys.readouterr().out
    assert "2002" in out
    assert "[3]" in out


@pytest.mark.parametrize("readme", READMES)
def test_request(readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    event = {
        "id": "01h2x3y4z5",
        "type": "lead_status_changed",
        "entity_id": 19619,
        "entity_type": "lead",
        "created_at": 1700000000,
    }
    events = mock.get("/api/v4/events").respond(
        200, json={"_page": 1, "_links": payloads.links("events"), "_embedded": {"events": [event]}}
    )

    run_fragment(sample(readme, "request"))

    assert events.calls.last.request.url.params["limit"] == "100"
    assert "lead_status_changed 19619" in capsys.readouterr().out


@pytest.mark.parametrize("readme", READMES)
def test_complex(readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    route = mock.post("/api/v4/leads/complex").respond(
        200,
        json=[
            {
                "id": 9993813,
                "contact_id": 15009433,
                "company_id": 15009435,
                "request_id": ["0"],
                "merged": False,
            }
        ],
    )

    run_fragment(sample(readme, "complex"))

    [lead] = body(route.calls.last)
    assert lead["price"] == 5000
    assert lead["pipeline_id"] == 3177727
    [contact] = lead["_embedded"]["contacts"]
    codes = [
        (f["field_code"], f["values"][0]["enum_code"]) for f in contact["custom_fields_values"]
    ]
    assert codes == [("PHONE", "WORK"), ("EMAIL", "WORK")]
    assert "@" in contact["custom_fields_values"][1]["values"][0]["value"]
    [company] = lead["_embedded"]["companies"]
    assert set(company) == {"name"}
    [tag] = lead["_embedded"]["tags"]
    assert set(tag) == {"name"}
    assert "9993813 15009433 15009435 False" in capsys.readouterr().out


@pytest.mark.parametrize("readme", READMES)
def test_link(readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    link = {
        "entity_id": 19619,
        "entity_type": "leads",
        "to_entity_id": 15009433,
        "to_entity_type": "contacts",
        "metadata": {"is_main": True},
    }
    route = mock.post("/api/v4/leads/19619/link").respond(
        200, json={"_links": payloads.links("leads/19619/link"), "_embedded": {"links": [link]}}
    )
    links = mock.get("/api/v4/leads/19619/links").respond(
        200, json={"_links": payloads.links("leads/19619/links"), "_embedded": {"links": [link]}}
    )

    run_fragment(sample(readme, "link"))

    assert body(route.calls.last) == [
        {"to_entity_id": 15009433, "to_entity_type": "contacts", "metadata": {"is_main": True}}
    ]
    assert links.calls.last.request.url.params["filter[to_entity_type]"] == "contacts"
    assert "contacts 15009433" in capsys.readouterr().out


@pytest.mark.parametrize("readme", READMES)
def test_batch_errors(
    readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(
        [
            httpx.Response(200, json=payloads.created("leads", list(range(1000, 1050)))),
            httpx.Response(
                400,
                json={
                    "validation-errors": [
                        {
                            "request_id": "3",
                            "errors": [
                                {
                                    "code": "InvalidType",
                                    "path": "price",
                                    "detail": "This value should be of type int.",
                                }
                            ],
                        }
                    ],
                    "title": "Bad Request",
                    "type": "https://httpstatus.es/400",
                    "status": 400,
                    "detail": "Request validation failed",
                },
            ),
        ]
    )
    route = mock.post("/api/v4/leads").mock(side_effect=lambda request: next(answers))

    run_fragment(sample(readme, "batch-errors"))

    assert [len(body(call)) for call in route.calls] == [50, 50]
    out = capsys.readouterr().out
    assert "50 50" in out
    assert "53 price InvalidType" in out


@pytest.mark.parametrize("readme", READMES)
def test_fields_read(readme: str) -> None:
    run_doctest(sample(readme, "fields-read"), "fields-read")


@pytest.mark.parametrize("readme", READMES)
def test_fields_write(readme: str) -> None:
    run_doctest(sample(readme, "fields-write"), "fields-write")


@pytest.mark.parametrize("readme", READMES)
def test_fields_update(readme: str, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/contacts/3").respond(200, json=payloads.contact(3))
    route = mock.patch("/api/v4/contacts").respond(200, json=payloads.updated("contacts", [3]))

    run_fragment(sample(readme, "fields-update"))

    [update] = body(route.calls.last)
    assert update["id"] == 3
    [phones] = update["custom_fields_values"]
    assert phones["field_code"] == "PHONE"
    values = [(v["value"], v.get("enum_code")) for v in phones["values"]]
    assert values == [
        ("+79123456789", "WORK"),
        ("+79990001122", "MOB"),
        ("+79001112233", "WORK"),
    ]


@pytest.mark.parametrize("readme", READMES)
def test_custom_fields(
    readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    lead_field = payloads.custom_field(
        427661, name="Budget", type="numeric", code=None, enums=None, entity_type="leads"
    )
    mock.get("/api/v4/leads/custom_fields").respond(
        200, json=payloads.page("custom_fields", [lead_field], path="leads/custom_fields")
    )
    options = [
        {"id": 4567, "value": "Website", "sort": 1},
        {"id": 4568, "value": "Advertising", "sort": 2},
        {"id": 4567, "value": "Сайт", "sort": 3},
        {"id": 4568, "value": "Реклама", "sort": 4},
    ]
    source = payloads.custom_field(1234, name="Source", type="select", code=None, enums=options)
    mock.get("/api/v4/contacts/custom_fields/1234").respond(200, json=source)

    run_fragment(sample(readme, "custom-fields"))

    out = capsys.readouterr().out
    assert "427661" in out
    assert "4568" in out


@pytest.mark.parametrize("readme", READMES)
def test_notes_tasks(
    readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    add_note = mock.post("/api/v4/leads/notes").respond(
        200, json=payloads.created("notes", [76787983])
    )
    notes = mock.get("/api/v4/leads/19619/notes").respond(
        200, json=payloads.page("notes", [payloads.note(entity_id=19619)], path="leads/19619/notes")
    )
    add_task = mock.post("/api/v4/tasks").respond(200, json=payloads.created("tasks", [7087]))
    tasks = mock.get("/api/v4/tasks").respond(200, json=payloads.page("tasks", [payloads.task()]))
    complete = mock.patch("/api/v4/tasks/7087").respond(
        200, json={"id": 7087, "updated_at": 1700000000, "_links": payloads.links("tasks/7087")}
    )

    run_fragment(sample(readme, "notes-tasks"))

    [note] = body(add_note.calls.last)
    assert (note["entity_id"], note["note_type"]) == (19619, "common")
    assert set(note["params"]) == {"text"}
    assert notes.called
    [task] = body(add_task.calls.last)
    assert (task["entity_type"], task["entity_id"], task["task_type_id"]) == ("leads", 19619, 1)
    assert task["complete_till"] > time.time()
    params = tasks.calls.last.request.url.params
    assert params["filter[is_completed]"] == "0"
    assert params["filter[entity_type]"] == "leads"
    assert params["filter[entity_id][0]"] == "19619"
    done = body(complete.calls.last)
    assert done["is_completed"] is True
    assert set(done["result"]) == {"text"}
    out = capsys.readouterr().out
    assert "common" in out
    assert "7087" in out


@pytest.mark.parametrize("readme", READMES)
def test_webhook_parse(readme: str) -> None:
    run_doctest(sample(readme, "webhook-parse"), "webhook-parse")


@pytest.mark.parametrize("readme", READMES)
def test_webhook_fastapi(
    readme: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("fastapi")
    monkeypatch.setenv("AMO_WEBHOOK_SECRET", "s3cret-path-part")
    app = load_module(sample(readme, "webhook-fastapi"))["app"]
    status_change = {
        "leads[status][0][id]": "25399013",
        "leads[status][0][status_id]": "142",
        "leads[status][0][pipeline_id]": "1300",
        "leads[status][0][old_status_id]": "32392156",
        "account[subdomain]": "example",
        "account[id]": "29085955",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            wrong = await http.post("/webhooks/amocrm/guess", data=status_change, headers=headers)
            assert wrong.status_code == 404
            assert capsys.readouterr().out == ""
            ok = await http.post(
                "/webhooks/amocrm/s3cret-path-part", data=status_change, headers=headers
            )
            assert ok.status_code == 200
            assert ok.json() == {"events": 1}
            bad = await http.post(
                "/webhooks/amocrm/s3cret-path-part", content=b"a=%FF", headers=headers
            )
            assert bad.status_code == 400

    asyncio.run(scenario())
    out = capsys.readouterr().out
    assert "25399013" in out
    assert "32392156 -> 142" in out


@pytest.mark.parametrize("readme", READMES)
def test_limits(readme: str, mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMO_LONG_LIVED_TOKEN", TOKEN)
    leads = mock.get(path__regex=r"^/api/v4/leads/\d+$").mock(
        side_effect=lambda request: httpx.Response(
            200, json=payloads.lead(int(request.url.path.rsplit("/", 1)[1]))
        )
    )
    started = time.monotonic()

    namespace = run_fragment(sample(readme, "limits"))

    elapsed = time.monotonic() - started
    assert leads.call_count == 3
    assert elapsed >= 2 / 3.5 - 0.05  # three requests at 3.5 per second: two gaps
    assert [lead.id for lead in namespace["found"]] == [19619, 19620, 19621]


@pytest.mark.parametrize("readme", READMES)
def test_retry_write(
    readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    route = mock.post("/api/v4/leads").respond(
        429, headers={"Retry-After": "2"}, json={"title": "Too Many Requests", "status": 429}
    )

    run_fragment(sample(readme, "retry-write"))

    assert route.call_count == 1
    assert "2.0" in capsys.readouterr().out


@pytest.mark.parametrize("readme", READMES)
def test_errors(readme: str, mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    mock.get("/api/v4/leads/1").respond(204)
    mock.get("/api/v4/leads/2").respond(
        402, json={"title": "Payment Required", "status": 402, "detail": "Account is not paid"}
    )

    run_fragment(sample(readme, "errors"))

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("1 ")
    assert out[1].startswith("402 ")
    assert "GET" in out[1]
    assert f"{BASE}/api/v4/leads/2" in out[1]


@pytest.mark.parametrize("readme", READMES)
def test_testing(readme: str) -> None:
    module = load_module(sample(readme, "testing"))
    tests = [value for name, value in module.items() if name.startswith("test_")]
    assert tests
    for test in tests:
        [mark] = [m for m in getattr(test, "pytestmark", []) if m.name == "respx"]
        with respx.mock(**mark.kwargs) as router:
            asyncio.run(test(router))
