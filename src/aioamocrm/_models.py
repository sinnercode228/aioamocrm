"""Pydantic models of amoCRM v4 entities.

Field names and shapes follow the amoCRM v4 documentation and were not checked against a live
account. Every model allows extra fields, so a field amoCRM adds later is kept (in
``model_extra``) instead of breaking validation, and every documented field is optional,
because what amoCRM returns depends on the method, the ``with`` parameter and the account.

``_links`` and ``_embedded`` are exposed as :attr:`links` and :attr:`embedded`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Account",
    "AmoModel",
    "Company",
    "ComplexLeadResult",
    "Contact",
    "CustomField",
    "CustomFieldEnum",
    "CustomFieldValue",
    "CustomFieldValues",
    "Embedded",
    "EntityRef",
    "FieldKey",
    "Lead",
    "Link",
    "Note",
    "Pipeline",
    "PipelineEmbedded",
    "Status",
    "Tag",
    "Task",
    "TaskResult",
    "User",
]

#: A custom field is named by its numeric ``field_id`` or by its ``field_code`` (``"PHONE"``).
FieldKey = int | str

# Parts of a fetched entity that amoCRM computes itself; they are never sent back.
_READ_ONLY = frozenset({"_links", "account_id", "is_deleted", "closest_task_at"})
# Parts of a custom_fields_values entry that only describe the field.
_FIELD_DESCRIPTION = frozenset({"field_name", "field_type"})


def _links_field() -> Any:
    return Field(
        default=None, validation_alias=AliasChoices("_links", "links"), serialization_alias="_links"
    )


def _check_key(key: FieldKey) -> None:
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        msg = "a custom field key is a field_id (int) or a field_code (str)"
        raise TypeError(msg)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _READ_ONLY:
                continue
            if key == "custom_fields_values" and isinstance(item, list):
                cleaned[key] = [_clean_field(entry) for entry in item]
            else:
                cleaned[key] = _clean(item)
        return cleaned
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _clean_field(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    cleaned = {k: v for k, v in entry.items() if k not in _FIELD_DESCRIPTION}
    if cleaned.get("field_id") is not None:
        cleaned.pop("field_code", None)  # name the field once
    return _clean(cleaned)


class AmoModel(BaseModel):
    """Base of every model: extra fields allowed and kept."""

    model_config = ConfigDict(extra="allow")

    def to_api(self) -> dict[str, Any]:
        """Return the JSON body for a write request.

        Only fields that were set are included (``None`` values are left out), and read-only
        parts of fetched entities are dropped: ``_links``, ``account_id``, ``is_deleted``,
        ``closest_task_at``, and ``field_name`` / ``field_type`` of custom field values.
        Send a dict instead of a model to control the body exactly (for example to send
        ``null``).
        """
        data = self.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude_none=True)
        cleaned: dict[str, Any] = _clean(data)
        return cleaned


class Tag(AmoModel):
    """A tag in ``_embedded.tags``."""

    id: int | None = None
    name: str | None = None
    color: str | None = None


class CustomFieldValue(AmoModel):
    """One value of a custom field: ``{"value": ..., "enum_id": ..., "enum_code": ...}``."""

    value: Any = None
    enum_id: int | None = None
    enum_code: str | None = None


class CustomFieldValues(AmoModel):
    """One entry of ``custom_fields_values``: a field and its values."""

    field_id: int | None = None
    field_name: str | None = None
    field_code: str | None = None
    field_type: str | None = None
    values: list[CustomFieldValue] | None = None

    def matches(self, key: FieldKey) -> bool:
        """Tell whether this is the field with that id, or with that code (any case)."""
        _check_key(key)
        if isinstance(key, int):
            return self.field_id == key
        return self.field_code is not None and self.field_code.upper() == key.upper()


class _Entity(AmoModel):
    """Fields and custom field helpers shared by leads, contacts and companies."""

    id: int | None = None
    name: str | None = None
    responsible_user_id: int | None = None
    group_id: int | None = None
    created_by: int | None = None
    updated_by: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
    account_id: int | None = None
    closest_task_at: int | None = None
    is_deleted: bool | None = None
    custom_fields_values: list[CustomFieldValues] | None = None
    links: dict[str, Any] | None = _links_field()
    embedded: Embedded = Field(
        default_factory=lambda: Embedded(),  # noqa: PLW0108 - Embedded is defined below
        validation_alias=AliasChoices("_embedded", "embedded"),
        serialization_alias="_embedded",
    )

    @field_validator("embedded", mode="before")
    @classmethod
    def _empty_embedded(cls, value: Any) -> Any:
        # PHP serialises an empty associative array as [].
        return {} if value is None or value == [] else value

    def field(self, key: FieldKey) -> CustomFieldValues | None:
        """Return the custom field with this ``field_id`` (int) or ``field_code`` (str)."""
        _check_key(key)
        for entry in self.custom_fields_values or []:
            if entry.matches(key):
                return entry
        return None

    def field_values(self, key: FieldKey) -> list[Any]:
        """Return every value of a custom field, or ``[]`` when the field is not filled."""
        entry = self.field(key)
        if entry is None:
            return []
        return [item.value for item in entry.values or []]

    def field_value(
        self, key: FieldKey, default: Any = None, *, enum_code: str | None = None
    ) -> Any:
        """Return the first value of a custom field.

        Args:
            key: ``field_id`` (int) or ``field_code`` (str, any case), such as ``"PHONE"``.
            default: Returned when the field or a matching value is missing.
            enum_code: Only consider values with this ``enum_code``, such as ``"WORK"`` or
                ``"MOB"`` for ``PHONE``.
        """
        entry = self.field(key)
        if entry is None:
            return default
        for item in entry.values or []:
            if enum_code is None or (
                item.enum_code is not None and item.enum_code.upper() == enum_code.upper()
            ):
                return item.value
        return default

    def set_field(
        self,
        key: FieldKey,
        *values: Any,
        enum_code: str | None = None,
        enum_id: int | None = None,
    ) -> None:
        """Set the values of a custom field, replacing what it had.

        A plain value becomes ``{"value": value}`` plus ``enum_code`` / ``enum_id`` when given;
        a :class:`CustomFieldValue` or a mapping is used as it is. A field already present is
        updated in place (keeping its ``field_id``); otherwise it is added by ``field_id`` (int
        key) or ``field_code`` (str key).

        Example::

            contact.set_field("PHONE", "+79123456789", enum_code="WORK")
            lead.set_field(123456, "Сайт")

        Raises:
            ValueError: No value was given.
        """
        _check_key(key)
        if not values:
            msg = "set_field needs at least one value"
            raise ValueError(msg)
        items: list[CustomFieldValue] = []
        for value in values:
            if isinstance(value, CustomFieldValue):
                items.append(value)
            elif isinstance(value, Mapping):
                items.append(CustomFieldValue.model_validate(dict(value)))
            else:
                items.append(CustomFieldValue(value=value, enum_code=enum_code, enum_id=enum_id))

        entries = list(self.custom_fields_values or [])
        for index, entry in enumerate(entries):
            if entry.matches(key):
                entries[index] = CustomFieldValues(
                    field_id=entry.field_id, field_code=entry.field_code, values=items
                )
                break
        else:
            if isinstance(key, int):
                entries.append(CustomFieldValues(field_id=key, values=items))
            else:
                entries.append(CustomFieldValues(field_code=key, values=items))
        # Assigning (rather than mutating) marks the field as set for to_api().
        self.custom_fields_values = entries


class Lead(_Entity):
    """A lead (``/api/v4/leads``)."""

    price: int | None = None
    status_id: int | None = None
    pipeline_id: int | None = None
    loss_reason_id: int | None = None
    closed_at: int | None = None
    score: int | None = None
    labor_cost: int | None = None


class Contact(_Entity):
    """A contact (``/api/v4/contacts``).

    ``is_main`` is set on the contacts embedded in a lead: it marks the main contact.
    """

    first_name: str | None = None
    last_name: str | None = None
    is_unsorted: bool | None = None
    is_main: bool | None = None


class Company(_Entity):
    """A company (``/api/v4/companies``)."""


class Embedded(AmoModel):
    """``_embedded`` of a lead, contact or company.

    Fetched entities embed references (``id`` and ``_links``; ``with=contacts`` on leads adds
    ``is_main``). For ``POST /api/v4/leads/complex`` the embedded contacts and companies are
    full entities to create.
    """

    tags: list[Tag] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    companies: list[Company] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)
    customers: list[dict[str, Any]] = Field(default_factory=list)
    catalog_elements: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator(
        "tags", "contacts", "companies", "leads", "customers", "catalog_elements", mode="before"
    )
    @classmethod
    def _null_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


class Note(AmoModel):
    """A note on a lead, contact or company (``/api/v4/{entity_type}/notes``)."""

    id: int | None = None
    entity_id: int | None = None
    note_type: str | None = None
    params: dict[str, Any] | None = None
    responsible_user_id: int | None = None
    group_id: int | None = None
    created_by: int | None = None
    updated_by: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
    account_id: int | None = None
    links: dict[str, Any] | None = _links_field()

    @classmethod
    def common(cls, entity_id: int, text: str) -> Note:
        """Build a plain text note (``note_type`` ``common``) for an entity."""
        return cls(entity_id=entity_id, note_type="common", params={"text": text})

    @property
    def text(self) -> str | None:
        """``params.text``, the text of common and service notes."""
        value = (self.params or {}).get("text")
        return value if isinstance(value, str) else None


class TaskResult(AmoModel):
    """The result written when a task was completed."""

    text: str | None = None


class Task(AmoModel):
    """A task (``/api/v4/tasks``).

    ``complete_till`` is a Unix time; ``task_type_id`` is 1 for a call and 2 for a meeting
    (or the id of a custom task type).
    """

    id: int | None = None
    text: str | None = None
    entity_id: int | None = None
    entity_type: str | None = None
    task_type_id: int | None = None
    complete_till: int | None = None
    duration: int | None = None
    is_completed: bool | None = None
    result: TaskResult | None = None
    responsible_user_id: int | None = None
    group_id: int | None = None
    created_by: int | None = None
    updated_by: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
    account_id: int | None = None
    links: dict[str, Any] | None = _links_field()

    @field_validator("result", mode="before")
    @classmethod
    def _empty_result(cls, value: Any) -> Any:
        # A task without a result is documented with "result": [] (an empty PHP array).
        return None if value == [] else value


class Status(AmoModel):
    """A stage of a pipeline. 142 is "won" and 143 is "lost" in every pipeline."""

    id: int | None = None
    name: str | None = None
    sort: int | None = None
    is_editable: bool | None = None
    pipeline_id: int | None = None
    color: str | None = None
    type: int | None = None
    account_id: int | None = None
    links: dict[str, Any] | None = _links_field()


class PipelineEmbedded(AmoModel):
    """``_embedded`` of a pipeline."""

    statuses: list[Status] = Field(default_factory=list)


class Pipeline(AmoModel):
    """A lead pipeline (``/api/v4/leads/pipelines``)."""

    id: int | None = None
    name: str | None = None
    sort: int | None = None
    is_main: bool | None = None
    is_unsorted_on: bool | None = None
    is_archive: bool | None = None
    account_id: int | None = None
    links: dict[str, Any] | None = _links_field()
    embedded: PipelineEmbedded = Field(
        default_factory=PipelineEmbedded,
        validation_alias=AliasChoices("_embedded", "embedded"),
        serialization_alias="_embedded",
    )

    @property
    def statuses(self) -> list[Status]:
        """The pipeline's stages."""
        return self.embedded.statuses

    def status(self, status_id: int) -> Status | None:
        """Return the stage with this id."""
        return next((s for s in self.embedded.statuses if s.id == status_id), None)


