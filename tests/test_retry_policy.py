from __future__ import annotations

import random
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from aioamocrm import RetryPolicy
from aioamocrm._errors import parse_retry_after


class Bounds(random.Random):
    """Returns the lower or the upper bound of every ``uniform`` call."""

    def __init__(self, *, upper: bool) -> None:
        super().__init__(0)
        self.upper = upper

    def uniform(self, a: float, b: float) -> float:
        return b if self.upper else a


def test_backoff_doubles_with_jitter_and_is_capped() -> None:
    policy = RetryPolicy(backoff_base=0.5, backoff_max=3.0)
    highs = [policy.backoff(n, Bounds(upper=True)) for n in range(1, 7)]
    lows = [policy.backoff(n, Bounds(upper=False)) for n in range(1, 7)]
    assert highs == [0.5, 1.0, 2.0, 3.0, 3.0, 3.0]
    assert lows == [0.25, 0.5, 1.0, 1.5, 1.5, 1.5]


def test_backoff_stays_capped_for_any_attempt_number() -> None:
    # 0.5 * 2 ** 1100 does not fit in a float: a long-running retry loop must not crash on it.
    policy = RetryPolicy(max_attempts=5_000, backoff_base=0.5, backoff_max=30.0)
    for attempt in (64, 1_025, 1_100, 4_999):
        assert policy.backoff(attempt, Bounds(upper=True)) == 30.0


def test_backoff_jitter_stays_within_the_band() -> None:
    policy = RetryPolicy(backoff_base=1.0, backoff_max=60.0)
    rng = random.Random(42)
    for attempt in range(1, 6):
        ceiling = 2 ** (attempt - 1)
        delays = {policy.backoff(attempt, rng) for _ in range(50)}
        assert all(ceiling / 2 <= d <= ceiling for d in delays)
        assert len(delays) > 1  # actually jittered


def test_defaults() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 4
    assert policy.retry_statuses == frozenset({500, 502, 503, 504})
    assert policy.idempotent_methods == frozenset({"GET", "HEAD", "OPTIONS"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_base": -1.0},
        {"backoff_max": -1.0},
        {"max_retry_after": -1.0},
    ],
)
def test_policy_is_validated(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("3", 3.0),
        (" 1.5 ", 1.5),
        ("-4", 0.0),
        ("inf", None),
        ("nan", None),
        ("soon", None),
    ],
)
def test_retry_after_seconds(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


def test_retry_after_http_date() -> None:
    now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 16, 12, 0, 7, tzinfo=timezone.utc)
    assert parse_retry_after(format_datetime(later, usegmt=True), now=now.timestamp()) == 7.0
    assert parse_retry_after(format_datetime(now, usegmt=True), now=later.timestamp()) == 0.0


@pytest.mark.parametrize(
    ("header", "day"),
    [
        ("Sun Nov  6 08:49:37 1994", 6),
        ("Sun Nov 06 08:49:37 1994", 6),
        ("Wed Nov 16 08:49:37 1994", 16),
        ("Sunday, 06-Nov-94 08:49:37 GMT", 6),
        ("Sun, 06 Nov 1994 08:49:37 GMT", 6),
    ],
)
def test_retry_after_all_http_date_formats(header: str, day: int) -> None:
    moment = datetime(1994, 11, day, 8, 49, 37, tzinfo=timezone.utc).timestamp()
    assert parse_retry_after(header, now=moment - 7) == 7.0
    assert parse_retry_after(header, now=moment + 7) == 0.0


@pytest.mark.parametrize(
    "header",
    [
        "Sun Nov  6 25:00:00 1994",
        "Sun Nov  6 08:49:37",
        "Sun Nov 6 08:49:37 1994",  # missing date3 padding
        "Sun Nov  6 08:49:37 94",
        "Sun Xxx  6 08:49:37 1994",
    ],
)
def test_retry_after_malformed_asctime_is_ignored(header: str) -> None:
    assert parse_retry_after(header, now=0) is None
