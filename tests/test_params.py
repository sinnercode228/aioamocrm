from __future__ import annotations

import pytest

from aioamocrm._params import encode, list_params


def test_nested_filters_use_php_brackets() -> None:
    assert encode(
        "filter",
        {
            "id": [1, 2],
            "updated_at": {"from": 1700000000, "to": 1700086400},
            "statuses": [{"pipeline_id": 10, "status_id": 142}],
        },
    ) == [
        ("filter[id][0]", "1"),
        ("filter[id][1]", "2"),
        ("filter[updated_at][from]", "1700000000"),
        ("filter[updated_at][to]", "1700086400"),
        ("filter[statuses][0][pipeline_id]", "10"),
        ("filter[statuses][0][status_id]", "142"),
    ]


def test_scalars_booleans_and_none() -> None:
    assert encode("filter", {"is_completed": False, "skip": None, "name": "Acme"}) == [
        ("filter[is_completed]", "0"),
        ("filter[name]", "Acme"),
    ]
    assert encode("flag", True) == [("flag", "1")]
    assert encode("nothing", None) == []


def test_list_params_puts_everything_together() -> None:
    assert list_params(
        page=2,
        limit=250,
        filter={"id": 5},
        order={"updated_at": "desc"},
        with_=["contacts", "loss_reason"],
        query="+7 999",
        params={"custom": "x"},
    ) == [
        ("page", "2"),
        ("limit", "250"),
        ("filter[id]", "5"),
        ("order[updated_at]", "desc"),
        ("with", "contacts,loss_reason"),
        ("query", "+7 999"),
        ("custom", "x"),
    ]


def test_with_accepts_a_plain_string() -> None:
    assert list_params(with_="contacts") == [("with", "contacts")]


def test_empty_arguments_add_nothing() -> None:
    assert list_params() == []
    assert list_params(filter={}, with_=[], query="") == []


@pytest.mark.parametrize("limit", [0, 251, -5])
def test_limit_must_be_between_1_and_250(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        list_params(limit=limit)


def test_page_must_be_positive() -> None:
    with pytest.raises(ValueError, match="page"):
        list_params(page=0)
