"""Client-side request spacing.

The amoCRM v4 documentation limits an integration to 7 requests per second (and an account to
50 per second across integrations); going over it is answered with HTTP 429, and repeated
abuse with an IP block (HTTP 403). The default of 7 comes from that documentation and was not
measured against a live account.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

__all__ = ["RateLimiter"]


class RateLimiter:
    """Spaces calls at least ``1 / rate`` seconds apart.

    Each caller reserves the next free slot and sleeps until it, so concurrent callers are
    served in arrival order and no more than ``rate`` calls start in any one-second window.
    Idle time does not build up credit for a later burst. A cancelled caller leaves its slot
    unused, which only ever makes the spacing more conservative.

    One limiter can be shared by several clients that must share one budget.

    Args:
        rate: Calls per second.
        clock: Monotonic clock in seconds; replaceable in tests.
        sleep: Coroutine function that sleeps for the given seconds; replaceable in tests.
    """

    def __init__(
        self,
        rate: float = 7.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not math.isfinite(rate) or rate <= 0:
            msg = "rate must be a positive number of calls per second"
            raise ValueError(msg)
        self._rate = float(rate)
        self._interval = 1.0 / self._rate
        self._clock = clock
        self._sleep = sleep
        self._next_slot = -math.inf

    @property
    def rate(self) -> float:
        """Calls per second."""
        return self._rate

    def __repr__(self) -> str:
        return f"RateLimiter({self._rate:g})"

    async def acquire(self) -> None:
        """Wait for the next free slot."""
        # No await between reading and moving the next slot: the reservation is atomic
        # within the event loop, so no lock is needed.
        now = self._clock()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            await self._sleep(delay)
