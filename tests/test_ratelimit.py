from __future__ import annotations

import asyncio
import itertools

import httpx
import pytest
import respx

from aioamocrm import AmoCRMClient, LongLivedToken, RateLimiter


class FakeClock:
    """A monotonic clock that only moves when somebody sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        wake_at = self.now + delay
        await asyncio.sleep(0)  # let other callers run, as a real sleep would
        self.now = max(self.now, wake_at)


def limiter_with(clock: FakeClock, rate: float = 7.0) -> RateLimiter:
    return RateLimiter(rate, clock=clock, sleep=clock.sleep)


async def test_sequential_calls_are_spaced_by_one_over_the_rate() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock)
    times = []
    for _ in range(10):
        await limiter.acquire()
        times.append(clock.now)

    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert times[0] == 0.0
    assert all(gap == pytest.approx(1 / 7) for gap in gaps)


async def test_no_more_than_rate_calls_in_any_second() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock)
    times = []
    for _ in range(30):
        await limiter.acquire()
        times.append(clock.now)

    for start in times:
        in_window = [t for t in times if start <= t < start + 1.0 - 1e-9]
        assert len(in_window) <= 7


async def test_concurrent_callers_get_consecutive_slots() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock, rate=5.0)
    started: list[float] = []

    async def call() -> None:
        await limiter.acquire()
        started.append(clock.now)

    await asyncio.gather(*(call() for _ in range(6)))

    assert started == pytest.approx([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])


async def test_a_stalled_event_loop_does_not_release_a_burst() -> None:
    # Callers queue up, then the loop is blocked for 3 s (a big synchronous parse, a slow
    # callback) and every waiter wakes up late. They must still start 1 / rate apart, not
    # all at once on the first tick after the stall.
    clock = FakeClock()
    limiter = limiter_with(clock, rate=5.0)
    started: list[float] = []

    async def call() -> None:
        await limiter.acquire()
        started.append(clock.now)

    async def stall() -> None:
        await asyncio.sleep(0)
        clock.now += 3.0

    await asyncio.gather(stall(), *(call() for _ in range(6)))

    assert started[0] == 0.0
    assert started[1] == pytest.approx(3.0)
    gaps = [b - a for a, b in itertools.pairwise(started)]
    assert min(gaps) >= 0.2 - 1e-9


async def test_a_cancelled_caller_does_not_hold_up_the_others() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock, rate=5.0)
    await limiter.acquire()
    waiting = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)  # now asleep until its slot
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    await asyncio.wait_for(limiter.acquire(), timeout=1.0)
    assert clock.now == pytest.approx(0.2)


async def test_idle_time_does_not_build_up_a_burst() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock, rate=2.0)
    await limiter.acquire()
    clock.now = 100.0  # a long pause
    await limiter.acquire()
    assert clock.sleeps == []
    await limiter.acquire()
    assert clock.sleeps == pytest.approx([0.5])


def test_one_limiter_serves_one_event_loop_after_another() -> None:
    # A client built once and used from several asyncio.run() calls, as scripts and test
    # suites do: the waiting queue must not stay tied to the first loop.
    clock = FakeClock()
    limiter = limiter_with(clock, rate=5.0)

    async def burst() -> None:
        await asyncio.gather(*(limiter.acquire() for _ in range(3)))

    asyncio.run(burst())
    asyncio.run(burst())
    assert clock.now == pytest.approx(1.0)


def test_the_rate_is_configurable_and_validated() -> None:
    assert RateLimiter().rate == 7.0
    assert RateLimiter(2.5).rate == 2.5
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="rate"):
            RateLimiter(bad)


async def test_client_requests_are_spaced_by_its_limiter() -> None:
    clock = FakeClock()
    limiter = limiter_with(clock)
    sent: list[float] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(clock.now)
        return httpx.Response(200, json={})

    with respx.mock(base_url="https://example.amocrm.ru") as mock:
        route = mock.get("/api/v4/account").mock(side_effect=answer)
        async with AmoCRMClient(
            subdomain="example", auth=LongLivedToken("t"), rate_limit=limiter
        ) as amo:
            await asyncio.gather(*(amo.request("GET", "account") for _ in range(4)))

    assert route.call_count == 4
    assert sent == pytest.approx([0, 1 / 7, 2 / 7, 3 / 7])