class CustomFieldEnum(AmoModel):
    """One option of a select, multiselect or multitext field."""

    id: int | None = None
    value: str | None = None
    sort: int | None = None


class CustomField(AmoModel):
    """A custom field definition (``/api/v4/{entity_type}/custom_fields``)."""

    id: int | None = None
    name: str | None = None
    type: str | None = None
    code: str | None = None
    sort: int | None = None
    is_api_only: bool | None = None
    is_predefined: bool | None = None
    is_deletable: bool | None = None
    entity_type: str | None = None
    group_id: str | None = None
    enums: list[CustomFieldEnum] | None = None
    account_id: int | None = None
    links: dict[str, Any] | None = _links_field()

    def enum_id(self, value: str) -> int | None:
        """Return the id of the option with this value (``"WORK"``, ``"Сайт"``)."""
        for option in self.enums or []:
            if option.value == value:
                return option.id
        return None


class User(AmoModel):
    """A user of the account (``/api/v4/users``)."""

    id: int | None = None
    name: str | None = None
    email: str | None = None
    lang: str | None = None
    rights: dict[str, Any] | None = None
    links: dict[str, Any] | None = _links_field()


class Account(AmoModel):
    """The account (``/api/v4/account``)."""

    id: int | None = None
    name: str | None = None
    subdomain: str | None = None
    created_at: int | None = None
    created_by: int | None = None
    updated_at: int | None = None
    updated_by: int | None = None
    current_user_id: int | None = None
    country: str | None = None
    currency: str | None = None
    currency_symbol: str | None = None
    customers_mode: str | None = None
    is_unsorted_on: bool | None = None
    is_loss_reason_enabled: bool | None = None
    is_helpbot_enabled: bool | None = None
    is_technical_account: bool | None = None
    contact_name_display_order: int | None = None
    amojo_id: str | None = None
    uuid: str | None = None
    version: int | None = None
    links: dict[str, Any] | None = _links_field()
    embedded: dict[str, Any] | list[Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("_embedded", "embedded"),
        serialization_alias="_embedded",
    )


class EntityRef(AmoModel):
    """What a create or update answers for each item: its id and ``request_id``."""

    id: int | None = None
    request_id: str | None = None
    updated_at: int | None = None
    entity_id: int | None = None
    links: dict[str, Any] | None = _links_field()


class ComplexLeadResult(AmoModel):
    """What ``POST /api/v4/leads/complex`` answers for each lead."""

    id: int | None = None
    contact_id: int | None = None
    company_id: int | None = None
    request_id: list[str] = Field(default_factory=list)
    merged: bool | None = None


class Link(AmoModel):
    """A link between two entities (``/api/v4/{entity_type}/{id}/link``)."""

    entity_id: int | None = None
    entity_type: str | None = None
    to_entity_id: int | None = None
    to_entity_type: str | None = None
    metadata: dict[str, Any] | None = None


for _model in (Lead, Contact, Company, Embedded):
    _model.model_rebuild()
