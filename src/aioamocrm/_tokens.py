"""OAuth2 token sets and where they are kept between runs.

amoCRM rotates the refresh token on every refresh: the answer to a refresh carries a new
refresh token and the old one stops working (amoCRM v4 documentation; not checked against a
live account). Losing the new one means asking the account owner to install the integration
again, so a :class:`TokenStorage` must save it durably before it is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._errors import AuthError

__all__ = ["JsonFileTokenStorage", "MemoryTokenStorage", "TokenSet", "TokenStorage"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenSet:
    """An OAuth2 access token with the refresh token that renews it.

    The tokens are left out of ``repr()`` so that logging a :class:`TokenSet` does not leak them.

    Attributes:
        access_token: Bearer token sent with API requests.
        refresh_token: Single-use token that buys the next :class:`TokenSet`.
        expires_at: Unix time at which the access token expires.
        token_type: Always ``"Bearer"`` for amoCRM.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    token_type: str = "Bearer"  # noqa: S105 - a token type, not a secret

    def expires_within(self, seconds: float, *, now: float) -> bool:
        """Tell whether the access token expires less than ``seconds`` after ``now``."""
        return now >= self.expires_at - seconds

    @classmethod
    def from_response(cls, data: Mapping[str, Any], *, now: float) -> TokenSet:
        """Build a token set from an ``/oauth2/access_token`` answer.

        Args:
            data: The decoded answer: ``token_type``, ``expires_in`` (seconds; 86400 for
                amoCRM), ``access_token`` and ``refresh_token``.
            now: Unix time at which the answer was received.

        Raises:
            AuthError: A token or ``expires_in`` is missing or malformed.
        """
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            msg = "the token endpoint answer has no access_token or refresh_token"
            raise AuthError(msg)
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in)
            or expires_in < 0
        ):
            # NaN or infinity (json.loads accepts both) would make the token never expire
            # in our eyes, so it would only be renewed after a 401.
            msg = "the token endpoint answer has no usable expires_in (seconds, finite, >= 0)"
            raise AuthError(msg)
        token_type = data.get("token_type")
        return cls(
            access_token=access,
            refresh_token=refresh,
            expires_at=now + float(expires_in),
            token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict **including the raw tokens**, for storages only."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenSet:
        """Rebuild a token set saved with :meth:`to_dict`.

        Raises:
            ValueError: A field is missing or has the wrong type.
        """
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_at = data.get("expires_at")
        token_type = data.get("token_type", "Bearer")
        if not isinstance(access, str) or not isinstance(refresh, str):
            msg = "access_token and refresh_token must be strings"
            raise ValueError(msg)
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
        ):
            msg = "expires_at must be a finite number"
            raise ValueError(msg)
        if not isinstance(token_type, str):
            msg = "token_type must be a string"
            raise ValueError(msg)
        return cls(
            access_token=access,
            refresh_token=refresh,
            expires_at=float(expires_at),
            token_type=token_type,
        )


@runtime_checkable
class TokenStorage(Protocol):
    """Where :class:`~aioamocrm.OAuth2` keeps the current token set.

    ``save`` is called with every new token set before it is used, so that a rotated refresh
    token survives a crash or a restart. Implementations backed by shared storage (a database,
    Redis) let several processes use one integration; they should make ``save`` atomic.
    """

    async def load(self) -> TokenSet | None:
        """Return the saved token set, or ``None`` when there is none yet."""
        ...

    async def save(self, tokens: TokenSet) -> None:
        """Persist ``tokens``, replacing what was saved before."""
        ...


class MemoryTokenStorage:
    """Keeps the token set in memory: for tests and short-lived scripts.

    Args:
        tokens: Token set to start with.
    """

    def __init__(self, tokens: TokenSet | None = None) -> None:
        self._tokens = tokens

    async def load(self) -> TokenSet | None:
        """Return the last saved token set."""
        return self._tokens

    async def save(self, tokens: TokenSet) -> None:
        """Remember ``tokens``."""
        self._tokens = tokens


class JsonFileTokenStorage:
    """Keeps the token set in a JSON file, replaced atomically on every save.

    A save writes a temporary file in the same directory, flushes it to disk and renames it over
    the target, so a reader (or a crash) never sees a half-written file: the file holds either
    the previous token set or the new one. The file is created with mode ``0600``.

    Args:
        path: File to use; missing parent directories are created on the first save.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"JsonFileTokenStorage({str(self.path)!r})"

    async def load(self) -> TokenSet | None:
        """Read the token set, or return ``None`` when the file does not exist.

        Raises:
            AuthError: The file exists but does not hold a valid token set.
        """
        return await asyncio.to_thread(self._load)

    async def save(self, tokens: TokenSet) -> None:
        """Write ``tokens`` atomically."""
        await asyncio.to_thread(self._save, tokens)

    def _load(self) -> TokenSet | None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                msg = "expected a JSON object"
                raise TypeError(msg)
            return TokenSet.from_dict(data)
        except (ValueError, TypeError) as exc:
            msg = f"cannot read the token set from {self.path}: {exc}"
            raise AuthError(msg) from exc

    def _save(self, tokens: TokenSet) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file with mode 0600, so the tokens are never readable by others.
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(tokens.to_dict(), file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        self._sync_directory(directory)
        logger.debug("token set saved to %s", self.path)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        """Flush the rename itself to disk where the platform allows it (POSIX)."""
        if os.name != "posix":  # pragma: no cover - Windows cannot open a directory
            return
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:  # pragma: no cover - unusual filesystems
            return
        try:
            os.fsync(dir_fd)
        except OSError:  # pragma: no cover - some filesystems refuse fsync on directories
            pass
        finally:
            os.close(dir_fd)
