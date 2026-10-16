"""Parsing of amoCRM account webhooks.

amoCRM posts webhooks as ``application/x-www-form-urlencoded`` bodies whose keys use PHP
bracket notation, such as ``leads[add][0][id]=1``,
``contacts[update][0][custom_fields][0][values][0][value]=+7...`` and
``account[subdomain]=example``. One body may carry several entities and actions. Values are
strings, exactly as sent.

**These webhooks are not signed.** Anyone who knows the URL can post to it, so put a secret
in the URL (a long random path segment or a ``?token=`` compared with
:func:`hmac.compare_digest`) and check it before parsing. Answer with a 2xx quickly and do the
work in the background: the amoCRM documentation says that an endpoint that keeps failing or
answering slowly gets the webhook resent and eventually switched off.

The body format comes from the amoCRM webhook documentation and public examples; it was not
recorded from a live account here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from ._models import FieldKey

__all__ = ["MAX_DEPTH", "Webhook", "WebhookEvent", "parse_form", "parse_webhook"]

#: Deepest bracket nesting accepted in a key; real webhooks stay well under 10.
MAX_DEPTH = 32

_KEY = re.compile(r"([^\[\]]+)((?:\[[^\[\]]*\])*)")
_SEGMENT = re.compile(r"\[([^\[\]]*)\]")


def _is_index(key: str) -> bool:
    return key.isascii() and key.isdigit()


def _next_index(node: Mapping[str, Any]) -> str:
    indexes = [int(key) for key in node if _is_index(key)]
    return str(max(indexes) + 1 if indexes else 0)


def _listify(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    converted = {key: _listify(item) for key, item in value.items()}
    if converted and all(_is_index(key) for key in converted):
        return [converted[key] for key in sorted(converted, key=int)]
    return converted


def parse_form(body: bytes | str, *, max_fields: int = 10_000) -> dict[str, Any]:
    """Decode a form body with PHP bracket keys into nested dicts and lists.

    ``a[b][0][c]=1&a[b][1][c]=2`` becomes ``{"a": {"b": [{"c": "1"}, {"c": "2"}]}}``: objects
    whose keys are all numbers become lists in index order, and ``[]`` appends. A key that is
    not well-formed bracket notation is kept as it is.

    Raises:
        ValueError: The body is not UTF-8, has more than ``max_fields`` fields, or nests
            deeper than :data:`MAX_DEPTH`.
    """
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        pairs = parse_qsl(
            text,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=max_fields,
        )
    except UnicodeDecodeError as exc:
        msg = "the webhook body is not valid UTF-8"
        raise ValueError(msg) from exc
    except ValueError as exc:
        msg = f"the webhook body has more than {max_fields} fields"
        raise ValueError(msg) from exc

    root: dict[str, Any] = {}
    for key, value in pairs:
        match = _KEY.fullmatch(key)
        if match is None:
            root[key] = value
            continue
        segments = [match.group(1), *_SEGMENT.findall(match.group(2))]
        if len(segments) > MAX_DEPTH:
            msg = f"a webhook key nests too deep (more than {MAX_DEPTH} levels)"
            raise ValueError(msg)
        node = root
        for segment in segments[:-1]:
            name = segment or _next_index(node)
            child = node.get(name)
            if not isinstance(child, dict):
                child = {}
                node[name] = child
            node = child
        node[segments[-1] or _next_index(node)] = value
    result: dict[str, Any] = _listify(root)
    return result


def _as_int(value: Any) -> int | None:
    if isinstance(value, str) and _is_index(value):
        return int(value)
    return None


@dataclass(frozen=True)
class WebhookEvent:
    """One entity in one action of a webhook.

    Attributes:
        entity: The top-level key: ``"leads"``, ``"contacts"``, ``"companies"``,
            ``"customers"``, ``"task"``...
        action: ``"add"``, ``"update"``, ``"delete"``, ``"restore"``, ``"status"``,
            ``"responsible"``, ``"note"``...
        payload: The entity's fields as sent, string values, nested dicts and lists. For
            ``note`` actions the note itself is under ``payload["note"]``, with
            ``element_id`` naming the entity it was added to.
    """

    entity: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> int | None:
        """``payload["id"]`` as an int, when it is a number."""
        return _as_int(self.payload.get("id"))

    @property
    def custom_fields(self) -> list[dict[str, Any]]:
        """``payload["custom_fields"]``: dicts with ``id``, ``name``, maybe ``code``, ``values``."""
        fields = self.payload.get("custom_fields")
        if isinstance(fields, dict):
            fields = list(fields.values())
        if not isinstance(fields, list):
            return []
        return [item for item in fields if isinstance(item, dict)]

    def _field(self, key: FieldKey) -> dict[str, Any] | None:
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            msg = "a custom field key is an id (int) or a code (str)"
            raise TypeError(msg)
        for item in self.custom_fields:
            if isinstance(key, int):
                if str(item.get("id")) == str(key):
                    return item
            elif str(item.get("code") or "").upper() == key.upper():
                return item
        return None

    def field_values(self, key: FieldKey) -> list[Any]:
        """Return the values of a custom field by id (int) or code (str, such as ``"PHONE"``).

        Both shapes amoCRM uses are read: ``values[0][value]=...`` and ``values[0]=...``.
        """
        item = self._field(key)
        if item is None:
            return []
        values = item.get("values")
        if isinstance(values, dict):
            values = list(values.values())
        if not isinstance(values, list):
            return []
        return [value.get("value") if isinstance(value, dict) else value for value in values]

    def field_value(self, key: FieldKey, default: Any = None) -> Any:
        """Return the first value of a custom field, or ``default``."""
        values = self.field_values(key)
        return values[0] if values else default


@dataclass(frozen=True)
class Webhook:
    """A parsed webhook body.

    Attributes:
        events: Every entity of every action, in body order.
        account: The ``account`` block: ``subdomain``, ``id``, ``_links``.
        data: The whole body as nested dicts and lists.
    """

    events: tuple[WebhookEvent, ...] = ()
    account: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def subdomain(self) -> str | None:
        """``account[subdomain]``."""
        value = self.account.get("subdomain")
        return value if isinstance(value, str) else None

    @property
    def account_id(self) -> int | None:
        """``account[id]`` as an int."""
        return _as_int(self.account.get("id"))


def parse_webhook(body: bytes | str, *, max_fields: int = 10_000) -> Webhook:
    """Parse the raw body of an amoCRM webhook request.

    Framework-agnostic: pass the bytes as received (``await request.body()`` in FastAPI or
    Starlette, ``await request.read()`` in aiohttp). Verify the URL secret first, since these
    webhooks carry no signature.

    Example::

        webhook = parse_webhook(raw_body)
        for event in webhook.events:
            if (event.entity, event.action) == ("leads", "status"):
                print(event.id, event.payload["status_id"])

    Raises:
        ValueError: The body is not UTF-8, is too large or nests too deep.
    """
    data = parse_form(body, max_fields=max_fields)
    account = data.get("account")
    events: list[WebhookEvent] = []
    for entity, actions in data.items():
        if entity == "account" or not isinstance(actions, dict):
            continue
        for action, items in actions.items():
            batch = [items] if isinstance(items, dict) else items
            if not isinstance(batch, list):
                continue
            events.extend(
                WebhookEvent(entity=entity, action=action, payload=item)
                for item in batch
                if isinstance(item, dict)
            )
    return Webhook(
        events=tuple(events),
        account=account if isinstance(account, dict) else {},
        data=data,
    )
