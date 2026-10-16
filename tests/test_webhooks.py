"""Webhook bodies as amoCRM sends them: x-www-form-urlencoded with PHP bracket keys.

The leads[add] body follows the example in the amoCRM webhook documentation (including the
date field sent as ``values][0]=`` without ``[value]``); the status, contact and note bodies
follow the same notation as described there and in public examples. None of them was
recorded from a live account.
"""

from __future__ import annotations

import time
from urllib.parse import quote, urlencode

import pytest

from aioamocrm import Webhook, WebhookEvent, parse_webhook

ACCOUNT = [
    ("account[subdomain]", "example"),
    ("account[id]", "29085955"),
    ("account[_links][self]", "https://example.amocrm.ru"),
]

LEAD_ADD = [
    ("leads[add][0][id]", "25399013"),
    ("leads[add][0][name]", "Lead title"),
    ("leads[add][0][status_id]", "142"),
    ("leads[add][0][price]", "1000"),
    ("leads[add][0][responsible_user_id]", "123123"),
    ("leads[add][0][last_modified]", "1413554372"),
    ("leads[add][0][modified_user_id]", "123123"),
    ("leads[add][0][created_user_id]", "123123"),
    ("leads[add][0][date_create]", "1413554372"),
    ("leads[add][0][pipeline_id]", "1300"),
    ("leads[add][0][account_id]", "29085955"),
    ("leads[add][0][custom_fields][0][id]", "427183"),
    ("leads[add][0][custom_fields][0][name]", "Checkbox custom field"),
    ("leads[add][0][custom_fields][0][values][0][value]", "1"),
    ("leads[add][0][custom_fields][1][id]", "427271"),
    ("leads[add][0][custom_fields][1][name]", "Date custom field"),
    ("leads[add][0][custom_fields][1][values][0]", "1412380800"),
    ("leads[add][0][custom_fields][2][id]", "427661"),
    ("leads[add][0][custom_fields][2][name]", "Text custom field"),
    ("leads[add][0][custom_fields][2][values][0][value]", "Валера"),
    ("leads[add][0][custom_fields][3][id]", "1075334"),
    ("leads[add][0][custom_fields][3][name]", "Multiselect custom field"),
    ("leads[add][0][custom_fields][3][values][0][value]", "Option2"),
    ("leads[add][0][custom_fields][3][values][0][enum]", "2479774"),
    ("leads[add][0][custom_fields][3][values][1][value]", "Option3"),
    ("leads[add][0][custom_fields][3][values][1][enum]", "2479776"),
    ("leads[add][0][created_at]", "1413554372"),
    ("leads[add][0][updated_at]", "1413554372"),
    *ACCOUNT,
]

LEAD_UPDATE = [
    ("leads[update][0][id]", "25399013"),
    ("leads[update][0][name]", "Lead title (edited)"),
    ("leads[update][0][status_id]", "32392156"),
    ("leads[update][0][old_status_id]", "32392156"),
    ("leads[update][0][price]", "1500"),
    ("leads[update][0][responsible_user_id]", "123123"),
    ("leads[update][0][last_modified]", "1413554400"),
    ("leads[update][0][modified_user_id]", "123123"),
    ("leads[update][0][pipeline_id]", "1300"),
    ("leads[update][0][tags][0][id]", "31491"),
    ("leads[update][0][tags][0][name]", "site"),
    ("leads[update][0][updated_at]", "1413554400"),
    *ACCOUNT,
]

LEAD_STATUS = [
    ("leads[status][0][id]", "25399013"),
    ("leads[status][0][status_id]", "142"),
    ("leads[status][0][pipeline_id]", "1300"),
    ("leads[status][0][old_status_id]", "32392156"),
    ("leads[status][0][old_pipeline_id]", "1300"),
    ("leads[status][0][last_modified]", "1413554500"),
    *ACCOUNT,
]

