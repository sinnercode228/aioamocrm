from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from aioamocrm import (
    AmoCRMClient,
    AmoCRMError,
    Company,
    ComplexLeadResult,
    Contact,
    CustomField,
    Embedded,
    EntityRef,
    Lead,
    Link,
    Note,
    NotFoundError,
    RateLimitError,
    ServerError,
    Task,
    ValidationError,
)

from . import payloads


def body(call: Any) -> Any:
    return json.loads(call.request.content)


def created_answer(key: str) -> Any:
    """Answer a POST with one id per item, numbered from 1000."""
    counter = iter(range(1000, 100000))

    def answer(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)
        return httpx.Response(200, json=payloads.created(key, [next(counter) for _ in items]))

    return answer


# ---- leads, contacts, companies -------------------------------------------------------------


async def test_get_a_lead_with_contacts(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/leads/19619").mock(
        return_value=httpx.Response(200, json=payloads.lead())
    )
    lead = await amo.leads.get(19619, with_=["contacts"])
    assert isinstance(lead, Lead)
    assert lead.id == 19619
    assert route.calls.last.request.url.params["with"] == "contacts"


@pytest.mark.parametrize("answer", [httpx.Response(204), httpx.Response(404)])
async def test_a_missing_entity_is_not_found(
    amo: AmoCRMClient, mock: respx.MockRouter, answer: httpx.Response
) -> None:
    mock.get("/api/v4/contacts/5").mock(return_value=answer)
    with pytest.raises(NotFoundError):
        await amo.contacts.get(5)


async def test_ids_in_paths_must_be_integers(amo: AmoCRMClient) -> None:
    with pytest.raises(TypeError):
        await amo.leads.get("1/../../account")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await amo.leads.get(True)


async def test_create_sends_models_and_dicts(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.post("/api/v4/contacts").mock(side_effect=created_answer("contacts"))
    contact = Contact(first_name="Иван")
    contact.set_field("PHONE", "+79123456789", enum_code="WORK")

    refs = await amo.contacts.create([contact, {"name": "Пётр"}])

    assert body(route.calls.last) == [
        {
            "first_name": "Иван",
            "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+79123456789", "enum_code": "WORK"}]}
            ],
        },
        {"name": "Пётр"},
    ]
    assert [ref.id for ref in refs] == [1000, 1001]
    assert all(isinstance(ref, EntityRef) for ref in refs)


async def test_batch_create_splits_large_inputs(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.post("/api/v4/leads").mock(side_effect=created_answer("leads"))
    leads = [Lead(name=f"Lead {n}") for n in range(120)]

    refs = await amo.leads.create(leads)

    assert [len(body(call)) for call in route.calls] == [50, 50, 20]
    assert body(route.calls[2])[0] == {"name": "Lead 100"}
    assert [ref.id for ref in refs] == list(range(1000, 1120))


async def test_batch_size_is_configurable_and_bounded(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/companies").mock(side_effect=created_answer("companies"))
    await amo.companies.create([Company(name=str(n)) for n in range(5)], batch_size=2)
    assert [len(body(call)) for call in route.calls] == [2, 2, 1]
    for bad in (0, 251):
        with pytest.raises(ValueError, match="batch_size"):
            await amo.companies.create([Company(name="x")], batch_size=bad)


async def test_nothing_to_create_sends_nothing(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.post("/api/v4/leads")
    assert await amo.leads.create([]) == []
    assert not route.called


async def test_a_failed_chunk_reports_where_it_failed(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    failure = httpx.Response(
        400,
        json={
            "validation-errors": [
                {
                    "request_id": "1",
                    "errors": [{"code": "NotBlank", "path": "name", "detail": "Required"}],
                }
            ],
            "title": "Bad Request",
            "type": "https://httpstatus.es/400",
            "status": 400,
            "detail": "Request validation failed",
        },
    )
    mock.post("/api/v4/leads").mock(
        side_effect=[httpx.Response(200, json=payloads.created("leads", [1, 2])), failure]
    )

    with pytest.raises(ValidationError) as caught:
        await amo.leads.create([Lead(name="a"), Lead(name="b"), Lead(name="c"), {}], batch_size=2)

    error = caught.value
    assert error.batch_offset == 2
    assert [ref.id for ref in error.completed] == [1, 2]
    assert error.errors[0].path == "name"
    assert error.errors[0].request_id == "1"  # within the chunk: input index 2 + 1 = 3


async def test_create_does_not_retry_5xx_unless_asked(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/leads").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=payloads.created("leads", [1]))]
    )
    with pytest.raises(ServerError):
        await amo.leads.create([Lead(name="x")])
    assert route.call_count == 1
    refs = await amo.leads.create([Lead(name="x")], retry=True)
    assert [ref.id for ref in refs] == [1]


async def test_a_rate_limited_chunk_is_reported_not_sent_again(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/leads/complex").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": 1, "contact_id": 2, "request_id": ["0"]}]),
            httpx.Response(429, json={"title": "Too Many Requests", "status": 429}),
            httpx.Response(200, json=[{"id": 9, "contact_id": 9, "request_id": ["0"]}]),
        ]
    )

    with pytest.raises(RateLimitError) as caught:
        await amo.leads.create_complex([Lead(name="a"), Lead(name="b")], batch_size=1)

    assert route.call_count == 2
    assert caught.value.batch_offset == 1
    assert [result.id for result in caught.value.completed] == [1]


