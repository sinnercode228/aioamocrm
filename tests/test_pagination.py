from __future__ import annotations

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, Lead, Page

from . import payloads


def leads_page(ids: list[int], number: int, *, limit: int, has_next: bool) -> httpx.Response:
    return httpx.Response(
        200,
        json=payloads.page(
            "leads",
            [payloads.lead(i) for i in ids],
            number=number,
            limit=limit,
            has_next=has_next,
        ),
    )


async def test_iterate_walks_three_pages_by_following_next(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=[
            leads_page([1, 2], 1, limit=2, has_next=True),
            leads_page([3, 4], 2, limit=2, has_next=True),
            leads_page([5], 3, limit=2, has_next=False),
        ]
    )

    leads = [lead async for lead in amo.leads.iterate(limit=2, filter={"id": [1, 2, 3, 4, 5]})]

    assert [lead.id for lead in leads] == [1, 2, 3, 4, 5]
    assert all(isinstance(lead, Lead) for lead in leads)
    assert route.call_count == 3
    first = route.calls[0].request.url.params
    assert first["page"] == "1"
    assert first["limit"] == "2"
    assert first["filter[id][0]"] == "1"
    # Later pages are the hrefs amoCRM gave in _links.next.
    assert [str(call.request.url) for call in route.calls[1:]] == [
        "https://example.amocrm.ru/api/v4/leads?page=2&limit=2",
        "https://example.amocrm.ru/api/v4/leads?page=3&limit=2",
    ]


async def test_a_short_page_ends_the_walk_even_with_a_next_link(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=[leads_page([1, 2, 3], 1, limit=5, has_next=True)]
    )
    assert [lead.id async for lead in amo.leads.iterate(limit=5)] == [1, 2, 3]
    assert route.call_count == 1


async def test_a_full_page_without_next_ends_the_walk(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=[leads_page([1, 2], 1, limit=2, has_next=False)]
    )
    assert [lead.id async for lead in amo.leads.iterate(limit=2)] == [1, 2]
    assert route.call_count == 1


async def test_204_on_the_first_page_is_an_empty_list(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    assert [lead async for lead in amo.leads.iterate(filter={"id": 404})] == []
    assert route.call_count == 1


async def test_204_on_a_later_page_ends_the_walk(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=[leads_page([1, 2], 1, limit=2, has_next=True), httpx.Response(204)]
    )
    assert [lead.id async for lead in amo.leads.iterate(limit=2)] == [1, 2]
    assert route.call_count == 2


async def test_a_next_link_to_another_host_is_not_followed(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    body = payloads.page("leads", [payloads.lead(1), payloads.lead(2)], limit=2)
    body["_links"]["next"] = {"href": "https://elsewhere.example.com/api/v4/leads?page=2"}
    route = mock.get("/api/v4/leads").mock(
        side_effect=[
            httpx.Response(200, json=body),
            leads_page([3], 2, limit=2, has_next=False),
        ]
    )

    ids = [lead.id async for lead in amo.leads.iterate(limit=2, order={"id": "asc"})]

    assert ids == [1, 2, 3]
    second = route.calls[1].request.url
    assert second.host == "example.amocrm.ru"
    assert second.params["page"] == "2"
    assert second.params["limit"] == "2"
    assert second.params["order[id]"] == "asc"


async def test_get_page_returns_one_page(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    route = mock.get("/api/v4/leads").mock(
        return_value=leads_page([7, 8], 3, limit=2, has_next=True)
    )

    page = await amo.leads.get_page(3, limit=2, with_=["contacts"], query="Иван")

    assert isinstance(page, Page)
    assert page.number == 3
    assert [lead.id for lead in page] == [7, 8]
    assert len(page) == 2
    assert page.has_next
    assert page.next_url == "https://example.amocrm.ru/api/v4/leads?page=4&limit=2"
    params = route.calls.last.request.url.params
    assert params["page"] == "3"
    assert params["with"] == "contacts"
    assert params["query"] == "Иван"


async def test_get_page_on_204(amo: AmoCRMClient, mock: respx.MockRouter) -> None:
    mock.get("/api/v4/leads").mock(return_value=httpx.Response(204))
    page = await amo.leads.get_page(2)
    assert page.items == []
    assert page.number == 2
    assert not page.has_next


async def test_limit_is_checked_before_any_request(amo: AmoCRMClient) -> None:
    with pytest.raises(ValueError, match="limit"):
        amo.leads.iterate(limit=500)
    with pytest.raises(ValueError, match="limit"):
        await amo.leads.get_page(limit=0)


async def test_a_page_without_embedded_items_is_empty(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    mock.get("/api/v4/leads").mock(return_value=httpx.Response(200, json={"_page": 1}))
    assert [lead async for lead in amo.leads.iterate()] == []
