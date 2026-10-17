from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, Lead, Page

from . import payloads
from .conftest import API


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
    # Later pages are the hrefs amoCRM gave in _links.next, plus the filter they left out.
    for call, number in zip(route.calls[1:], ("2", "3"), strict=True):
        params = call.request.url.params
        assert params["page"] == number
        assert params["limit"] == "2"
        assert params.get_list("filter[id][4]") == ["5"]


async def test_a_short_page_with_a_next_link_is_followed(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    # The next link is what says another page exists, as in the official PHP SDK; the size of
    # the page does not.
    route = mock.get("/api/v4/leads").mock(
        side_effect=[
            leads_page([1, 2, 3], 1, limit=5, has_next=True),
            leads_page([4], 2, limit=5, has_next=False),
        ]
    )
    assert [lead.id async for lead in amo.leads.iterate(limit=5)] == [1, 2, 3, 4]
    assert route.call_count == 2


async def test_an_endpoint_that_serves_smaller_pages_is_walked_to_the_end(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    # iterate() asks for 250 per page; an endpoint that serves at most 50 still links the next.
    def capped(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        ids = range((number - 1) * 50 + 1, min(number * 50, 120) + 1)
        body = payloads.page(
            "custom_fields",
            [payloads.custom_field(i) for i in ids],
            number=number,
            path="leads/custom_fields",
            limit=50,
            has_next=number < 3,
        )
        return httpx.Response(200, json=body)

    route = mock.get("/api/v4/leads/custom_fields").mock(side_effect=capped)

    fields = [field.id async for field in amo.leads.custom_fields.iterate()]

    assert fields == list(range(1, 121))
    assert route.call_count == 3
    assert route.calls[0].request.url.params["limit"] == "250"


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


async def test_an_empty_page_with_a_next_link_ends_the_walk(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=[
            leads_page([1, 2], 1, limit=2, has_next=True),
            leads_page([], 2, limit=2, has_next=True),
            leads_page([3], 3, limit=2, has_next=False),
        ]
    )
    assert [lead.id async for lead in amo.leads.iterate(limit=2)] == [1, 2]
    assert route.call_count == 2


async def test_parameters_missing_from_the_next_link_are_kept(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    body = payloads.page("leads", [payloads.lead(1), payloads.lead(2)], limit=2)
    # A next link that carries only page and limit must not drop with= or filter[...].
    body["_links"]["next"] = {"href": "https://example.amocrm.ru/api/v4/leads?page=2&limit=2"}
    route = mock.get("/api/v4/leads").mock(
        side_effect=[httpx.Response(200, json=body), leads_page([3], 2, limit=2, has_next=False)]
    )

    ids = [
        lead.id
        async for lead in amo.leads.iterate(
            limit=2, with_=["contacts"], filter={"updated_at": {"from": 1700000000}}
        )
    ]

    assert ids == [1, 2, 3]
    second = route.calls[1].request.url.params
    assert second["page"] == "2"
    assert second["limit"] == "2"
    assert second["with"] == "contacts"
    assert second["filter[updated_at][from]"] == "1700000000"
    assert len(second.multi_items()) == 4


def numbered_leads(total: int, next_href: Callable[[int], str]) -> Callable[..., httpx.Response]:
    """A list endpoint over leads 1..total that honours page and limit like amoCRM."""

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        # Without limit the server uses its own default, smaller than what the walk asked for.
        number, limit = int(params["page"]), int(params.get("limit", "2"))
        ids = list(range((number - 1) * limit + 1, min(number * limit, total) + 1))
        if not ids:
            return httpx.Response(204)
        body = payloads.page("leads", [payloads.lead(i) for i in ids], number=number, limit=limit)
        body["_links"]["next"] = {"href": next_href(number)}
        return httpx.Response(200, json=body)

    return answer


async def test_a_next_link_without_limit_keeps_the_page_size(
    amo: AmoCRMClient, mock: respx.MockRouter
) -> None:
    route = mock.get("/api/v4/leads").mock(
        side_effect=numbered_leads(7, lambda number: f"{API}/leads?page={number + 1}")
    )

    ids = [lead.id async for lead in amo.leads.iterate(limit=3)]

    assert ids == [1, 2, 3, 4, 5, 6, 7]
    # Page 3 is short but still links page 4, which answers 204.
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2", "3", "4"]
    assert [call.request.url.params["limit"] for call in route.calls] == ["3"] * 4


@pytest.mark.parametrize("stuck_on", [1, 2])
async def test_a_next_link_that_does_not_move_forward_is_not_followed(
    amo: AmoCRMClient, mock: respx.MockRouter, stuck_on: int
) -> None:
    # A next link that keeps pointing at the same page (or back) must neither loop over
    # pages already seen nor skip the ones after them.
    route = mock.get("/api/v4/leads").mock(
        side_effect=numbered_leads(10, lambda number: f"{API}/leads?page={stuck_on}&limit=3")
    )

    ids = [lead.id async for lead in amo.leads.iterate(limit=3)]

    assert ids == list(range(1, 11))
    pages = [call.request.url.params["page"] for call in route.calls]
    assert pages == ["1", "2", "3", "4", "5"]
