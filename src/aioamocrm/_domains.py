"""The domains amoCRM and Kommo accounts live on, and the test that a host is one of them."""

from __future__ import annotations

import re

__all__ = ["DOMAINS", "is_account_host"]

#: Account domains of amoCRM (Russia, international) and Kommo.
DOMAINS = ("amocrm.ru", "amocrm.com", "kommo.com")

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")


def is_account_host(host: str) -> bool:
    """Tell whether ``host`` is a plain host name under one of :data:`DOMAINS`.

    ``example.amocrm.ru`` and ``api-a.amocrm.ru`` are; ``amocrm.ru`` itself, a host with a port,
    a path or upper-case letters, and anything on another domain are not.
    """
    return _HOST.fullmatch(host) is not None and any(
        host.endswith(f".{domain}") for domain in DOMAINS
    )
