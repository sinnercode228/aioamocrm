"""Read-only checks against a live account: the account, its pipelines, an unknown lead."""

from __future__ import annotations

import pytest

from aioamocrm import AmoCRMClient, NotFoundError

from .conftest import LIVE, SUBDOMAIN

pytestmark = LIVE

WON = 142
LOST = 143


async def test_the_account(live_amo: AmoCRMClient) -> None:
    account = await live_amo.account.get()
    assert account.id is not None
    assert account.subdomain == SUBDOMAIN.strip().lower()
    assert account.current_user_id is not None


async def test_the_pipelines(live_amo: AmoCRMClient) -> None:
    pipelines = await live_amo.pipelines.list()
    assert pipelines
    assert [p.is_main for p in pipelines].count(True) == 1
    for pipeline in pipelines:
        assert pipeline.id is not None
        # "Won" and "lost" are part of every pipeline.
        assert {WON, LOST} <= {status.id for status in pipeline.statuses}

    main = next(p for p in pipelines if p.is_main)
    assert main.id is not None
    fetched = await live_amo.pipelines.get(main.id)
    assert fetched.name == main.name
    statuses = await live_amo.pipelines.statuses(main.id)
    assert [s.id for s in statuses] == [s.id for s in main.statuses]


async def test_an_unknown_lead_is_not_found(live_amo: AmoCRMClient) -> None:
    with pytest.raises(NotFoundError):
        await live_amo.leads.get(2_000_000_000)
