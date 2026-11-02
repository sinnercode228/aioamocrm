"""Skip amoCRM webhooks that were already handled.

amoCRM resends a webhook that is not answered with a 2xx quickly enough, so the same change can
arrive twice. :func:`aioamocrm.parse_webhook` gives one event per entity and action, and
:func:`event_key` names each one by account, entity, id, action and the time of the change
(``last_modified``, ``updated_at`` or, for notes, ``date_create``). A resent webhook carries
the same values and gets the same key; the next change of the same lead gets a new one.

:class:`RecentKeys` remembers keys in process memory for a day by default. Several processes
need a shared store instead: in Redis, ``SET <key> 1 NX EX 86400`` claims a key atomically and
answers nil when another process claimed it first.

Run it on bodies saved from amoCRM requests, in the order they came::

    python examples/webhook_dedupe.py first.txt second.txt
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from aioamocrm import Webhook, WebhookEvent, parse_webhook

DAY = 24 * 60 * 60


def event_key(webhook: Webhook, event: WebhookEvent) -> str:
    """A key that a resent webhook repeats and the next change of the entity does not."""
    item: Any = event.payload.get("note") if event.action == "note" else event.payload
    if not isinstance(item, dict):
        item = {}
    version = (
        item.get("last_modified")
        or item.get("updated_at")
        or item.get("date_create")
        or item.get("status_id")
        or ""
    )
    return f"{webhook.account_id}:{event.entity}:{item.get('id')}:{event.action}:{version}"


class RecentKeys:
    """Keys seen in the last ``ttl`` seconds, in memory."""

    def __init__(self, ttl: float = DAY, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._expires: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self._expires)

    def claim(self, key: str) -> bool:
        """Remember ``key``; ``False`` when it was already claimed and has not expired."""
        now = self._clock()
        self._expires = {k: t for k, t in self._expires.items() if t > now}
        if key in self._expires:
            return False
        self._expires[key] = now + self.ttl
        return True


def main(paths: Sequence[str]) -> None:
    seen = RecentKeys()
    for path in paths:
        webhook = parse_webhook(Path(path).read_bytes())
        for event in webhook.events:
            key = event_key(webhook, event)
            print(f"{'new' if seen.claim(key) else 'duplicate'}: {key}")


if __name__ == "__main__":
    main(sys.argv[1:])
