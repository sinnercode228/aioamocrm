"""Turn a Tilda form submission into an amoCRM lead with its contact and a note.

Tilda posts each submission as ``application/x-www-form-urlencoded``, with the fields named as
in the form editor (``Name``, ``Phone``, ``Email``, ``Comment``...) plus ``formid``,
``formname``, ``tranid`` and, when the site collects them, ``utm_*`` fields and ``COOKIES``.
The API key set in the Tilda webhook settings comes as the ``api_key`` field, and when the
webhook is connected Tilda sends ``test=test`` once and expects the answer ``ok``.

For each submission :func:`handle_form`:

* checks the API key in constant time (an empty key accepts nothing);
* normalizes the phone (``8 900 123-45-67`` and ``9001234567`` become ``+79001234567``);
* looks for a contact with that phone, or with that email when there is no phone, through the
  full-text search (``GET /api/v4/contacts?query=...``) and keeps only an exact match, since
  the search also returns partial ones;
* creates the lead and links the contact found as its main one, or creates the lead and a new
  contact together through ``/api/v4/leads/complex`` (a form without a name, phone or email
  gets a lead without a contact);
* adds a note with the comment, the form and the UTM marks.

Once the lead exists, a failed link or note is returned in ``problems`` and not raised: the
caller must not retry, because a second run would create a second lead. Writes are not resent
by the client either (see the README section "What is retried").

Run it on a body saved from a Tilda request::

    AMO_SUBDOMAIN=example AMO_LONG_LIVED_TOKEN=... TILDA_API_KEY=... \\
        python examples/tilda_lead.py < submission.txt

In a web app, answer Tilda first and call :func:`handle_form` with the raw body in the
background, as ``webhook_fastapi.py`` does for amoCRM webhooks.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from aioamocrm import AmoCRMClient, AmoCRMError, Contact, Embedded, Lead, LongLivedToken, Note, Tag

NAME_KEYS = ("name", "имя", "fio", "фио", "your_name")
PHONE_KEYS = ("phone", "телефон", "tel", "phone_number")
EMAIL_KEYS = ("email", "e-mail", "почта", "mail")
COMMENT_KEYS = ("comment", "комментарий", "message", "сообщение")
DEFAULT_LEAD_NAME = "Заявка с сайта"
TAGS = ("tilda",)


@dataclass(frozen=True)
class Submission:
    """The fields of one form submission that the lead needs."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    comment: str | None = None
    form_id: str | None = None
    form_name: str | None = None
    tranid: str | None = None
    utm: dict[str, str] = field(default_factory=dict)


@dataclass
class CreatedLead:
    """What :func:`create_lead` made, and what failed after the lead was saved."""

    lead_id: int
    contact_id: int | None
    new_contact: bool
    problems: list[str] = field(default_factory=list)


def parse_form(body: bytes | str) -> dict[str, str]:
    """Decode a Tilda body; keys are lower-cased, since Tilda keeps the editor's spelling."""
    text = body.decode() if isinstance(body, bytes) else body
    return {key.strip().lower(): value for key, value in parse_qsl(text, keep_blank_values=True)}


