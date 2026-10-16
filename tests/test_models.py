from __future__ import annotations

import pytest

from aioamocrm import (
    Account,
    Company,
    ComplexLeadResult,
    Contact,
    CustomField,
    CustomFieldValue,
    CustomFieldValues,
    Embedded,
    EntityRef,
    Lead,
    Link,
    Note,
    Pipeline,
    Tag,
    Task,
    User,
)

from . import payloads


def test_a_lead_is_parsed_with_its_embedded_contacts() -> None:
    body = payloads.lead(
        _embedded={
            "tags": [{"id": 1, "name": "site", "color": None}],
            "companies": [{"id": 5, "_links": payloads.links("companies/5")}],
            "contacts": [
                {"id": 10971463, "is_main": True, "_links": payloads.links("contacts/10971463")},
                {"id": 10971465, "is_main": False, "_links": payloads.links("contacts/10971465")},
            ],
        }
    )
    lead = Lead.model_validate(body)

    assert lead.id == 19619
    assert lead.name == "Сделка для примера"
    assert lead.price == 46333
    assert lead.status_id == 142
    assert lead.pipeline_id == 1300
    assert lead.closed_at == 1483005931
    assert lead.links == payloads.links("leads/19619")
    assert [c.id for c in lead.embedded.contacts] == [10971463, 10971465]
    assert lead.embedded.contacts[0].is_main is True
    assert lead.embedded.companies[0].id == 5
    assert lead.embedded.tags == [Tag(id=1, name="site", color=None)]


def test_unknown_fields_are_kept() -> None:
    lead = Lead.model_validate(payloads.lead(brand_new_field={"x": 1}))
    assert lead.model_extra == {"source_id": None, "brand_new_field": {"x": 1}}
    contact = Contact.model_validate(
        payloads.contact(_embedded={"tags": [], "companies": [], "catalog_elements": []})
    )
    assert contact.embedded.catalog_elements == []


def test_php_empty_arrays_and_nulls_are_tolerated() -> None:
    lead = Lead.model_validate(payloads.lead(_embedded=[]))
    assert lead.embedded == Embedded()
    lead = Lead.model_validate(payloads.lead(_embedded={"tags": None}))
    assert lead.embedded.tags == []
    task = Task.model_validate(payloads.task(result=[]))
    assert task.result is None


def test_field_helpers_read_by_code_and_by_id() -> None:
    contact = Contact.model_validate(payloads.contact())

    phone = contact.field("PHONE")
    assert isinstance(phone, CustomFieldValues)
    assert phone.field_id == 3
    assert contact.field_value("PHONE") == "+79123456789"
    assert contact.field_value("phone") == "+79123456789"  # codes are matched case-insensitively
    assert contact.field_value("PHONE", enum_code="MOB") == "+79990001122"
    assert contact.field_values("PHONE") == ["+79123456789", "+79990001122"]
    assert contact.field_value("EMAIL") == "ivan@example.com"
    assert contact.field_value(1234) == "Сайт"
    assert contact.field_value(3, enum_code="WORK") == "+79123456789"


def test_field_helpers_with_missing_fields() -> None:
    contact = Contact.model_validate(payloads.contact())
    assert contact.field("NOPE") is None
    assert contact.field_value("NOPE") is None
    assert contact.field_value("NOPE", "fallback") == "fallback"
    assert contact.field_value("PHONE", enum_code="HOME") is None
    assert contact.field_values(999) == []

    lead = Lead.model_validate(payloads.lead())  # custom_fields_values is null
    assert lead.field_value("PHONE", 0) == 0
    assert lead.field_values(1) == []


def test_boolean_keys_are_refused() -> None:
    with pytest.raises(TypeError):
        Contact().field(True)


def test_set_field_replaces_an_existing_field() -> None:
    contact = Contact.model_validate(payloads.contact())
    contact.set_field("PHONE", "+70000000000", enum_code="WORK")

    assert contact.field_values("PHONE") == ["+70000000000"]
    phone = next(f for f in contact.to_api()["custom_fields_values"] if f.get("field_id") == 3)
    # Writes name the field once, by id, and leave out the read-only name and type.
    assert phone == {"field_id": 3, "values": [{"value": "+70000000000", "enum_code": "WORK"}]}


def test_set_field_adds_new_fields_by_code_or_by_id() -> None:
    contact = Contact(name="Иван")
    contact.set_field("PHONE", "+79123456789", enum_code="WORK")
    contact.set_field("EMAIL", "a@example.com", "b@example.com", enum_code="WORK")
    contact.set_field(1234, 4567, enum_id=4567)

    assert contact.to_api() == {
        "name": "Иван",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+79123456789", "enum_code": "WORK"}]},
            {
                "field_code": "EMAIL",
                "values": [
                    {"value": "a@example.com", "enum_code": "WORK"},
                    {"value": "b@example.com", "enum_code": "WORK"},
                ],
            },
            {"field_id": 1234, "values": [{"value": 4567, "enum_id": 4567}]},
        ],
    }
    assert contact.field_value("EMAIL") == "a@example.com"


def test_set_field_accepts_ready_values() -> None:
    contact = Contact()
    contact.set_field(
        "PHONE",
        CustomFieldValue(value="+71", enum_code="WORK"),
        {"value": "+72", "enum_code": "MOB"},
    )
    assert contact.to_api()["custom_fields_values"] == [
        {
            "field_code": "PHONE",
            "values": [{"value": "+71", "enum_code": "WORK"}, {"value": "+72", "enum_code": "MOB"}],
        }
    ]


