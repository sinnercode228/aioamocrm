"""Endpoint groups reached through :class:`~aioamocrm.AmoCRMClient` attributes.

Paths, bodies and answers follow the amoCRM v4 documentation and were not checked against a
live account. Batch writes are split into chunks of ``batch_size`` items (50 by default; the
documentation asks for at most 50 leads per ``/leads/complex`` call and allows up to 250
items in other writes). Writes are not retried on 5xx or timeouts unless ``retry=True``: amoCRM
has no idempotency keys, so a repeated ``POST`` may create the same lead twice.
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ._errors import AmoCRMError, NotFoundError
from ._models import (
    Account,
    AmoModel,
    Company,
    ComplexLeadResult,
    Contact,
    CustomField,
    EntityRef,
    Lead,
    Link,
    Note,
    Pipeline,
    Status,
    Task,
    User,
    _Entity,
)
from ._pagination import Page, embedded_items, fetch_page, iterate_pages
from ._params import MAX_LIMIT, list_params

if TYPE_CHECKING:
    from ._client import AmoCRMClient

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "AccountResource",
    "CompaniesResource",
    "ContactsResource",
    "CustomFieldsResource",
    "EntityResource",
    "LeadsResource",
    "NotesResource",
    "PipelinesResource",
    "TasksResource",
    "UsersResource",
]

DEFAULT_BATCH_SIZE = 50

ModelT = TypeVar("ModelT", bound=AmoModel)
ResultT = TypeVar("ResultT")
EntityT = TypeVar("EntityT", bound=_Entity)


def _id(value: int, name: str = "id") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an int, not {type(value).__name__}"
        raise TypeError(msg)
    return value


def _body(item: object) -> dict[str, Any]:
    if isinstance(item, AmoModel):
        return item.to_api()
    if isinstance(item, Mapping):
        return dict(item)
    msg = f"expected a model or a mapping, not {type(item).__name__}"
    raise TypeError(msg)


def _check_batch_size(batch_size: int) -> None:
    if not 1 <= batch_size <= MAX_LIMIT:
        msg = f"batch_size must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)


def _refs(key: str) -> Callable[[Any], list[EntityRef]]:
    def parse(data: Any) -> list[EntityRef]:
        return [EntityRef.model_validate(item) for item in embedded_items(data, key)]

    return parse


def _complex_results(data: Any) -> list[ComplexLeadResult]:
    if not isinstance(data, list):
        return []
    return [ComplexLeadResult.model_validate(item) for item in data if isinstance(item, Mapping)]


async def _write_batches(
    client: AmoCRMClient,
    method: str,
    path: str,
    bodies: Sequence[dict[str, Any]],
    *,
    batch_size: int,
    retry: bool,
    parse: Callable[[Any], list[ResultT]],
) -> list[ResultT]:
    results: list[ResultT] = []
    for offset in range(0, len(bodies), batch_size):
        chunk = list(bodies[offset : offset + batch_size])
        try:
            data = await client.request(method, path, json=chunk, retry=retry)
        except AmoCRMError as exc:
            exc.batch_offset = offset
            exc.completed = list(results)
            raise
        results.extend(parse(data))
    return results


async def _get_one(
    client: AmoCRMClient, path: str, model: type[ModelT], params: list[tuple[str, str]]
) -> ModelT:
    data = await client.request("GET", path, params=params)
    if data is None:
        # amoCRM may answer 204 No Content for an id it does not know.
        msg = f"GET /api/v4/{path}: no such entity (204 No Content)"
        raise NotFoundError(msg, status_code=204, method="GET", url=client.api_url + path)
    return model.model_validate(data)


class _Listing(Generic[ModelT]):
    """``get_page`` and ``iterate`` over one list endpoint."""

    def __init__(self, client: AmoCRMClient, path: str, key: str, model: type[ModelT]) -> None:
        self._client = client
        self._path = path
        self._key = key
        self._model = model

    async def get_page(
        self,
        page: int = 1,
        *,
        limit: int = MAX_LIMIT,
        filter: Mapping[str, Any] | None = None,
        order: Mapping[str, str] | None = None,
        with_: str | Sequence[str] | None = None,
        query: str | int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Page[ModelT]:
        """Fetch one page.

        Args:
            page: Page number, from 1.
            limit: Items per page, at most 250.
            filter: ``filter[...]`` parameters, such as
                ``{"updated_at": {"from": 1700000000}}`` or ``{"id": [1, 2]}``.
            order: Sorting, such as ``{"updated_at": "desc"}``.
            with_: Related data, such as ``["contacts"]``.
            query: Full-text search.
            params: Any other query parameters.

        Returns:
            The page; an empty one when amoCRM answers 204.
        """
        query_pairs = list_params(
            page=page,
            limit=limit,
            filter=filter,
            order=order,
            with_=with_,
            query=query,
            params=params,
        )
        return await fetch_page(
            self._client, self._path, self._key, self._model, query=query_pairs, number=page
        )

    def iterate(
        self,
        *,
        limit: int = MAX_LIMIT,
        filter: Mapping[str, Any] | None = None,
        order: Mapping[str, str] | None = None,
        with_: str | Sequence[str] | None = None,
        query: str | int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ModelT]:
        """Iterate over every item, page after page.

        Takes the same arguments as :meth:`get_page` (without ``page``). Pages are fetched
        lazily, one at a time, as the loop needs them. An empty list (204) yields nothing.

        Example::

            async for lead in amo.leads.iterate(filter={"updated_at": {"from": since}}):
                ...
        """
        list_params(limit=limit)  # fail now, not at the first iteration
        query_pairs = list_params(
            filter=filter, order=order, with_=with_, query=query, params=params
        )
        return iterate_pages(
            self._client, self._path, self._key, self._model, query=query_pairs, limit=limit
        )


class NotesResource:
    """Notes of one entity type: ``amo.leads.notes``, ``amo.contacts.notes``..."""

    def __init__(self, client: AmoCRMClient, entity_type: str) -> None:
        self._client = client
        self.entity_type = entity_type

    def _listing(self, entity_id: int | None) -> _Listing[Note]:
        path = f"{self.entity_type}/notes"
        if entity_id is not None:
            path = f"{self.entity_type}/{_id(entity_id, 'entity_id')}/notes"
        return _Listing(self._client, path, "notes", Note)

    async def get_page(
        self,
        page: int = 1,
        *,
        entity_id: int | None = None,
        limit: int = MAX_LIMIT,
        filter: Mapping[str, Any] | None = None,
        order: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Page[Note]:
        """Fetch one page of notes, of one entity when ``entity_id`` is given."""
        return await self._listing(entity_id).get_page(
            page, limit=limit, filter=filter, order=order, params=params
        )

    def iterate(
        self,
        *,
        entity_id: int | None = None,
        limit: int = MAX_LIMIT,
        filter: Mapping[str, Any] | None = None,
        order: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Note]:
        """Iterate over notes, of one entity when ``entity_id`` is given.

        ``filter`` takes ``note_type``, ``id`` and ``updated_at``, and ``entity_id`` for the
        notes of several entities.
        """
        return self._listing(entity_id).iterate(
            limit=limit, filter=filter, order=order, params=params
        )

    async def get(self, note_id: int) -> Note:
        """Fetch one note by id.

        Raises:
            NotFoundError: There is no such note.
        """
        path = f"{self.entity_type}/notes/{_id(note_id, 'note_id')}"
        return await _get_one(self._client, path, Note, [])

    async def create(
        self,
        notes: Iterable[Note | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[EntityRef]:
        """Add notes; each names its entity with ``entity_id``.

        Example::

            await amo.leads.notes.create([Note.common(lead_id, "Callback after 18:00")])

        Returns:
            One reference per note, in order: ``id``, ``entity_id`` and ``request_id``.
        """
        _check_batch_size(batch_size)
        bodies = [_body(note) for note in notes]
        return await _write_batches(
            self._client,
            "POST",
            f"{self.entity_type}/notes",
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_refs("notes"),
        )


class CustomFieldsResource(_Listing[CustomField]):
    """Custom field definitions of one entity type: ``amo.leads.custom_fields``..."""

    def __init__(self, client: AmoCRMClient, entity_type: str) -> None:
        super().__init__(client, f"{entity_type}/custom_fields", "custom_fields", CustomField)
        self.entity_type = entity_type

    async def get(self, field_id: int) -> CustomField:
        """Fetch one field definition by id.

        Raises:
            NotFoundError: There is no such field.
        """
        path = f"{self._path}/{_id(field_id, 'field_id')}"
        return await _get_one(self._client, path, CustomField, [])


class EntityResource(_Listing[EntityT]):
    """Leads, contacts or companies: list, get, batch create and update, links, notes."""

    def __init__(self, client: AmoCRMClient, path: str, model: type[EntityT]) -> None:
        super().__init__(client, path, path, model)
        self.notes = NotesResource(client, path)
        self.custom_fields = CustomFieldsResource(client, path)

    async def get(self, entity_id: int, *, with_: str | Sequence[str] | None = None) -> EntityT:
        """Fetch one entity by id.

        Raises:
            NotFoundError: There is no such entity (404, or 204 No Content).
        """
        path = f"{self._path}/{_id(entity_id)}"
        return await _get_one(self._client, path, self._model, list_params(with_=with_))

    async def create(
        self,
        items: Iterable[EntityT | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[EntityRef]:
        """Create entities, ``batch_size`` per request.

        Models are sent with :meth:`~aioamocrm.AmoModel.to_api`; mappings as they are.

        Returns:
            One reference per item, in order, with the new ``id``.

        Raises:
            AmoCRMError: A chunk failed. ``batch_offset`` is the input index of its first item
                (the ``request_id`` of a validation issue counts from there) and ``completed``
                holds the references of the chunks saved before it.
        """
        _check_batch_size(batch_size)
        bodies = [_body(item) for item in items]
        return await _write_batches(
            self._client,
            "POST",
            self._path,
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_refs(self._key),
        )

    async def update(
        self,
        items: Iterable[EntityT | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[EntityRef]:
        """Update entities, ``batch_size`` per request; every item needs its ``id``.

        Build partial models (``Lead(id=1, status_id=142)``) or mappings: a model fetched from
        the API sends back every field it was read with.

        Returns:
            One reference per item, with ``id`` and ``updated_at``.

        Raises:
            ValueError: An item has no ``id``.
            AmoCRMError: A chunk failed; see :meth:`create`.
        """
        _check_batch_size(batch_size)
        bodies = [_body(item) for item in items]
        for index, data in enumerate(bodies):
            if data.get("id") is None:
                msg = f"item {index} has no id; updates need the id of each entity"
                raise ValueError(msg)
        return await _write_batches(
            self._client,
            "PATCH",
            self._path,
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_refs(self._key),
        )

    async def link(
        self,
        entity_id: int,
        to_entity_type: str,
        to_entity_id: int,
        *,
        metadata: Mapping[str, Any] | None = None,
        retry: bool = False,
    ) -> list[Link]:
        """Link another entity to this one, such as a contact to a lead.

        Args:
            entity_id: Id of this entity.
            to_entity_type: ``"contacts"``, ``"companies"``, ``"leads"``, ``"catalog_elements"``...
            to_entity_id: Id of the entity to link.
            metadata: Link metadata, such as ``{"is_main": True}`` for a lead's main contact or
                ``{"catalog_id": 1, "quantity": 2}`` for a catalog element.
            retry: Retry on 5xx and timeouts.
        """
        path = f"{self._path}/{_id(entity_id, 'entity_id')}/link"
        data = await self._client.request(
            "POST",
            path,
            json=[self._link_body(to_entity_type, to_entity_id, metadata)],
            retry=retry,
        )
        return [Link.model_validate(item) for item in embedded_items(data, "links")]

    async def unlink(
        self,
        entity_id: int,
        to_entity_type: str,
        to_entity_id: int,
        *,
        metadata: Mapping[str, Any] | None = None,
        retry: bool = False,
    ) -> None:
        """Remove a link made with :meth:`link`."""
        path = f"{self._path}/{_id(entity_id, 'entity_id')}/unlink"
        await self._client.request(
            "POST",
            path,
            json=[self._link_body(to_entity_type, to_entity_id, metadata)],
            retry=retry,
        )

    async def links(self, entity_id: int, *, filter: Mapping[str, Any] | None = None) -> list[Link]:
        """List the links of one entity; ``filter`` takes ``to_entity_type``, ``to_entity_id``."""
        path = f"{self._path}/{_id(entity_id, 'entity_id')}/links"
        data = await self._client.request("GET", path, params=list_params(filter=filter))
        return [Link.model_validate(item) for item in embedded_items(data, "links")]

    @staticmethod
    def _link_body(
        to_entity_type: str, to_entity_id: int, metadata: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "to_entity_id": _id(to_entity_id, "to_entity_id"),
            "to_entity_type": to_entity_type,
        }
        if metadata:
            body["metadata"] = dict(metadata)
        return body


class LeadsResource(EntityResource[Lead]):
    """``amo.leads``: everything of :class:`EntityResource` plus complex creation."""

    def __init__(self, client: AmoCRMClient) -> None:
        super().__init__(client, "leads", Lead)

    async def create_complex(
        self,
        leads: Iterable[Lead | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[ComplexLeadResult]:
        """Create leads together with their contact and company (``POST /leads/complex``).

        Put the new contact and company in ``embedded``::

            lead = Lead(name="Website form", embedded=Embedded(contacts=[contact]))
            [result] = await amo.leads.create_complex([lead])
            result.id, result.contact_id

        The documentation allows at most 50 leads per call, which is the default
        ``batch_size``.

        Returns:
            One result per lead with ``id``, ``contact_id``, ``company_id`` and ``merged``
            (whether amoCRM merged the contact into an existing duplicate).
        """
        _check_batch_size(batch_size)
        bodies = [_body(lead) for lead in leads]
        return await _write_batches(
            self._client,
            "POST",
            "leads/complex",
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_complex_results,
        )


class ContactsResource(EntityResource[Contact]):
    """``amo.contacts``."""

    def __init__(self, client: AmoCRMClient) -> None:
        super().__init__(client, "contacts", Contact)


class CompaniesResource(EntityResource[Company]):
    """``amo.companies``."""

    def __init__(self, client: AmoCRMClient) -> None:
        super().__init__(client, "companies", Company)


class TasksResource(_Listing[Task]):
    """``amo.tasks``: list, get, create, update and complete tasks.

    ``filter`` takes ``responsible_user_id``, ``is_completed``, ``task_type``,
    ``entity_type``, ``entity_id``, ``id`` and ``updated_at``.
    """

    def __init__(self, client: AmoCRMClient) -> None:
        super().__init__(client, "tasks", "tasks", Task)

    async def get(self, task_id: int) -> Task:
        """Fetch one task by id.

        Raises:
            NotFoundError: There is no such task.
        """
        return await _get_one(self._client, f"tasks/{_id(task_id, 'task_id')}", Task, [])

    async def create(
        self,
        tasks: Iterable[Task | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[EntityRef]:
        """Create tasks; each needs ``text`` and ``complete_till`` (a Unix time)."""
        _check_batch_size(batch_size)
        bodies = [_body(task) for task in tasks]
        return await _write_batches(
            self._client,
            "POST",
            "tasks",
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_refs("tasks"),
        )

    async def update(
        self,
        tasks: Iterable[Task | Mapping[str, Any]],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        retry: bool = False,
    ) -> list[EntityRef]:
        """Update tasks; every item needs its ``id``.

        Raises:
            ValueError: An item has no ``id``.
        """
        _check_batch_size(batch_size)
        bodies = [_body(task) for task in tasks]
        for index, data in enumerate(bodies):
            if data.get("id") is None:
                msg = f"item {index} has no id; updates need the id of each task"
                raise ValueError(msg)
        return await _write_batches(
            self._client,
            "PATCH",
            "tasks",
            bodies,
            batch_size=batch_size,
            retry=retry,
            parse=_refs("tasks"),
        )

    async def complete(
        self, task_id: int, *, result: str | None = None, retry: bool = False
    ) -> EntityRef:
        """Mark a task as done, with an optional result text."""
        body: dict[str, Any] = {"is_completed": True}
        if result is not None:
            body["result"] = {"text": result}
        data = await self._client.request(
            "PATCH", f"tasks/{_id(task_id, 'task_id')}", json=body, retry=retry
        )
        return EntityRef.model_validate(data or {"id": task_id})


class PipelinesResource:
    """``amo.pipelines``: lead pipelines and their statuses (not paginated)."""

    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def list(self) -> builtins.list[Pipeline]:
        """Return every pipeline with its statuses."""
        data = await self._client.request("GET", "leads/pipelines")
        return [Pipeline.model_validate(item) for item in embedded_items(data, "pipelines")]

    async def get(self, pipeline_id: int) -> Pipeline:
        """Fetch one pipeline.

        Raises:
            NotFoundError: There is no such pipeline.
        """
        path = f"leads/pipelines/{_id(pipeline_id, 'pipeline_id')}"
        return await _get_one(self._client, path, Pipeline, [])

    async def statuses(self, pipeline_id: int) -> builtins.list[Status]:
        """Return the statuses of one pipeline."""
        path = f"leads/pipelines/{_id(pipeline_id, 'pipeline_id')}/statuses"
        data = await self._client.request("GET", path)
        return [Status.model_validate(item) for item in embedded_items(data, "statuses")]


class UsersResource(_Listing[User]):
    """``amo.users``. ``with_`` takes ``role``, ``group``, ``uuid``, ``amojo_id``..."""

    def __init__(self, client: AmoCRMClient) -> None:
        super().__init__(client, "users", "users", User)

    async def get(self, user_id: int, *, with_: str | Sequence[str] | None = None) -> User:
        """Fetch one user by id.

        Raises:
            NotFoundError: There is no such user.
        """
        path = f"users/{_id(user_id, 'user_id')}"
        return await _get_one(self._client, path, User, list_params(with_=with_))


class AccountResource:
    """``amo.account``."""

    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def get(self, *, with_: str | Sequence[str] | None = None) -> Account:
        """Fetch the account; ``with_`` takes ``amojo_id``, ``users_groups``, ``task_types``..."""
        return await _get_one(self._client, "account", Account, list_params(with_=with_))