CONTACT_UPDATE = [
    ("contacts[update][0][id]", "15009433"),
    ("contacts[update][0][name]", "Иван Иванов"),
    ("contacts[update][0][responsible_user_id]", "123123"),
    ("contacts[update][0][date_create]", "1413554372"),
    ("contacts[update][0][last_modified]", "1413554600"),
    ("contacts[update][0][created_user_id]", "123123"),
    ("contacts[update][0][modified_user_id]", "123123"),
    ("contacts[update][0][account_id]", "29085955"),
    ("contacts[update][0][custom_fields][0][id]", "3"),
    ("contacts[update][0][custom_fields][0][name]", "Телефон"),
    ("contacts[update][0][custom_fields][0][code]", "PHONE"),
    ("contacts[update][0][custom_fields][0][values][0][value]", "+79123456789"),
    ("contacts[update][0][custom_fields][0][values][0][enum]", "1"),
    ("contacts[update][0][custom_fields][0][values][1][value]", "+79990001122"),
    ("contacts[update][0][custom_fields][0][values][1][enum]", "3"),
    ("contacts[update][0][custom_fields][1][id]", "5"),
    ("contacts[update][0][custom_fields][1][name]", "Email"),
    ("contacts[update][0][custom_fields][1][code]", "EMAIL"),
    ("contacts[update][0][custom_fields][1][values][0][value]", "ivan@example.com"),
    ("contacts[update][0][custom_fields][1][values][0][enum]", "7"),
    ("contacts[update][0][type]", "contact"),
    *ACCOUNT,
]

NOTE_ADD = [
    ("leads[note][0][note][id]", "76787983"),
    ("leads[note][0][note][text]", "Клиент просит перезвонить"),
    ("leads[note][0][note][note_type]", "4"),
    ("leads[note][0][note][element_id]", "25399013"),
    ("leads[note][0][note][element_type]", "2"),
    ("leads[note][0][note][created_by]", "123123"),
    ("leads[note][0][note][date_create]", "1413554700"),
    ("leads[note][0][note][account_id]", "29085955"),
    *ACCOUNT,
]


def form(pairs: list[tuple[str, str]]) -> bytes:
    """Encode the way amoCRM does: brackets percent-encoded too."""
    return urlencode(pairs, quote_via=quote).encode()


def only(webhook: Webhook) -> WebhookEvent:
    assert len(webhook.events) == 1
    return webhook.events[0]


def test_lead_added() -> None:
    webhook = parse_webhook(form(LEAD_ADD))
    event = only(webhook)

    assert (event.entity, event.action) == ("leads", "add")
    assert event.id == 25399013
    assert event.payload["name"] == "Lead title"
    assert event.payload["status_id"] == "142"
    assert event.payload["pipeline_id"] == "1300"
    assert [field["id"] for field in event.custom_fields] == [
        "427183",
        "427271",
        "427661",
        "1075334",
    ]
    assert event.field_value(427661) == "Валера"
    assert event.field_value(427271) == "1412380800"  # sent as a bare value
    assert event.field_values(1075334) == ["Option2", "Option3"]
    assert event.field_value(999, "none") == "none"


def test_account_block() -> None:
    webhook = parse_webhook(form(LEAD_ADD))
    assert webhook.subdomain == "example"
    assert webhook.account_id == 29085955
    assert webhook.account["_links"] == {"self": "https://example.amocrm.ru"}
    assert webhook.data["account"]["subdomain"] == "example"


def test_lead_updated_with_tags() -> None:
    event = only(parse_webhook(form(LEAD_UPDATE)))
    assert (event.entity, event.action) == ("leads", "update")
    assert event.payload["price"] == "1500"
    assert event.payload["tags"] == [{"id": "31491", "name": "site"}]
    assert event.custom_fields == []


def test_lead_status_changed() -> None:
    event = only(parse_webhook(form(LEAD_STATUS)))
    assert (event.entity, event.action) == ("leads", "status")
    assert event.payload["status_id"] == "142"
    assert event.payload["old_status_id"] == "32392156"
    assert event.payload["old_pipeline_id"] == "1300"


def test_contact_updated_with_custom_fields() -> None:
    event = only(parse_webhook(form(CONTACT_UPDATE)))
    assert (event.entity, event.action) == ("contacts", "update")
    assert event.id == 15009433
    assert event.payload["name"] == "Иван Иванов"
    assert event.field_value("PHONE") == "+79123456789"
    assert event.field_value("phone") == "+79123456789"
    assert event.field_values("PHONE") == ["+79123456789", "+79990001122"]
    assert event.field_value("EMAIL") == "ivan@example.com"
    assert event.field_value(5) == "ivan@example.com"
    assert event.custom_fields[0]["values"][1] == {"value": "+79990001122", "enum": "3"}