def test_set_field_needs_a_value() -> None:
    with pytest.raises(ValueError, match="value"):
        Contact().set_field("PHONE")


def test_to_api_sends_only_what_was_set() -> None:
    assert Lead(name="Заявка", price=1000, status_id=142).to_api() == {
        "name": "Заявка",
        "price": 1000,
        "status_id": 142,
    }
    assert Lead(id=5, price=None).to_api() == {"id": 5}


def test_to_api_drops_read_only_parts_of_a_fetched_entity() -> None:
    data = Contact.model_validate(payloads.contact()).to_api()
    for key in ("_links", "account_id", "is_deleted", "closest_task_at"):
        assert key not in data
    assert data["id"] == 3
    assert all(
        "field_name" not in f and "field_type" not in f for f in data["custom_fields_values"]
    )
    source = next(f for f in data["custom_fields_values"] if f["field_id"] == 1234)
    assert source == {"field_id": 1234, "values": [{"value": "Сайт", "enum_id": 4567}]}


def test_embedded_entities_for_complex_leads() -> None:
    contact = Contact(first_name="Иван", last_name="Иванов")
    contact.set_field("PHONE", "+79123456789", enum_code="WORK")
    lead = Lead(
        name="Заявка с сайта",
        price=5000,
        embedded=Embedded(
            contacts=[contact],
            companies=[Company(name="ООО Ромашка")],
            tags=[Tag(name="site")],
        ),
    )
    assert lead.to_api() == {
        "name": "Заявка с сайта",
        "price": 5000,
        "_embedded": {
            "tags": [{"name": "site"}],
            "contacts": [
                {
                    "first_name": "Иван",
                    "last_name": "Иванов",
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


def test_common_note() -> None:
    note = Note.common(167353, "Комментарий из формы")
    assert note.to_api() == {
        "entity_id": 167353,
        "note_type": "common",
        "params": {"text": "Комментарий из формы"},
    }
    parsed = Note.model_validate(payloads.note())
    assert parsed.text == "Текст примечания"
    assert Note(note_type="call_in", params={"uniq": "x"}).text is None


def test_task_model() -> None:
    task = Task.model_validate(payloads.task(result={"text": "Дозвонились"}, is_completed=True))
    assert task.is_completed is True
    assert task.result is not None
    assert task.result.text == "Дозвонились"
    assert task.entity_type == "leads"
    new = Task(text="Перезвонить", complete_till=1700000000, entity_id=1, entity_type="leads")
    assert new.to_api() == {
        "text": "Перезвонить",
        "complete_till": 1700000000,
        "entity_id": 1,
        "entity_type": "leads",
    }


def test_pipeline_with_statuses() -> None:
    pipeline = Pipeline.model_validate(payloads.pipelines()["_embedded"]["pipelines"][0])
    assert pipeline.is_main is True
    assert [s.id for s in pipeline.statuses] == [32392156, 142]
    assert pipeline.statuses[1].name == "Успешно реализовано"
    assert pipeline.status(142) is pipeline.statuses[1]
    assert pipeline.status(1) is None


def test_custom_field_definition() -> None:
    field = CustomField.model_validate(payloads.custom_field())
    assert field.code == "PHONE"
    assert field.type == "multitext"
    assert field.enums is not None
    assert [e.value for e in field.enums] == ["WORK", "MOB"]
    assert field.enum_id("MOB") == 3
    assert field.enum_id("FAX") is None


def test_users_account_refs_links() -> None:
    user = User.model_validate(payloads.user())
    assert user.email == "manager@example.com"
    assert user.rights is not None
    account = Account.model_validate(payloads.account())
    assert account.subdomain == "example"
    assert account.currency == "RUB"
    ref = EntityRef.model_validate(
        {"id": 1, "request_id": "0", "_links": payloads.links("leads/1")}
    )
    assert ref.id == 1
    assert ref.request_id == "0"
    result = ComplexLeadResult.model_validate(
        {
            "id": 9993813,
            "contact_id": 15009433,
            "company_id": None,
            "request_id": ["0"],
            "merged": False,
        }
    )
    assert result.contact_id == 15009433
    assert result.request_id == ["0"]
    link = Link.model_validate(
        {
            "entity_id": 1,
            "entity_type": "leads",
            "to_entity_id": 2,
            "to_entity_type": "contacts",
            "metadata": {"main_contact": True},
        }
    )
    assert link.to_entity_type == "contacts"


def test_appending_to_the_default_embedded_is_sent() -> None:
    lead = Lead(name="Заявка")
    lead.embedded.tags.append(Tag(name="site"))
    lead.embedded.contacts.append(Contact(name="Иван"))
    assert lead.to_api() == {
        "name": "Заявка",
        "_embedded": {"tags": [{"name": "site"}], "contacts": [{"name": "Иван"}]},
    }
    assert Lead(name="x", embedded=Embedded()).to_api() == {"name": "x"}


def test_embedded_extras_are_sent_too() -> None:
    lead = Lead.model_validate(
        {"name": "x", "_embedded": {"source": {"external_id": "form-1", "type": "widget"}}}
    )
    assert lead.to_api() == {
        "name": "x",
        "_embedded": {"source": {"external_id": "form-1", "type": "widget"}},
    }
