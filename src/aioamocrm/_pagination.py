"""Pages of list endpoints and the async iterator that walks them.

A list answer looks like ``{"_page": 1, "_links": {"self": ..., "next": {"href": ...}},
"_embedded": {"leads": [...]}}``; a list with nothing in it is ``204 No Content``. Both come
from the amoCRM v4 documentation and were not checked against a live account.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ._models import AmoModel

if TYPE_CHECKING:
    from ._client import AmoCRMClient

__all__ = ["Page", "embedded_items", "fetch_page", "iterate_pages"]

ItemT = TypeVar("ItemT")
ModelT = TypeVar("ModelT", bound=AmoModel)


@dataclass(frozen=True)
class Page(Generic[ItemT]):
    """One page of a list endpoint.

    Iterating a page iterates its items.

    Attributes:
        items: The entities on this page; empty for a ``204 No Content`` answer.
        number: The page number (``_page``), from 1.
        next_url: ``_links.next.href``, or ``None`` on the last page.
    """

    items: list[ItemT] = field(default_factory=list)
    number: int = 1
    next_url: str | None = None

    @property
    def has_next(self) -> bool:
        """Whether amoCRM linked a next page."""
        return self.next_url is not None

    def __iter__(self) -> Iterator[ItemT]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


def embedded_items(data: Any, key: str) -> list[Any]:
    """Return ``data["_embedded"][key]`` as a list, or ``[]`` for any other shape."""
    if not isinstance(data, Mapping):
        return []
    embedded = data.get("_embedded")
    if not isinstance(embedded, Mapping):
        return []
    items = embedded.get(key)
    return items if isinstance(items, list) else []


def _next_url(data: Any) -> str | None:
    if not isinstance(data, Mapping):
        return None
    links = data.get("_links")
    if not isinstance(links, Mapping):
        return None
    nxt = links.get("next")
    href = nxt.get("href") if isinstance(nxt, Mapping) else None
    return href if isinstance(href, str) and href else None


def parse_page(data: Any, key: str, model: type[ModelT], number: int) -> Page[ModelT]:
    """Build a :class:`Page` from a decoded list answer (``None`` for 204)."""
    items = [model.model_validate(item) for item in embedded_items(data, key)]
    page_number = data.get("_page") if isinstance(data, Mapping) else None
    return Page(
        items=items,
        number=page_number if isinstance(page_number, int) else number,
        next_url=_next_url(data),
    )


async def fetch_page(
    client: AmoCRMClient,
    path: str,
    key: str,
    model: type[ModelT],
    *,
    query: list[tuple[str, str]],
    number: int,
) -> Page[ModelT]:
    """Request one page."""
    data = await client.request("GET", path, params=query)
    return parse_page(data, key, model, number)


async def iterate_pages(
    client: AmoCRMClient,
    path: str,
    key: str,
    model: type[ModelT],
    *,
    query: list[tuple[str, str]],
    limit: int,
) -> AsyncIterator[ModelT]:
    """Yield the items of every page, from page 1.

    ``query`` holds everything but ``page`` and ``limit``. The walk follows ``_links.next``
    when it points into the account's API (otherwise it asks for the next page number with the
    same parameters) and stops at the first page that is empty (204), shorter than ``limit``,
    or has no ``next`` link.
    """
    number = 1
    url = path
    params: list[tuple[str, str]] | None = [("page", "1"), ("limit", str(limit)), *query]
    while True:
        data = await client.request("GET", url, params=params)
        page = parse_page(data, key, model, number)
        for item in page.items:
            yield item
        if page.next_url is None or len(page.items) < limit:
            return
        number += 1
        if client.owns_url(page.next_url) and page.next_url != url:
            url, params = page.next_url, None
        else:
            url = path
            params = [("page", str(number)), ("limit", str(limit)), *query]
