"""Exceptions raised by the client, and the mapping from amoCRM error answers to them.

amoCRM v4 answers errors with ``application/problem+json`` bodies such as
``{"title": "Bad Request", "type": "https://httpstatus.es/400", "status": 400, "detail": "..."}``.
A 400 on a write also carries ``validation-errors``: one entry per rejected item of the batch,
keyed by its ``request_id`` (the item's position in the request unless the caller set one), each
with a list of ``{"code", "path", "detail"}``. Both shapes come from the amoCRM v4 documentation;
they were not checked against a live account, so every field is read defensively.
"""

from __future__ import annotations

import email.utils
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timezone
from typing import Any

import httpx

__all__ = [
    "AccountMovedError",
    "AmoCRMError",
    "AuthError",
    "ForbiddenError",
    "NetworkError",
    "NotFoundError",
    "PaymentRequiredError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
    "ValidationIssue",
    "error_from_response",
    "parse_retry_after",
]

_MAX_TEXT = 300
_ASCTIME_DATE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?:[0-9]{2}| [0-9]) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}"
)


class AmoCRMError(Exception):
    """Base class for every error raised by aioamocrm.

    Attributes:
        status_code: HTTP status of the answer, or ``None`` when there was no answer.
        title: ``title`` of the problem body, when amoCRM sent one.
        detail: ``detail`` of the problem body, when amoCRM sent one.
        body: The decoded JSON body, or the text body, or ``None``.
        method: HTTP method of the failed request.
        url: URL of the failed request, without its query string.
        batch_offset: For a batched write, the index in the caller's input of the first item of
            the chunk that failed; ``None`` otherwise.
        completed: For a batched write, the results of the chunks that were saved before the
            failing one; empty otherwise.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        title: str | None = None,
        detail: str | None = None,
        body: Any = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.body = body
        self.method = method
        self.url = url
        self.batch_offset: int | None = None
        self.completed: list[Any] = []


class AuthError(AmoCRMError):
    """The credentials were rejected (HTTP 401) or a token could not be obtained or refreshed."""


class AccountMovedError(AuthError):
    """A refresh was refused because the account now lives at another domain.

    Raised when the token endpoint answers a refresh with 401 or 404 and amoCRM, asked where
    the account is now (``GET /oauth2/account/current/subdomain``), names another host. The
    refresh token was not spent: create the client for :attr:`domain` with the same
    :class:`~aioamocrm.OAuth2` and storage, and the next request refreshes there.

    Attributes:
        domain: The account's current host, such as ``newname.amocrm.ru``.
    """

    def __init__(self, message: str, *, domain: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.domain = domain


class PaymentRequiredError(AmoCRMError):
    """HTTP 402: the amoCRM account is not paid, so the API refuses to serve it."""


class ForbiddenError(AmoCRMError):
    """HTTP 403: no rights for this action, a blocked account, or an IP blocked for flooding."""


class NotFoundError(AmoCRMError):
    """HTTP 404, or an entity asked for by id that amoCRM answered with no content."""


class RateLimitError(AmoCRMError):
    """HTTP 429 on a write made without ``retry=True``, or still there after the retries ran out.

    Attributes:
        retry_after: Seconds from the ``Retry-After`` header, when amoCRM sent one.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(AmoCRMError):
    """HTTP 5xx from amoCRM that was still there after the retries ran out."""


class NetworkError(AmoCRMError):
    """The request did not get an HTTP answer (connection failure, timeout, broken stream)."""


@dataclass(frozen=True)
class ValidationIssue:
    """One problem amoCRM found in one item of a write request.

    Attributes:
        request_id: ``request_id`` of the item: its index within the request unless the caller
            sent its own ``request_id``.
        code: Machine-readable code, such as ``NotSupportedChoice``.
        path: Path to the offending field, such as ``custom_fields_values.0.field_id``.
        detail: Human-readable explanation.
    """

    request_id: str | None
    code: str | None
    path: str | None
    detail: str | None


