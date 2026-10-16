"""API answers shaped like the examples in the amoCRM v4 documentation.

They were written from the documentation, not recorded from a live account.
"""

from __future__ import annotations

from typing import Any

BASE = "https://example.amocrm.ru"


def links(path: str) -> dict[str, Any]:
    return {"self": {"href": f"{BASE}/api/v4/{path}"}}


def lead(lead_id: int = 19619, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": lead_id,
        "name": "Сделка для примера",
        "price": 46333,
        "responsible_user_id": 123321,
        "group_id": 625,
        "status_id": 142,
        "pipeline_id": 1300,
        "loss_reason_id": None,
        "source_id": None,
        "created_by": 321123,
        "updated_by": 321123,
        "created_at": 1453279607,
        "updated_at": 1502193501,
        "closed_at": 1483005931,
        "closest_task_at": None,
        "is_deleted": False,
        "custom_fields_values": None,
        "score": None,
        "account_id": 5135160,
        "labor_cost": None,
        "_links": links(f"leads/{lead_id}"),
        "_embedded": {"tags": [], "companies": []},
    }
    body.update(overrides)
    return body


def contact(contact_id: int = 3, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": contact_id,
        "name": "Иван Иванов",
        "first_name": "Иван",
        "last_name": "Иванов",
        "responsible_user_id": 504141,
        "group_id": 0,
        "created_by": 504141,
        "updated_by": 504141,
        "created_at": 1582117331,
        "updated_at": 1590943929,
        "closest_task_at": None,
        "is_deleted": False,
        "is_unsorted": False,
        "custom_fields_values": [
            {
                "field_id": 3,
                "field_name": "Телефон",
                "field_code": "PHONE",
                "field_type": "multitext",
                "values": [
                    {"value": "+79123456789", "enum_id": 1, "enum_code": "WORK"},
                    {"value": "+79990001122", "enum_id": 3, "enum_code": "MOB"},
                ],
            },
            {
                "field_id": 5,
                "field_name": "Email",
                "field_code": "EMAIL",
                "field_type": "multitext",
                "values": [{"value": "ivan@example.com", "enum_id": 7, "enum_code": "WORK"}],
            },
            {
                "field_id": 1234,
                "field_name": "Источник",
                "field_code": None,
                "field_type": "select",
                "values": [{"value": "Сайт", "enum_id": 4567}],
            },
        ],
        "account_id": 28805383,
        "_links": links(f"contacts/{contact_id}"),
        "_embedded": {"tags": [], "companies": []},
    }
    body.update(overrides)
    return body


def company(company_id: int = 1, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": company_id,
        "name": "АО Рога и копыта",
        "responsible_user_id": 504141,
        "group_id": 0,
        "created_by": 504141,
        "updated_by": 504141,
        "created_at": 1582117331,
        "updated_at": 1586361223,
        "closest_task_at": None,
        "is_deleted": False,
        "custom_fields_values": None,
        "account_id": 28805383,
        "_links": links(f"companies/{company_id}"),
        "_embedded": {"tags": []},
    }
    body.update(overrides)
    return body


def page(
    key: str,
    items: list[dict[str, Any]],
    *,
    number: int = 1,
    path: str | None = None,
    limit: int = 250,
    has_next: bool = False,
) -> dict[str, Any]:
    path = path or key
    page_links: dict[str, Any] = {
        "self": {"href": f"{BASE}/api/v4/{path}?page={number}&limit={limit}"}
    }
    if has_next:
        page_links["next"] = {"href": f"{BASE}/api/v4/{path}?page={number + 1}&limit={limit}"}
    if number > 1:
        page_links["prev"] = {"href": f"{BASE}/api/v4/{path}?page={number - 1}&limit={limit}"}
    return {"_page": number, "_links": page_links, "_embedded": {key: items}}


def created(key: str, ids: list[int], *, start: int = 0) -> dict[str, Any]:
    """Answer to POST /api/v4/{key}: ids with the request_id of each item."""
    return {
        "_links": links(key),
        "_embedded": {
            key: [
                {"id": item_id, "request_id": str(start + n), "_links": links(f"{key}/{item_id}")}
                for n, item_id in enumerate(ids)
            ]
        },
    }


