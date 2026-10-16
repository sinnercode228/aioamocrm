"""Query strings in the PHP bracket notation amoCRM reads.

``{"filter": {"id": [1, 2], "updated_at": {"from": 1}}}`` becomes
``filter[id][0]=1&filter[id][1]=2&filter[updated_at][from]=1``. List parameters ``with`` are
comma separated (``with=contacts,loss_reason``), and ``limit`` is at most 250, as in the
amoCRM v4 documentation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["MAX_LIMIT", "encode", "list_params"]

MAX_LIMIT = 250

QueryPairs = list[tuple[str, str]]


def encode(prefix: str, value: Any) -> QueryPairs:
    """Flatten ``value`` under the name ``prefix``; ``None`` values are left out."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        pairs: QueryPairs = []
        for key, item in value.items():
            pairs.extend(encode(f"{prefix}[{key}]", item))
        return pairs
    if isinstance(value, (list, tuple)):
        pairs = []
        for index, item in enumerate(value):
            pairs.extend(encode(f"{prefix}[{index}]", item))
        return pairs
    if isinstance(value, bool):
        return [(prefix, "1" if value else "0")]
    return [(prefix, str(value))]


def encode_mapping(params: Mapping[str, Any]) -> QueryPairs:
    """Flatten every entry of ``params``."""
    pairs: QueryPairs = []
    for key, value in params.items():
        pairs.extend(encode(key, value))
    return pairs


def list_params(
    *,
    page: int | None = None,
    limit: int | None = None,
    filter: Mapping[str, Any] | None = None,
    order: Mapping[str, str] | None = None,
    with_: str | Sequence[str] | None = None,
    query: str | int | None = None,
    params: Mapping[str, Any] | None = None,
) -> QueryPairs:
    """Build the query of a list request.

    Args:
        page: Page number, from 1.
        limit: Items per page, 1 to 250.
        filter: ``filter[...]`` parameters, nested as amoCRM documents them.
        order: ``order[field]=asc|desc``.
        with_: Related data to include, such as ``["contacts"]``.
        query: Full-text search (``query=``).
        params: Any other parameters, flattened the same way.

    Raises:
        ValueError: ``page`` or ``limit`` is out of range.
    """
    if page is not None and page < 1:
        msg = "page must be 1 or more"
        raise ValueError(msg)
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)
    pairs: QueryPairs = []
    if page is not None:
        pairs.append(("page", str(page)))
    if limit is not None:
        pairs.append(("limit", str(limit)))
    if filter:
        pairs.extend(encode("filter", filter))
    if order:
        pairs.extend(encode("order", order))
    if with_:
        pairs.append(("with", with_ if isinstance(with_, str) else ",".join(with_)))
    if query is not None and query != "":
        pairs.append(("query", str(query)))
    if params:
        pairs.extend(encode_mapping(params))
    return pairs