class ValidationError(AmoCRMError):
    """HTTP 400 (or 422): the request was rejected, usually with per-item validation errors.

    Attributes:
        errors: Every validation issue amoCRM listed, in order.
    """

    def __init__(
        self, message: str, *, errors: list[ValidationIssue] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)
        self.errors: list[ValidationIssue] = list(errors or [])


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Read a ``Retry-After`` header given in seconds or as an HTTP date.

    Args:
        value: The header value, or ``None``.
        now: Current Unix time, for HTTP dates; defaults to :func:`time.time`.

    Returns:
        Seconds to wait (never negative), or ``None`` when the header is missing or unreadable.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if moment.tzinfo is None:
        # RFC 9110 section 5.6.7 gives asctime dates an implicit UTC zone.
        # Other zone-less email dates (including -0000) remain unreadable.
        if not _ASCTIME_DATE.fullmatch(text):
            return None
        moment = moment.replace(tzinfo=timezone.utc)
    current = time.time() if now is None else now
    return max(0.0, moment.timestamp() - current)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _issues(body: Any) -> list[ValidationIssue]:
    if not isinstance(body, Mapping):
        return []
    entries = body.get("validation-errors")
    if not isinstance(entries, list):
        return []
    issues: list[ValidationIssue] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        errors = entry.get("errors")
        if not isinstance(errors, list):
            continue
        request_id = _text(entry.get("request_id"))
        issues.extend(
            ValidationIssue(
                request_id=request_id,
                code=_text(error.get("code")),
                path=_text(error.get("path")),
                detail=_text(error.get("detail")),
            )
            for error in errors
            if isinstance(error, Mapping)
        )
    return issues


def _decode_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _short(text: str) -> str:
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "..."


_BY_STATUS: dict[int, type[AmoCRMError]] = {
    400: ValidationError,
    401: AuthError,
    402: PaymentRequiredError,
    403: ForbiddenError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitError,
}


_REDIRECT_FIRST = 300
_REDIRECT_END = 400


def _redirect_note(url: httpx.URL, location: str | None) -> str:
    hint = "check the subdomain, domain or base_url"
    if location:
        try:
            target = url.join(location).copy_with(query=None, fragment=None)
        except (httpx.InvalidURL, ValueError):
            pass
        else:
            return f": redirected to {target}; {hint}"
    return f": redirected; {hint}"


def error_from_response(response: httpx.Response) -> AmoCRMError:
    """Build the typed exception for an error answer.

    A 3xx is an error too, raised as :class:`AmoCRMError` naming where it pointed, without the
    query. The client never follows redirects: httpx would replay a write where the redirect
    points, as a ``GET`` without its body after a 302 or 303 (or a 301 to a ``POST``), and with
    its body after a 307 or 308 (or a 301 to a ``PATCH``); for the token endpoint the body holds
    the client secret and the refresh token. The token is not the reason: httpx drops it on a
    redirect to another host.

    Args:
        response: An answer with a 3xx, 4xx or 5xx status; its request must be set.

    Returns:
        The exception to raise. Its message names the method, the path (never the query string,
        which may hold search terms) and whatever ``title`` / ``detail`` amoCRM sent, with
        ``hint`` in brackets after the detail when it adds something.
    """
    request = response.request
    url = request.url.copy_with(query=None, fragment=None)
    body = _decode_body(response)
    title = detail = hint = None
    if isinstance(body, Mapping):
        title = _text(body.get("title"))
        detail = _text(body.get("detail") or body.get("hint"))
        hint = _text(body.get("hint"))

    status = response.status_code
    parts = [f"HTTP {status}"]
    if title:
        parts.append(f" {title}")
    if detail:
        parts.append(f": {detail}")
        if hint and hint != detail:
            # The token endpoint puts the reason there ("Token has been revoked").
            parts.append(f" ({hint})")
    elif isinstance(body, str) and body.strip():
        parts.append(f": {_short(body.strip())}")
    parts.append(f" ({request.method} {url.path})")
    if _REDIRECT_FIRST <= status < _REDIRECT_END:
        parts.append(_redirect_note(request.url, response.headers.get("Location")))
    message = "".join(parts)

    common: dict[str, Any] = {
        "status_code": status,
        "title": title,
        "detail": detail,
        "body": body,
        "method": request.method,
        "url": str(url),
    }
    kind = _BY_STATUS.get(status)
    if kind is None and status >= 500:  # noqa: PLR2004
        kind = ServerError
    if kind is ValidationError:
        issues = _issues(body)
        if issues:
            listed = "; ".join(
                f"[{issue.request_id}] {issue.path}: {issue.detail or issue.code}"
                for issue in issues[:10]
            )
            message = f"{message}: {_short(listed)}"
        return ValidationError(message, errors=issues, **common)
    if kind is RateLimitError:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        return RateLimitError(message, retry_after=retry_after, **common)
    return (kind or AmoCRMError)(message, **common)
