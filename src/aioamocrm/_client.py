"""The HTTP core: addresses, authorization, rate limiting, retries and error mapping."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

import httpx

from ._auth import Auth, OAuth2, TokenEndpoint
from ._domains import DOMAINS
from ._errors import AmoCRMError, NetworkError, error_from_response, parse_retry_after
from ._params import encode_mapping
from ._ratelimit import RateLimiter
from ._resources import (
    AccountResource,
    CompaniesResource,
    ContactsResource,
    LeadsResource,
    PipelinesResource,
    TasksResource,
    UsersResource,
)
from ._retry import RetryPolicy
from ._tokens import TokenSet
from ._version import __version__

__all__ = ["DOMAINS", "AmoCRMClient"]

logger = logging.getLogger(__name__)

_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TOO_MANY_REQUESTS = 429
_UNAUTHORIZED = 401
_ACCEPTED = 202
_SUCCESS_FIRST = 200
_SUCCESS_END = 300

# Failures that happen before a single byte of the request is sent: safe to retry for any method.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Indirection so tests can make backoff instant without touching asyncio itself.
_sleep = asyncio.sleep

Params = Mapping[str, Any] | Sequence[tuple[str, str]]


def _origin(base_url: str | None, subdomain: str | None, domain: str) -> str:
    if (base_url is None) == (subdomain is None):
        msg = "pass either base_url or subdomain"
        raise ValueError(msg)
    if subdomain is not None:
        name = subdomain.strip().lower()
        if not _SUBDOMAIN.fullmatch(name):
            msg = f"invalid amoCRM subdomain: {subdomain!r}"
            raise ValueError(msg)
        if domain not in DOMAINS:
            msg = f"domain must be one of {', '.join(DOMAINS)}; use base_url for anything else"
            raise ValueError(msg)
        return f"https://{name}.{domain}"
    assert base_url is not None  # noqa: S101 - narrowed by the check above
    url = httpx.URL(base_url.strip())
    if url.scheme not in ("http", "https") or not url.host:
        msg = f"base_url must be an absolute http(s) URL: {base_url!r}"
        raise ValueError(msg)
    if url.query or url.fragment:
        msg = "base_url must not have a query string or a fragment"
        raise ValueError(msg)
    return str(url).rstrip("/")


class AmoCRMClient:
    """Async client for one amoCRM / Kommo account.

    Use it as an async context manager, or call :meth:`aclose` when done::

        async with AmoCRMClient(subdomain="example", auth=LongLivedToken(token)) as amo:
            async for lead in amo.leads.iterate(with_=["contacts"]):
                ...

    Args:
        auth: :class:`~aioamocrm.LongLivedToken`, :class:`~aioamocrm.OAuth2` or any other
            :class:`~aioamocrm.Auth`.
        subdomain: Account subdomain, such as ``example`` for ``example.amocrm.ru``.
        domain: ``amocrm.ru`` (default), ``amocrm.com`` or ``kommo.com``.
        base_url: Full account address instead of ``subdomain`` and ``domain``, for a proxy
            or a test server, such as ``https://example.amocrm.ru``.
        http: An ``httpx.AsyncClient`` to use; the client then leaves closing it to you.
            Otherwise the client creates its own and closes it in :meth:`aclose`.
        timeout: Timeout in seconds for the client's own ``httpx.AsyncClient``.
        rate_limit: Requests per second (default 7, the documented per-integration limit), a
            :class:`~aioamocrm.RateLimiter` to share between clients, or ``None`` for no limit.
        retry: Retry settings; defaults to :class:`~aioamocrm.RetryPolicy` ``()``.
    """

    def __init__(
        self,
        *,
        auth: Auth,
        subdomain: str | None = None,
        domain: str = "amocrm.ru",
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        rate_limit: float | RateLimiter | None = 7.0,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.base_url = _origin(base_url, subdomain, domain)
        self.api_url = f"{self.base_url}/api/v4/"
        self.token_url = f"{self.base_url}/oauth2/access_token"
        self.auth = auth
        self._owns_http = http is None
        self.http = http if http is not None else httpx.AsyncClient(timeout=timeout)
        if isinstance(rate_limit, RateLimiter) or rate_limit is None:
            self.limiter = rate_limit
        else:
            self.limiter = RateLimiter(rate_limit)
        self.retry = retry if retry is not None else RetryPolicy()
        self._endpoint = TokenEndpoint(http=self.http, url=self.token_url)
        self._random = random.Random()  # noqa: S311 - jitter, not cryptography
        self._api = httpx.URL(self.api_url)

        #: Leads, with their notes, custom fields and ``create_complex``.
        self.leads = LeadsResource(self)
        #: Contacts, with their notes and custom fields.
        self.contacts = ContactsResource(self)
        #: Companies, with their notes and custom fields.
        self.companies = CompaniesResource(self)
        #: Tasks.
        self.tasks = TasksResource(self)
        #: Lead pipelines and their statuses.
        self.pipelines = PipelinesResource(self)
        #: Users of the account.
        self.users = UsersResource(self)
        #: The account itself.
        self.account = AccountResource(self)

    def __repr__(self) -> str:
        return f"AmoCRMClient(base_url={self.base_url!r})"

    async def __aenter__(self) -> AmoCRMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this client created it."""
        if self._owns_http and not self.http.is_closed:
            await self.http.aclose()

    async def exchange_code(self, code: str) -> TokenSet:
        """Trade an OAuth2 authorization code for tokens and save them in the storage.

        Raises:
            TypeError: The client was not created with :class:`~aioamocrm.OAuth2`.
            AuthError: amoCRM refused the code.
        """
        if not isinstance(self.auth, OAuth2):
            msg = "exchange_code() needs an OAuth2 auth"
            raise TypeError(msg)
        return await self.auth.exchange_code(self._endpoint, code)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: Any = None,
        retry: bool | None = None,
    ) -> Any:
        """Send one API request and decode the answer.

        Args:
            method: HTTP method.
            path: Path under ``/api/v4/`` (``"leads"``, ``"leads/1/notes"``), or an absolute URL
                under the same ``/api/v4/`` (as found in ``_links``).
            params: Query parameters; a mapping is flattened into PHP brackets
                (``{"filter": {"id": [1]}}`` gives ``filter[id][0]=1``).
            json: JSON body.
            retry: ``True`` to retry 429 and 5xx answers and broken connections whatever the
                method, ``False`` to never retry them, ``None`` (the default) to retry them for
                idempotent methods only. Failures to connect, where nothing was sent, are
                retried in every case.

        Returns:
            The decoded JSON, or ``None`` for an empty answer (amoCRM answers ``204 No Content``
            for a list with nothing in it) and for a ``202 Accepted`` whose body is not JSON.

        Raises:
            AmoCRMError: A subclass matching the failure; see :mod:`aioamocrm`.
            ValueError: ``path`` is an absolute URL outside this account's API.
        """
        response = await self._send(method.upper(), self._url(path), params, json, retry)
        if response.status_code == 204 or not response.content:  # noqa: PLR2004
            return None
        try:
            return response.json()
        except ValueError:
            if response.status_code == _ACCEPTED:
                # Taken for later processing. The official PHP SDK treats a 202 as a success
                # whatever its body; raising would report a failure for a request amoCRM took.
                return None
            where = f"{method.upper()} {response.request.url.path}"
            msg = f"{where} answered with a body that is not valid JSON"
            raise AmoCRMError(msg, status_code=response.status_code) from None

    def owns_url(self, url: str) -> bool:
        """Tell whether ``url`` is an absolute URL under this account's ``/api/v4/``."""
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            return False
        api = self._api
        return (
            parsed.scheme == api.scheme
            and parsed.host == api.host
            and parsed.port == api.port
            and parsed.path.startswith(api.path)
        )

    def _url(self, path: str) -> str:
        if "://" not in path:
            return self.api_url + path.lstrip("/")
        if not self.owns_url(path):
            msg = f"refusing to send the account's token outside {self.api_url}"
            raise ValueError(msg)
        return path

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"aioamocrm/{__version__}",
        }

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float | None:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if retry_after is None:
            return self.retry.backoff(attempt, self._random)
        if retry_after > self.retry.max_retry_after:
            return None
        return retry_after

    async def _send(
        self,
        method: str,
        url: str,
        params: Params | None,
        json: Any,
        retry: bool | None,
    ) -> httpx.Response:
        policy = self.retry
        unsafe_ok = retry if retry is not None else method in policy.idempotent_methods
        target = httpx.URL(url)
        # Merged here: httpx would replace a query already in the URL (a _links href) instead.
        if isinstance(params, Mapping):
            target = target.copy_merge_params(httpx.QueryParams(tuple(encode_mapping(params))))
        elif params is not None:
            target = target.copy_merge_params(httpx.QueryParams(tuple(params)))
        path = target.path  # logged instead of the URL: the query may hold search terms
        attempt = 1
        reauthorized = False
        while True:
            if self.limiter is not None:
                await self.limiter.acquire()
            token = await self.auth.get_token(self._endpoint)
            try:
                # Never followed, also with an http= client built with follow_redirects=True:
                # httpx would replay a write at the Location, as a GET without its body after a
                # 302 or 303 (or a 301 to a POST), and with its body after a 307 or 308 (or a
                # 301 to a PATCH).
                response = await self.http.request(
                    method,
                    target,
                    json=json,
                    headers=self._headers(token),
                    follow_redirects=False,
                )
            except httpx.RequestError as exc:
                retryable = isinstance(exc, httpx.TransportError) and (
                    unsafe_ok or isinstance(exc, _NOT_SENT)
                )
                if retryable and attempt < policy.max_attempts:
                    delay = policy.backoff(attempt, self._random)
                    logger.warning(
                        "%s %s failed with %s; attempt %d of %d in %.2f s",
                        method,
                        path,
                        type(exc).__name__,
                        attempt + 1,
                        policy.max_attempts,
                        delay,
                    )
                    await _sleep(delay)
                    attempt += 1
                    continue
                msg = f"{method} {path} failed: {type(exc).__name__}: {exc}"
                where = str(target.copy_with(query=None))
                raise NetworkError(msg, method=method, url=where) from exc

            status = response.status_code
            logger.debug("%s %s -> %d", method, path, status)
            if (
                status == _UNAUTHORIZED
                and not reauthorized
                and await self.auth.handle_unauthorized(self._endpoint, token)
            ):
                reauthorized = True
                logger.info("%s %s got 401; trying once more with a renewed token", method, path)
                continue
            retryable = unsafe_ok and (
                status == _TOO_MANY_REQUESTS or status in policy.retry_statuses
            )
            if retryable and attempt < policy.max_attempts:
                wait = self._retry_delay(response, attempt)
                if wait is not None:
                    logger.warning(
                        "%s %s -> %d; attempt %d of %d in %.2f s",
                        method,
                        path,
                        status,
                        attempt + 1,
                        policy.max_attempts,
                        wait,
                    )
                    await _sleep(wait)
                    attempt += 1
                    continue
            # Anything but 2xx is an error. A redirect (a wrong subdomain or domain, http://
            # instead of https://) usually has an empty body that would otherwise read as
            # "204, nothing there".
            if not _SUCCESS_FIRST <= status < _SUCCESS_END:
                raise error_from_response(response)
            return response
