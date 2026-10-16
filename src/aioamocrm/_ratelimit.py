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
import weakref
from collections.abc import Awaitable, Callable

__all__ = ["RateLimiter"]


class RateLimiter:
    """Spaces calls at least ``1 / rate`` seconds apart.

    Callers queue up in arrival order. Each one waits until ``1 / rate`` seconds have passed
    since the previous caller was actually let through, so the spacing holds even when the
    event loop was blocked and every waiter wakes up late: a stall delays the queue, it never
    releases a burst. Idle time does not build up credit for a later burst either, and a
    cancelled caller leaves the queue without using a slot.

    One limiter can be shared by several clients that must share one budget, within one event
    loop.

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
        self._last = -math.inf
        self._queue: asyncio.Lock | None = None
        self._queue_loop: weakref.ref[asyncio.AbstractEventLoop] | None = None

    @property
    def rate(self) -> float:
        """Calls per second."""
        return self._rate

    def __repr__(self) -> str:
        return f"RateLimiter({self._rate:g})"

    async def acquire(self) -> None:
        """Wait for the next free slot."""
        # The lock is a fair FIFO queue. Slots are not booked in advance: each caller measures
        # from the moment the previous one really went, after any late wake-up.
        async with self._queue_of_running_loop():
            delay = self._last + self._interval - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._last = self._clock()

    def _queue_of_running_loop(self) -> asyncio.Lock:
        # An asyncio.Lock belongs to the loop it first waited in; a limiter that outlives its
        # loop (one client, several asyncio.run() calls) gets a fresh queue in the next one.
        loop = asyncio.get_running_loop()
        if self._queue is None or self._queue_loop is None or self._queue_loop() is not loop:
            self._queue = asyncio.Lock()
            self._queue_loop = weakref.ref(loop)
        return self._queue
