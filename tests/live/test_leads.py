"""A lead's round trip on a live account: create, read, update, add a note, close.

The API v4 cannot delete a lead, so the test cleans up by moving the lead to "lost" (143) in
the main pipeline, also when a step fails. Every lead it creates carries the tag
``aioamocrm-live`` and a tag unique to the run; search for ``aioamocrm-live`` in the interface
to find and delete them. Point the live tests at a test account.
"""

from __future__ import annotations

import secrets

from aioamocrm import AmoCRMClient, Lead, Note, Tag

from .conftest import LIVE

pytestmark = LIVE

TAG = "aioamocrm-live"
LOST = 143


async def test_a_lead_round_trip(live_amo: AmoCRMClient) -> None:
    run_tag = f"{TAG}-{secrets.token_hex(4)}"
    pipelines = await live_amo.pipelines.list()
    main = next(p for p in pipelines if p.is_main)
    assert main.id is not None

    lead = Lead(name=f"aioamocrm live test {run_tag}", price=100, pipeline_id=main.id)
    lead.embedded.tags.extend([Tag(name=TAG), Tag(name=run_tag)])
    [created] = await live_amo.leads.create([lead])
    lead_id = created.id
    assert lead_id is not None
    try:
        fetched = await live_amo.leads.get(lead_id)
        assert fetched.name == lead.name
        assert fetched.price == 100
        assert fetched.pipeline_id == main.id
        assert {TAG, run_tag} <= {tag.name for tag in fetched.embedded.tags}

        [updated] = await live_amo.leads.update(
            [Lead(id=lead_id, name=f"{lead.name} (updated)", price=250)]
        )
        assert updated.id == lead_id
        fetched = await live_amo.leads.get(lead_id)
        assert fetched.name == f"{lead.name} (updated)"
        assert fetched.price == 250
        assert {TAG, run_tag} <= {tag.name for tag in fetched.embedded.tags}

        text = f"Note from {run_tag}"
        [note_ref] = await live_amo.leads.notes.create([Note.common(lead_id, text)])
        assert note_ref.id is not None
        note = await live_amo.leads.notes.get(note_ref.id, entity_id=lead_id)
        assert note.entity_id == lead_id
        assert note.note_type == "common"
        assert note.text == text
        listed = [n.id async for n in live_amo.leads.notes.iterate(entity_id=lead_id)]
        assert note_ref.id in listed
    finally:
        await live_amo.leads.update([Lead(id=lead_id, pipeline_id=main.id, status_id=LOST)])

    closed = await live_amo.leads.get(lead_id)
    assert closed.status_id == LOST