def test_note_added() -> None:
    event = only(parse_webhook(form(NOTE_ADD)))
    assert (event.entity, event.action) == ("leads", "note")
    assert event.id is None  # the note itself is nested under "note"
    note = event.payload["note"]
    assert note["text"] == "Клиент просит перезвонить"
    assert note["element_id"] == "25399013"
    assert note["note_type"] == "4"


def test_several_entities_and_actions_in_one_body() -> None:
    pairs = [
        ("leads[add][0][id]", "1"),
        ("leads[add][1][id]", "2"),
        ("leads[status][0][id]", "1"),
        ("contacts[add][0][id]", "10"),
        ("task[add][0][id]", "500"),
        *ACCOUNT,
    ]
    webhook = parse_webhook(form(pairs))
    assert [(e.entity, e.action, e.id) for e in webhook.events] == [
        ("leads", "add", 1),
        ("leads", "add", 2),
        ("leads", "status", 1),
        ("contacts", "add", 10),
        ("task", "add", 500),
    ]


def test_unencoded_brackets_and_text_bodies() -> None:
    body = "leads[delete][0][id]=7&leads[delete][0][status_id]=143&account[subdomain]=example"
    for raw in (body, body.encode()):
        event = only(parse_webhook(raw))
        assert (event.entity, event.action, event.id) == ("leads", "delete", 7)


def test_plus_and_percent_escapes_are_decoded() -> None:
    event = only(parse_webhook(b"leads[add][0][id]=1&leads[add][0][name]=Hello+world%21"))
    assert event.payload["name"] == "Hello world!"


def test_append_brackets_and_sparse_indexes() -> None:
    webhook = parse_webhook(
        b"leads[add][0][id]=1&leads[add][0][tags][]=a&leads[add][0][tags][]=b&leads[add][5][id]=6"
    )
    assert [e.id for e in webhook.events] == [1, 6]
    assert webhook.events[0].payload["tags"] == ["a", "b"]


def test_append_brackets_take_the_index_after_the_highest_one() -> None:
    # As PHP does: a[] goes after the highest index so far, explicit or appended.
    event = only(parse_webhook(b"leads[add][0][t][]=x&leads[add][0][t][7]=y&leads[add][0][t][]=z"))
    assert event.payload["t"] == ["x", "y", "z"]
    replaced = only(parse_webhook(b"leads[add][0][t][]=1&leads[add][0][t]=s&leads[add][0][t][]=2"))
    assert replaced.payload["t"] == ["2"]


def test_many_append_brackets_parse_in_linear_time() -> None:
    # max_fields bounds the work, so a body at the limit must stay cheap to parse: finding
    # the next index by scanning every key made this quadratic (seconds of blocked loop).
    body = "&".join(["leads[add][0][id]=1"] + ["leads[add][0][tags][]=t"] * 9_999).encode()
    started = time.perf_counter()
    event = only(parse_webhook(body))
    elapsed = time.perf_counter() - started
    assert len(event.payload["tags"]) == 9_999
    assert elapsed < 1.0


def test_a_single_item_without_an_index() -> None:
    event = only(parse_webhook(b"leads[add][id]=3&leads[add][name]=x"))
    assert event.payload == {"id": "3", "name": "x"}


def test_empty_and_unrelated_bodies_have_no_events() -> None:
    assert parse_webhook(b"").events == ()
    assert parse_webhook(b"account[subdomain]=example").events == ()
    assert parse_webhook(b"hello=world&leads=1").events == ()
    assert parse_webhook(b"").subdomain is None
    assert parse_webhook(b"").account_id is None


def test_non_numeric_ids_are_none() -> None:
    event = only(parse_webhook(b"leads[add][0][id]=abc"))
    assert event.id is None


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        parse_webhook(b"leads[add][0][name]=%FF%FE")


def test_deep_nesting_is_rejected() -> None:
    key = "leads" + "[a]" * 40
    with pytest.raises(ValueError, match="deep"):
        parse_webhook(f"{key}=1".encode())


def test_too_many_fields_are_rejected() -> None:
    body = "&".join(f"leads[add][{n}][id]={n}" for n in range(20)).encode()
    with pytest.raises(ValueError, match="fields"):
        parse_webhook(body, max_fields=10)


def test_malformed_keys_are_kept_literally() -> None:
    webhook = parse_webhook(b"leads[add][0[id]=1&leads[add][0][id]=2")
    assert [e.id for e in webhook.events] == [2]
    assert webhook.data["leads[add][0[id]"] == "1"