async def test_update_in_batches(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        ids = [item["id"] for item in json.loads(request.content)]
        return httpx.Response(200, json=payloads.updated("leads", ids))

    route = mock.patch("/api/v4/leads").mock(side_effect=answer)
    refs = await amo.leads.update(
        [Lead(id=n, status_id=142) for n in range(1, 4)] + [{"id": 4, "price": 10}],
        batch_size=3,
    )
    assert [len(body(call)) for call in route.calls] == [3, 1]
    assert body(route.calls[0])[0] == {"id": 1, "status_id": 142}
    assert [ref.id for ref in refs] == [1, 2, 3, 4]
    assert refs[0].updated_at == 1700000000


async def test_update_needs_ids(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.patch("/api/v4/leads")
    with pytest.raises(ValueError, match="id"):
        await amo.leads.update([Lead(id=1, price=1), Lead(price=2)])
    assert not route.called


async def test_link_and_unlink(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    link_route = mock.post("/api/v4/leads/10/link").mock(
        return_value=httpx.Response(
            200,
            json={
                "_total_items": 1,
                "_links": payloads.links("leads/10/link"),
                "_embedded": {
                    "links": [
                        {
                            "entity_id": 10,
                            "entity_type": "leads",
                            "to_entity_id": 20,
                            "to_entity_type": "contacts",
                            "metadata": {"main_contact": True},
                        }
                    ]
                },
            },
        )
    )
    unlink_route = mock.post("/api/v4/leads/10/unlink").mock(return_value=httpx.Response(204))

    links = await amo.leads.link(10, "contacts", 20, metadata={"is_main": True})
    await amo.leads.unlink(10, "contacts", 20)

    assert body(link_route.calls.last) == [
        {"to_entity_id": 20, "to_entity_type": "contacts", "metadata": {"is_main": True}}
    ]
    assert links == [
        Link(
            entity_id=10,
            entity_type="leads",
            to_entity_id=20,
            to_entity_type="contacts",
            metadata={"main_contact": True},
        )
    ]
    assert body(unlink_route.calls.last) == [{"to_entity_id": 20, "to_entity_type": "contacts"}]


async def test_list_links(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/contacts/20/links").mock(return_value=httpx.Response(204))
    assert await amo.contacts.links(20, filter={"to_entity_type": "leads"}) == []
    assert route.calls.last.request.url.params["filter[to_entity_type]"] == "leads"


async def test_contacts_and_companies_iterate(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/contacts").mock(
        return_value=httpx.Response(200, json=payloads.page("contacts", [payloads.contact()]))
    )
    mock.get("/api/v4/companies").mock(
        return_value=httpx.Response(200, json=payloads.page("companies", [payloads.company()]))
    )
    contacts = [c async for c in amo.contacts.iterate(query="+79123456789")]
    companies = [c async for c in amo.companies.iterate()]
    assert contacts[0].field_value("PHONE") == "+79123456789"
    assert companies[0].name == "АО Рога и копыта"


# ---- complex leads ---------------------------------------------------------------------------


async def test_complex_lead_with_contact_and_company(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.post("/api/v4/leads/complex").mock(
        return_value=httpx.Response(
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
    )
    contact = Contact(name="Иван")
    contact.set_field("PHONE", "+79123456789", enum_code="WORK")
    lead = Lead(
        name="Заявка с сайта",
        price=5000,
        pipeline_id=3177727,
        embedded=Embedded(contacts=[contact], companies=[Company(name="ООО Ромашка")]),
    )

    results = await amo.leads.create_complex([lead])

    assert results == [
        ComplexLeadResult(
            id=9993813, contact_id=15009433, company_id=15009435, request_id=["0"], merged=False
        )
    ]
    assert body(route.calls.last) == [
        {
            "name": "Заявка с сайта",
            "price": 5000,
            "pipeline_id": 3177727,
            "_embedded": {
                "contacts": [
                    {
                        "name": "Иван",
                        "custom_fields_values": [
                            {
                                "field_code": "PHONE",
                                "values": [{"value": "+79123456789", "enum_code": "WORK"}],
                            }
                        ],
                    }
                ],
                "companies": [{"name": "ООО Ромашка"}],
            },
        }
    ]


async def test_complex_leads_are_batched(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)
        return httpx.Response(
            200, json=[{"id": n, "request_id": [str(n)]} for n in range(len(items))]
        )

    route = mock.post("/api/v4/leads/complex").mock(side_effect=answer)
    results = await amo.leads.create_complex([{"name": str(n)} for n in range(60)])
    assert [len(body(call)) for call in route.calls] == [50, 10]
    assert len(results) == 60


async def test_numeric_request_ids_do_not_break_the_answer_of_a_saved_write(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    # The documentation says request_id comes back unchanged, so a caller who sent numbers
    # gets numbers. The leads are saved by then: failing to read the answer would lose
    # their ids and invite a retry that creates them again.
    mock.post("/api/v4/leads").mock(
        return_value=httpx.Response(
            200, json={"_embedded": {"leads": [{"id": 10, "request_id": 7, "name": 2024}]}}
        )
    )
    mock.post("/api/v4/leads/complex").mock(
        return_value=httpx.Response(200, json=[{"id": 11, "contact_id": 12, "request_id": [7]}])
    )

    [ref] = await amo.leads.create([{"name": "Order 7", "request_id": 7}])
    [result] = await amo.leads.create_complex([{"name": "Order 7", "request_id": 7}])

    assert (ref.id, ref.request_id) == (10, "7")
    assert (result.id, result.contact_id, result.request_id) == (11, 12, ["7"])


async def test_an_unreadable_answer_to_a_saved_chunk_keeps_what_amocrm_sent(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    odd = {"_embedded": {"leads": [{"id": "L-2", "request_id": "0"}]}}
    mock.post("/api/v4/leads").mock(
        side_effect=[
            httpx.Response(200, json=payloads.created("leads", [1])),
            httpx.Response(200, json=odd),
        ]
    )

    with pytest.raises(AmoCRMError) as caught:
        await amo.leads.create([Lead(name="a"), Lead(name="b")], batch_size=1)

    error = caught.value
    assert error.status_code == 200
    assert error.body == odd  # the answer of the saved chunk, to recover its ids by hand
    assert error.batch_offset == 1
    assert [ref.id for ref in error.completed] == [1]
    assert "saved" in str(error)
    assert error.url == "https://example.amocrm.ru/api/v4/leads"


# ---- notes ----------------------------------------------------------------------------------


async def test_add_a_note_to_a_lead(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.post("/api/v4/leads/notes").mock(
        return_value=httpx.Response(
            200,
            json={
                "_links": payloads.links("leads/notes"),
                "_embedded": {
                    "notes": [
                        {
                            "id": 42,
                            "entity_id": 9993813,
                            "request_id": "0",
                            "_links": payloads.links("leads/9993813/notes/42"),
                        }
                    ]
                },
            },
        )
    )
    refs = await amo.leads.notes.create([Note.common(9993813, "Перезвонить после 18:00")])
    # Relay sends exactly this body.
    assert body(route.calls.last) == [
        {"entity_id": 9993813, "note_type": "common", "params": {"text": "Перезвонить после 18:00"}}
    ]
    assert refs[0].id == 42
    assert refs[0].entity_id == 9993813


async def test_list_notes_of_one_entity_and_of_all(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    one = mock.get("/api/v4/contacts/3/notes").mock(
        return_value=httpx.Response(
            200, json=payloads.page("notes", [payloads.note(1, 3)], path="contacts/3/notes")
        )
    )
    every = mock.get("/api/v4/companies/notes").mock(return_value=httpx.Response(204))

    notes = [n async for n in amo.contacts.notes.iterate(entity_id=3)]
    assert [n.id for n in notes] == [1]
    assert notes[0].text == "Текст примечания"
    assert one.called

    page = await amo.companies.notes.get_page(filter={"note_type": ["common"]})
    assert page.items == []
    assert every.calls.last.request.url.params["filter[note_type][0]"] == "common"


async def test_get_one_note_names_its_entity(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    # The official PHP SDK fetches one note at /{entity_type}/{entity_id}/notes/{id}: it has no
    # route that takes the note id alone.
    route = mock.get("/api/v4/leads/167353/notes/76787983").mock(
        return_value=httpx.Response(200, json=payloads.note())
    )
    note = await amo.leads.notes.get(76787983, entity_id=167353)
    assert (note.id, note.entity_id, note.note_type) == (76787983, 167353, "common")
    assert route.called


async def test_get_one_note_checks_both_ids(amo: AmoCRMClient) -> None:
    with pytest.raises(TypeError, match="entity_id must be an int"):
        await amo.leads.notes.get(76787983, entity_id="167353")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="note_id must be an int"):
        await amo.leads.notes.get("76787983", entity_id=167353)  # type: ignore[arg-type]


# ---- tasks ----------------------------------------------------------------------------------


async def test_create_list_and_complete_tasks(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    create = mock.post("/api/v4/tasks").mock(side_effect=created_answer("tasks"))
    listing = mock.get("/api/v4/tasks").mock(
        return_value=httpx.Response(200, json=payloads.page("tasks", [payloads.task()]))
    )
    complete = mock.patch("/api/v4/tasks/7087").mock(
        return_value=httpx.Response(
            200, json={"id": 7087, "updated_at": 1700000000, "_links": payloads.links("tasks/7087")}
        )
    )

    refs = await amo.tasks.create(
        [
            Task(
                text="Позвонить клиенту",
                complete_till=1700086400,
                entity_id=167353,
                entity_type="leads",
                task_type_id=1,
            )
        ]
    )
    tasks = [t async for t in amo.tasks.iterate(filter={"is_completed": False})]
    ref = await amo.tasks.complete(7087, result="Дозвонились")

    assert body(create.calls.last) == [
        {
            "text": "Позвонить клиенту",
            "entity_id": 167353,
            "entity_type": "leads",
            "task_type_id": 1,
            "complete_till": 1700086400,
        }
    ]
    assert refs[0].id == 1000
    assert listing.calls.last.request.url.params["filter[is_completed]"] == "0"
    assert tasks[0].text == "Позвонить клиенту"
    assert body(complete.calls.last) == {"is_completed": True, "result": {"text": "Дозвонились"}}
    assert ref.id == 7087


async def test_complete_without_a_result(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.patch("/api/v4/tasks/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    await amo.tasks.complete(1)
    assert body(route.calls.last) == {"is_completed": True}


async def test_get_and_update_tasks(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/tasks/7087").mock(return_value=httpx.Response(200, json=payloads.task()))
    update = mock.patch("/api/v4/tasks").mock(
        return_value=httpx.Response(200, json=payloads.updated("tasks", [7087]))
    )
    task = await amo.tasks.get(7087)
    assert task.result is None
    refs = await amo.tasks.update([Task(id=7087, complete_till=1800000000)])
    assert body(update.calls.last) == [{"id": 7087, "complete_till": 1800000000}]
    assert refs[0].id == 7087


# ---- pipelines, custom fields, users, account -----------------------------------------------


async def test_pipelines_and_statuses(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/leads/pipelines").mock(
        return_value=httpx.Response(200, json=payloads.pipelines())
    )
    pipeline_json = payloads.pipelines()["_embedded"]["pipelines"][0]
    mock.get("/api/v4/leads/pipelines/3177727").mock(
        return_value=httpx.Response(200, json=pipeline_json)
    )
    mock.get("/api/v4/leads/pipelines/3177727/statuses").mock(
        return_value=httpx.Response(
            200,
            json={
                "_total_items": 2,
                "_links": payloads.links("leads/pipelines/3177727/statuses"),
                "_embedded": {"statuses": pipeline_json["_embedded"]["statuses"]},
            },
        )
    )

    pipelines = await amo.pipelines.list()
    pipeline = await amo.pipelines.get(3177727)
    statuses = await amo.pipelines.statuses(3177727)

    assert [p.name for p in pipelines] == ["Воронка"]
    assert pipeline.status(142) is not None
    assert [s.id for s in statuses] == [32392156, 142]


async def test_no_pipelines_is_an_empty_list(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/leads/pipelines").mock(return_value=httpx.Response(204))
    mock.get("/api/v4/leads/pipelines/1/statuses").mock(return_value=httpx.Response(204))
    assert await amo.pipelines.list() == []
    assert await amo.pipelines.statuses(1) == []


async def test_custom_fields_per_entity(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    contacts = mock.get("/api/v4/contacts/custom_fields").mock(
        return_value=httpx.Response(
            200,
            json={
                "_total_items": 1,
                "_page": 1,
                "_page_count": 1,
                "_links": payloads.links("contacts/custom_fields"),
                "_embedded": {"custom_fields": [payloads.custom_field()]},
            },
        )
    )
    leads = mock.get("/api/v4/leads/custom_fields").mock(return_value=httpx.Response(204))
    one = mock.get("/api/v4/companies/custom_fields/77").mock(
        return_value=httpx.Response(200, json=payloads.custom_field(77, entity_type="companies"))
    )

    fields = [f async for f in amo.contacts.custom_fields.iterate()]
    assert isinstance(fields[0], CustomField)
    assert fields[0].enum_id("WORK") == 1
    assert [f async for f in amo.leads.custom_fields.iterate()] == []
    assert (await amo.companies.custom_fields.get(77)).id == 77
    assert contacts.called
    assert leads.called
    assert one.called


async def test_users(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/users").mock(
        return_value=httpx.Response(200, json=payloads.page("users", [payloads.user()]))
    )
    mock.get("/api/v4/users/504141").mock(return_value=httpx.Response(200, json=payloads.user()))
    users = [u async for u in amo.users.iterate(with_=["role", "group"])]
    assert users[0].email == "manager@example.com"
    assert (await amo.users.get(504141)).name == "Менеджер"


async def test_account(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/account").mock(
        return_value=httpx.Response(200, json=payloads.account())
    )
    account = await amo.account.get(with_=["amojo_id", "version"])
    assert account.subdomain == "example"
    assert route.calls.last.request.url.params["with"] == "amojo_id,version"