def updated(key: str, ids: list[int]) -> dict[str, Any]:
    """Answer to PATCH /api/v4/{key}."""
    return {
        "_links": links(key),
        "_embedded": {
            key: [
                {"id": item_id, "updated_at": 1700000000, "_links": links(f"{key}/{item_id}")}
                for item_id in ids
            ]
        },
    }


def note(note_id: int = 76787983, entity_id: int = 167353, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": note_id,
        "entity_id": entity_id,
        "created_by": 940088,
        "updated_by": 940088,
        "created_at": 1588841241,
        "updated_at": 1588841241,
        "responsible_user_id": 940088,
        "group_id": 0,
        "note_type": "common",
        "params": {"text": "Текст примечания"},
        "account_id": 17079858,
        "_links": links(f"leads/{entity_id}/notes/{note_id}"),
    }
    body.update(overrides)
    return body


def task(task_id: int = 7087, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": task_id,
        "created_by": 3987910,
        "updated_by": 3987910,
        "created_at": 1575364000,
        "updated_at": 1575364851,
        "responsible_user_id": 123123,
        "group_id": 0,
        "entity_id": 167353,
        "entity_type": "leads",
        "duration": 0,
        "is_completed": False,
        "task_type_id": 1,
        "text": "Позвонить клиенту",
        "result": [],
        "complete_till": 1575665940,
        "account_id": 321321,
        "_links": links(f"tasks/{task_id}"),
    }
    body.update(overrides)
    return body


def pipelines() -> dict[str, Any]:
    return {
        "_total_items": 1,
        "_links": links("leads/pipelines"),
        "_embedded": {
            "pipelines": [
                {
                    "id": 3177727,
                    "name": "Воронка",
                    "sort": 1,
                    "is_main": True,
                    "is_unsorted_on": True,
                    "is_archive": False,
                    "account_id": 12345678,
                    "_links": links("leads/pipelines/3177727"),
                    "_embedded": {
                        "statuses": [
                            {
                                "id": 32392156,
                                "name": "Неразобранное",
                                "sort": 10,
                                "is_editable": False,
                                "pipeline_id": 3177727,
                                "color": "#c1c1c1",
                                "type": 1,
                                "account_id": 12345678,
                                "_links": links("leads/pipelines/3177727/statuses/32392156"),
                            },
                            {
                                "id": 142,
                                "name": "Успешно реализовано",
                                "sort": 10000,
                                "is_editable": False,
                                "pipeline_id": 3177727,
                                "color": "#CCFF66",
                                "type": 0,
                                "account_id": 12345678,
                                "_links": links("leads/pipelines/3177727/statuses/142"),
                            },
                        ]
                    },
                }
            ]
        },
    }


def custom_field(field_id: int = 3, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": field_id,
        "name": "Телефон",
        "type": "multitext",
        "account_id": 28805383,
        "code": "PHONE",
        "sort": 4,
        "is_api_only": False,
        "enums": [
            {"id": 1, "value": "WORK", "sort": 2},
            {"id": 3, "value": "MOB", "sort": 6},
        ],
        "group_id": None,
        "required_statuses": [],
        "is_deletable": False,
        "is_predefined": True,
        "entity_type": "contacts",
        "remind": None,
        "_links": links(f"contacts/custom_fields/{field_id}"),
    }
    body.update(overrides)
    return body


def user(user_id: int = 504141, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": user_id,
        "name": "Менеджер",
        "email": "manager@example.com",
        "lang": "ru",
        "rights": {"leads": {"view": "A", "edit": "A", "add": "A", "delete": "A", "export": "A"}},
        "_links": links(f"users/{user_id}"),
    }
    body.update(overrides)
    return body


def account() -> dict[str, Any]:
    return {
        "id": 1231414,
        "name": "example",
        "subdomain": "example",
        "created_at": 1585840134,
        "created_by": 321321,
        "updated_at": 1589472711,
        "updated_by": 321321,
        "current_user_id": 581651,
        "country": "RU",
        "customers_mode": "segments",
        "is_unsorted_on": True,
        "is_loss_reason_enabled": True,
        "is_helpbot_enabled": False,
        "is_technical_account": False,
        "contact_name_display_order": 1,
        "amojo_id": "f3c6340d-410e-4ad1-8b9c-2b3c5a3e1d2f",
        "version": 11,
        "currency": "RUB",
        "currency_symbol": "₽",
        "_links": links("account"),
        "_embedded": {},
    }
