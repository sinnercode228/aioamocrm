"""Authorization: long-lived tokens and OAuth2 with refresh-token rotation.

The token endpoint, the request bodies and the answer come from the amoCRM v4 documentation
(``POST https://{subdomain}.amocrm.ru/oauth2/access_token`` with a JSON body; the answer holds
``token_type``, ``expires_in``, ``access_token`` and ``refresh_token``); they were not checked
against a live account here. Token requests are never retried: an authorization code works
once, and a refresh that reached amoCRM but whose answer was lost has already burnt the old
refresh token, so a blind retry cannot help and may hide what happened.

A refresh refused with 401 or 404 may mean that the account's domain changed. The official PHP
SDK (amocrm/amocrm-api-php) then asks ``GET https://{api_domain}/oauth2/account/current/subdomain``
with the refresh token in ``X-Refresh-Token``, ``api_domain`` being a claim of the access-token
JWT; this module asks the same and raises :class:`~aioamocrm.AccountMovedError` with the new host.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from ._domains import is_account_host
from ._errors import AccountMovedError, AuthError, NetworkError, error_from_response
from ._tokens import MemoryTokenStorage, TokenSet, TokenStorage

__all__ = ["Auth", "LongLivedToken", "OAuth2", "TokenEndpoint"]

logger = logging.getLogger(__name__)

_HTTP_OK = 200
# Refresh refusals after which the official PHP SDK looks the account's domain up.
_MOVED_STATUSES = frozenset({401, 404})
_ACCOUNT_DOMAIN_PATH = "/oauth2/account/current/subdomain"
_JWT_PARTS = 3


def _jwt_claim(token: str, name: str) -> str | None:
    """Read a text claim of a JWT without checking its signature; ``None`` when there is none."""
    parts = token.split(".")
    if len(parts) != _JWT_PARTS:
        return None
    payload = parts[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    value = claims.get(name) if isinstance(claims, dict) else None
    return value if isinstance(value, str) else None


async def _moved_to(endpoint: TokenEndpoint, tokens: TokenSet) -> str | None:
    """Ask amoCRM which host the account behind ``tokens`` lives on, if not the endpoint's.

    The refresh token goes only to an ``api_domain`` under amoCRM's or Kommo's domains, and
    only when the endpoint itself is an account host (not a proxy given as ``base_url``).
    Tokens issued before 21.08.2024 have no ``api_domain``, so there is nothing to ask.

    Returns:
        The account's current host, or ``None`` when it could not be learnt or did not change.
    """
    current = httpx.URL(endpoint.url).host
    api_domain = _jwt_claim(tokens.access_token, "api_domain")
    if not is_account_host(current) or api_domain is None or not is_account_host(api_domain):
        return None
    try:
        response = await endpoint.http.get(
            f"https://{api_domain}{_ACCOUNT_DOMAIN_PATH}",
            headers={"X-Refresh-Token": tokens.refresh_token, "Accept": "application/json"},
            follow_redirects=False,
        )
        data = response.json() if response.status_code == _HTTP_OK else None
    except (httpx.HTTPError, ValueError):
        return None
    domain = data.get("domain") if isinstance(data, Mapping) else None
    if not isinstance(domain, str):
        return None
    domain = domain.strip().lower()
    if not is_account_host(domain) or domain == current:
        return None
    return domain


def _account_moved(refusal: AuthError, domain: str) -> AccountMovedError:
    msg = (
        f"{refusal.message}; the account now lives at {domain}: create the client for that "
        "domain with the same tokens (this refresh token was not spent)"
    )
    return AccountMovedError(
        msg,
        domain=domain,
        status_code=refusal.status_code,
        title=refusal.title,
        detail=refusal.detail,
        body=refusal.body,
        method=refusal.method,
        url=refusal.url,
    )


def _newest(stored: TokenSet | None, current: TokenSet | None) -> TokenSet | None:
    """Pick the token set whose refresh token is the live one.

    Every refresh gives a later ``expires_at``, so the newer set holds the refresh token amoCRM
    still accepts. The storage is behind memory when the last save failed, and ahead of it when
    another process sharing the storage refreshed.
    """
    if stored is None or current is None:
        return stored if stored is not None else current
    return current if current.expires_at > stored.expires_at else stored


@dataclass(frozen=True)
class TokenEndpoint:
    """The OAuth2 token endpoint of one account and the HTTP client to reach it.

    :class:`~aioamocrm.AmoCRMClient` builds one for its account and hands it to the
    :class:`Auth` it was given.

    Attributes:
        http: Client used for token requests.
        url: Full URL of ``/oauth2/access_token`` on the account's domain.
    """

    http: httpx.AsyncClient
    url: str


@runtime_checkable
class Auth(Protocol):
    """How the client gets the bearer token for its requests."""

    async def get_token(self, endpoint: TokenEndpoint) -> str:
        """Return the access token to send now, renewing it first if needed."""
        ...

    async def handle_unauthorized(self, endpoint: TokenEndpoint, rejected_token: str) -> bool:
        """React to an HTTP 401 for ``rejected_token``.

        Returns:
            ``True`` when a different token is now available and the request is worth one
            more try, ``False`` when the 401 is final.
        """
        ...


class LongLivedToken:
    """A long-lived token created in the amoCRM integration settings.

    It is sent as ``Authorization: Bearer <token>`` and never refreshed: a 401 means the
    token was revoked or expired, and is raised as :class:`~aioamocrm.AuthError`.

    Args:
        token: The token text.
    """

    def __init__(self, token: str) -> None:
        if not token.strip():
            msg = "token must not be blank"
            raise ValueError(msg)
        self._token = token.strip()

    def __repr__(self) -> str:
        return "LongLivedToken(<hidden>)"

    async def get_token(self, endpoint: TokenEndpoint) -> str:
        """Return the token."""
        return self._token

    async def handle_unauthorized(
        self,
        endpoint: TokenEndpoint,
        rejected_token: str,
    ) -> bool:
        """A long-lived token cannot be renewed: the 401 is final."""
        return False


class OAuth2:
    """OAuth2 authorization code flow with automatic refresh.

    The access token is refreshed ``refresh_margin`` seconds before ``expires_at`` and once
    after a 401. A lock makes concurrent requests share one refresh, and the rotated refresh
    token is saved to ``storage`` as part of every refresh. Before refreshing, the storage is
    read again, so that tokens renewed meanwhile by another process sharing it are adopted
    instead of spending a refresh token that is no longer valid. When two processes refresh at
    the same moment, amoCRM accepts the refresh token once; the process that was refused reads
    the storage again and takes the winner's tokens if they are saved by then. To rule out
    that race entirely, give the processes a storage whose ``load`` and ``save`` run under a
    lock they share (a database row lock, for instance) or refresh from one process only.

    One instance belongs to one amoCRM account: give each account its own instance and
    storage.

    Args:
        client_id: Integration ID from the integration settings.
        client_secret: Secret key from the integration settings.
        redirect_uri: Redirect URI exactly as registered for the integration.
        storage: Where the tokens are kept; defaults to an empty :class:`MemoryTokenStorage`.
        refresh_margin: Seconds before expiry at which the access token is renewed.
        clock: Returns the current Unix time; replaceable in tests.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        storage: TokenStorage | None = None,
        *,
        refresh_margin: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for name, value in (
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("redirect_uri", redirect_uri),
        ):
            if not value.strip():
                msg = f"{name} must not be blank"
                raise ValueError(msg)
        if refresh_margin < 0:
            msg = "refresh_margin must not be negative"
            raise ValueError(msg)
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.storage: TokenStorage = storage if storage is not None else MemoryTokenStorage()
        self.refresh_margin = refresh_margin
        self._clock = clock
        self._tokens: TokenSet | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"OAuth2(client_id={self.client_id!r}, storage={self.storage!r})"

    async def exchange_code(self, endpoint: TokenEndpoint, code: str) -> TokenSet:
        """Trade an authorization code for the first token set and save it.

        amoCRM documents the code as valid for 20 minutes and usable once.

        Args:
            endpoint: Token endpoint of the account that issued the code (the ``referer``
                parameter of the redirect names that account's domain).
            code: The ``code`` parameter of the redirect.

        Raises:
            AuthError: amoCRM refused the code.
            NetworkError: The token endpoint could not be reached.
        """
        if not code.strip():
            msg = "code must not be blank"
            raise ValueError(msg)
        async with self._lock:
            tokens = await self._request(
                endpoint, {"grant_type": "authorization_code", "code": code.strip()}
            )
            await self._store(tokens)
        logger.info("OAuth2 code exchanged; access token valid until %.0f", tokens.expires_at)
        return tokens

    async def refresh(self, endpoint: TokenEndpoint) -> TokenSet:
        """Refresh now, whatever the expiry of the current access token.

        Raises:
            AuthError: There are no tokens yet, or amoCRM refused the refresh token.
            NetworkError: The token endpoint could not be reached.
        """
        current = await self._current()
        return await self._renew(endpoint, current.access_token, adopt_stored=False)

    async def get_token(self, endpoint: TokenEndpoint) -> str:
        """Return a usable access token, refreshing it when it is about to expire.

        Raises:
            AuthError: There are no tokens yet, or the refresh was refused.
            NetworkError: The token endpoint could not be reached.
        """
        tokens = await self._current()
        if tokens.expires_within(self.refresh_margin, now=self._clock()):
            tokens = await self._renew(endpoint, tokens.access_token)
        return tokens.access_token

    async def handle_unauthorized(self, endpoint: TokenEndpoint, rejected_token: str) -> bool:
        """Renew the tokens after ``rejected_token`` got a 401, unless that already happened.

        Raises:
            AuthError: The refresh was refused.
            NetworkError: The token endpoint could not be reached.
        """
        tokens = await self._renew(endpoint, rejected_token)
        return tokens.access_token != rejected_token

    async def _current(self) -> TokenSet:
        if self._tokens is None:
            async with self._lock:
                if self._tokens is None:
                    self._tokens = await self.storage.load()
        if self._tokens is None:
            msg = "no OAuth2 tokens yet: call exchange_code() with an authorization code first"
            raise AuthError(msg)
        return self._tokens

    async def _renew(
        self, endpoint: TokenEndpoint, stale_access_token: str, *, adopt_stored: bool = True
    ) -> TokenSet:
        async with self._lock:
            current = self._tokens
            if current is not None and current.access_token != stale_access_token:
                return current  # renewed by another request while this one waited for the lock
            stored = await self.storage.load()
            if (
                adopt_stored
                and stored is not None
                and stored.access_token != stale_access_token
                and (current is None or stored.expires_at > current.expires_at)
                and not stored.expires_within(self.refresh_margin, now=self._clock())
            ):
                logger.info("OAuth2 tokens renewed elsewhere were loaded from the storage")
                self._tokens = stored
                return stored
            base = _newest(stored, current)
            if base is None:
                msg = "no OAuth2 tokens yet: call exchange_code() with an authorization code first"
                raise AuthError(msg)
            try:
                tokens = await self._request(
                    endpoint, {"grant_type": "refresh_token", "refresh_token": base.refresh_token}
                )
            except AuthError as exc:
                winner = await self._renewed_elsewhere(base)
                if winner is not None:
                    logger.info("OAuth2 refresh refused; using the tokens another process saved")
                    self._tokens = winner
                    return winner
                if exc.status_code in _MOVED_STATUSES:
                    domain = await _moved_to(endpoint, base)
                    if domain is not None:
                        logger.warning("OAuth2 refresh refused: the account moved to %s", domain)
                        raise _account_moved(exc, domain) from exc
                raise
            await self._store(tokens)
        logger.info("OAuth2 tokens refreshed; access token valid until %.0f", tokens.expires_at)
        return tokens

    async def _renewed_elsewhere(self, spent: TokenSet) -> TokenSet | None:
        """After a refused refresh, return a newer set another process saved, if there is one.

        Two processes sharing the storage can read the same refresh token and refresh at the
        same moment. amoCRM accepts it once, so the other refresh is refused; by then the
        winner has usually saved its new set.
        """
        latest = await self.storage.load()
        if (
            latest is not None
            and latest.refresh_token != spent.refresh_token
            and latest.expires_at > spent.expires_at
            and not latest.expires_within(self.refresh_margin, now=self._clock())
        ):
            return latest
        return None

    async def _store(self, tokens: TokenSet) -> None:
        # Kept in memory first: the old refresh token is already dead, so even if the storage
        # fails this process can go on; the storage error is still raised to the caller.
        self._tokens = tokens
        await self.storage.save(tokens)

    async def _request(self, endpoint: TokenEndpoint, grant: Mapping[str, str]) -> TokenSet:
        body: dict[str, Any] = {
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            **grant,
            "redirect_uri": self.redirect_uri,
        }
        try:
            # A redirect is not followed whatever the client's settings: a 307 or 308 would
            # post the client secret and the refresh token wherever it points.
            response = await endpoint.http.post(
                endpoint.url,
                json=body,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.TransportError as exc:
            msg = f"the OAuth2 token endpoint could not be reached: {type(exc).__name__}"
            raise NetworkError(msg, method="POST", url=endpoint.url) from exc
        if response.status_code != _HTTP_OK:
            error = error_from_response(response)
            raise AuthError(
                f"OAuth2 token request failed: {error.message}",
                status_code=error.status_code,
                title=error.title,
                detail=error.detail,
                body=error.body,
                method=error.method,
                url=error.url,
            )
        try:
            data = response.json()
        except ValueError:
            msg = "the OAuth2 token endpoint answer is not JSON"
            raise AuthError(msg, status_code=response.status_code) from None
        if not isinstance(data, Mapping):
            msg = "the OAuth2 token endpoint answer is not a JSON object"
            raise AuthError(msg, status_code=response.status_code)
        return TokenSet.from_response(data, now=self._clock())
