"""When and how long the client waits before trying a request again."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

__all__ = ["RetryPolicy"]


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings for one client.

    What is retried:

    * Failures to connect, for every method: the request was never sent.
    * HTTP 429, HTTP 5xx in ``retry_statuses``, and broken connections or timeouts after the
      request was sent, only for ``idempotent_methods`` (by default ``GET``, ``HEAD``,
      ``OPTIONS``), or for any request made with ``retry=True``. amoCRM has no idempotency
      keys: a ``POST`` that timed out may have created the lead, and sending it again would
      create a second one. A 429 most likely means the write was refused before anything
      happened, but the documentation does not promise it, so writes are not resent on a 429
      either unless the caller opts in.

    The delay is ``Retry-After`` when the answer has one, otherwise exponential backoff with
    jitter: attempt *n* waits a random time between half and all of
    ``min(backoff_max, backoff_base * 2 ** (n - 1))``.

    Attributes:
        max_attempts: Tries in total, the first one included; ``1`` turns retries off.
        backoff_base: Upper bound of the first delay, in seconds.
        backoff_max: Cap on the backoff delay, in seconds.
        max_retry_after: Longest ``Retry-After`` the client is willing to wait; a longer one
            is raised at once as :class:`~aioamocrm.RateLimitError` (or the error for its status).
        retry_statuses: HTTP statuses retried for idempotent requests, besides 429.
        idempotent_methods: Methods whose 429, 5xx and timeouts are retried without
            ``retry=True``.
    """

    max_attempts: int = 4
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    max_retry_after: float = 60.0
    retry_statuses: frozenset[int] = field(default=frozenset({500, 502, 503, 504}))
    idempotent_methods: frozenset[str] = field(default=frozenset({"GET", "HEAD", "OPTIONS"}))

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        for name in ("backoff_base", "backoff_max", "max_retry_after"):
            if getattr(self, name) < 0:
                msg = f"{name} must not be negative"
                raise ValueError(msg)

    def backoff(self, attempt: int, rng: random.Random) -> float:
        """Return the delay after failed attempt number ``attempt`` (1-based)."""
        ceiling = min(self.backoff_max, self.backoff_base * 2 ** max(0, attempt - 1))
        return rng.uniform(ceiling / 2, ceiling)
