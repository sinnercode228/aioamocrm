"""Receive amoCRM webhooks with FastAPI.

amoCRM account webhooks are not signed, so the URL carries the secret. Generate one and
register ``https://your-host/webhooks/amocrm/<secret>`` in the amoCRM webhook settings::

    export AMO_WEBHOOK_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    uvicorn examples.webhook_fastapi:app

The endpoint checks the secret in constant time, parses the form body and answers right away;
the events are handled in a background task, because amoCRM resends webhooks that are not
answered quickly and switches off endpoints that keep failing. Background tasks die with the
process: when losing an event matters, put the parsed events in a queue or a table instead.

Since anyone who learns the URL can post to it, treat an event as a notification: before
acting on anything that matters, fetch the entity by ``event.id`` through the API. The README
section "Webhooks" has the details.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from aioamocrm import Webhook, parse_webhook

logger = logging.getLogger("amocrm.webhooks")


def handle(webhook: Webhook) -> None:
    """Do the real work: here, log what happened."""
    for event in webhook.events:
        if (event.entity, event.action) == ("leads", "status"):
            logger.info(
                "lead %s moved from status %s to %s",
                event.id,
                event.payload.get("old_status_id"),
                event.payload.get("status_id"),
            )
        elif event.entity == "contacts" and event.action in ("add", "update"):
            logger.info("contact %s, phone %s", event.id, event.field_value("PHONE"))
        elif event.action == "note":
            note = event.payload.get("note", {})
            logger.info("note on %s %s: %s", event.entity, note.get("element_id"), note.get("text"))
        else:
            logger.info("%s %s %s", event.entity, event.action, event.id)


def create_app(secret: str, handler: Callable[[Webhook], None] = handle) -> FastAPI:
    app = FastAPI(title="amoCRM webhooks")
    expected = secret.encode()  # an empty secret accepts nothing

    @app.post("/webhooks/amocrm/{secret}")
    async def amocrm_webhook(
        secret: str, request: Request, background: BackgroundTasks
    ) -> dict[str, int]:
        if not expected or not hmac.compare_digest(secret.encode(), expected):
            raise HTTPException(status_code=404)
        try:
            webhook = parse_webhook(await request.body())
        except ValueError:
            raise HTTPException(status_code=400, detail="malformed webhook body") from None
        background.add_task(handler, webhook)
        return {"events": len(webhook.events)}

    return app


app = create_app(os.environ.get("AMO_WEBHOOK_SECRET", ""))