def normalize_phone(value: str | None) -> str | None:
    """Bring a phone to ``+<digits>``; Russian local forms become ``+7...``."""
    text = (value or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return text or None


def _pick(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(key, "").strip()
        if value:
            return value
    return None


def _utm(fields: dict[str, str]) -> dict[str, str]:
    marks: dict[str, str] = {}
    for chunk in fields.get("cookies", "").split(";"):
        key, _, value = chunk.strip().partition("=")
        if key.lower().startswith("utm_") and value:
            marks[key.lower()] = value
    # A utm_ field of the form wins over the cookie.
    marks.update({k: v for k, v in fields.items() if k.startswith("utm_") and v})
    return marks


def read_submission(fields: dict[str, str]) -> Submission:
    """Pick the contact, the comment, the form and the UTM marks out of the fields."""
    email = _pick(fields, EMAIL_KEYS)
    return Submission(
        name=_pick(fields, NAME_KEYS),
        phone=normalize_phone(_pick(fields, PHONE_KEYS)),
        email=email.lower() if email else None,
        comment=_pick(fields, COMMENT_KEYS),
        form_id=fields.get("formid") or None,
        form_name=fields.get("formname") or None,
        tranid=fields.get("tranid") or None,
        utm=_utm(fields),
    )


def _matches(contact: Contact, submission: Submission) -> bool:
    phones = {normalize_phone(str(v)) for v in contact.field_values("PHONE")}
    emails = {str(v).strip().lower() for v in contact.field_values("EMAIL")}
    return (submission.phone is not None and submission.phone in phones) or (
        submission.email is not None and submission.email in emails
    )


async def find_contact(amo: AmoCRMClient, submission: Submission) -> Contact | None:
    """Return the contact with the submission's phone (or email), or ``None``."""
    query = submission.phone or submission.email
    if query is None:
        return None
    page = await amo.contacts.get_page(query=query, limit=50)
    return next((c for c in page.items if c.id is not None and _matches(c, submission)), None)


def note_text(submission: Submission) -> str:
    """The comment, then the form and the UTM marks, one per line."""
    parts = [submission.comment] if submission.comment else []
    details: list[str] = []
    if submission.form_name or submission.form_id or submission.tranid:
        form = submission.form_name or "?"
        if submission.form_id:
            form += f" ({submission.form_id})"
        if submission.tranid:
            form += f", request {submission.tranid}"
        details.append(f"Form: {form}")
    details.extend(f"{key}: {value}" for key, value in sorted(submission.utm.items()))
    if details:
        parts.append("\n".join(details))
    return "\n\n".join(parts)


def _saved_id(value: int | None) -> int:
    if value is None:
        msg = "amoCRM answered without the id of the new lead"
        raise AmoCRMError(msg)
    return value


async def create_lead(amo: AmoCRMClient, submission: Submission) -> CreatedLead:
    """Create the lead for a submission, with an existing or a new contact, and the note."""
    lead = Lead(
        name=(submission.form_name or DEFAULT_LEAD_NAME)[:255],
        embedded=Embedded(tags=[Tag(name=tag) for tag in TAGS]),
    )
    existing = await find_contact(amo, submission)
    if existing is None and (submission.name or submission.phone or submission.email):
        contact = Contact(name=submission.name or submission.phone or submission.email)
        if submission.phone:
            contact.set_field("PHONE", submission.phone, enum_code="WORK")
        if submission.email:
            contact.set_field("EMAIL", submission.email, enum_code="WORK")
        lead.embedded.contacts.append(contact)
        [result] = await amo.leads.create_complex([lead])
        created = CreatedLead(_saved_id(result.id), result.contact_id, new_contact=True)
    else:
        [ref] = await amo.leads.create([lead])
        contact_id = existing.id if existing is not None else None
        created = CreatedLead(_saved_id(ref.id), contact_id, new_contact=False)
        if contact_id is not None:
            try:
                await amo.leads.link(
                    created.lead_id, "contacts", contact_id, metadata={"is_main": True}
                )
            except AmoCRMError as exc:
                created.problems.append(f"link: {exc}")

    text = note_text(submission)
    if text:
        try:
            await amo.leads.notes.create([Note.common(created.lead_id, text)])
        except AmoCRMError as exc:
            created.problems.append(f"note: {exc}")
    return created


async def handle_form(amo: AmoCRMClient, body: bytes | str, *, api_key: str) -> CreatedLead | None:
    """Check and handle one Tilda request; ``None`` for the test request (answer ``ok``).

    Raises:
        PermissionError: The API key is missing or wrong.
    """
    fields = parse_form(body)
    given = fields.pop("api_key", "")
    if not api_key or not hmac.compare_digest(given.encode(), api_key.encode()):
        msg = "wrong Tilda API key"
        raise PermissionError(msg)
    if fields.get("test") == "test":
        return None
    return await create_lead(amo, read_submission(fields))


async def main(body: bytes) -> None:
    async with AmoCRMClient(
        subdomain=os.environ["AMO_SUBDOMAIN"],
        domain=os.environ.get("AMO_DOMAIN", "amocrm.ru"),
        auth=LongLivedToken(os.environ["AMO_LONG_LIVED_TOKEN"]),
    ) as amo:
        created = await handle_form(amo, body, api_key=os.environ["TILDA_API_KEY"])
    if created is None:
        print("ok")
        return
    if created.contact_id is None:
        print(f"lead {created.lead_id}, no contact")
    else:
        contact = "new contact" if created.new_contact else "existing contact"
        print(f"lead {created.lead_id}, {contact} {created.contact_id}")
    for problem in created.problems:
        print(f"not saved: {problem}")


if __name__ == "__main__":
    asyncio.run(main(sys.stdin.buffer.read()))
