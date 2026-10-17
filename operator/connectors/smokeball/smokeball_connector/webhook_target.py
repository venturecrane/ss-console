"""The one callback URL a Smokeball webhook subscription may point at.

``create_webhook_subscription`` is a model-callable tool, and a subscription is
persistent: once registered, Smokeball pushes the firm's matter events to its
``eventNotificationUrl`` until someone deletes it, each delivery signed with the
gate's own ``WEBHOOK_SECRET_SMOKEBALL``. Before 2026-10-17 the URL was whatever
the caller passed, so a single injected email turn could have registered a feed
of every matter event to any host on the internet.

The only legitimate target is the seat's OWN webhook gate, and the seat has
already authored it: ``connectors.<cap>.webhook_url`` on the enabled
``backend: mcp:smokeball`` connector in customer.yaml (the same value the boot
reconciler, ``operator/bin/webhook_reconcile.py``, registers). This module reads
it fresh on every call (no module-level state) and returns it only when it is
unambiguous: exactly one such connector, an ``https`` URL whose path is
``/webhooks/smokeball``. Anything else returns None and the tool refuses.
Fail-closed: no authored URL means no subscription, never a caller-chosen one.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML

_GATE_PATH = "/webhooks/smokeball"


def seat_webhook_url(path: str | None = None) -> str | None:
    """The seat's authored Smokeball webhook gate URL, or None."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable or unparseable config: no target
        return None
    connectors = data.get("connectors") if isinstance(data, dict) else None
    if not isinstance(connectors, dict):
        return None
    urls = {
        str(conn["webhook_url"]).strip()
        for conn in connectors.values()
        if isinstance(conn, dict)
        and conn.get("enabled")
        and str(conn.get("backend") or "") == "mcp:smokeball"
        and conn.get("webhook_url")
    }
    if len(urls) != 1:
        return None
    url = urls.pop()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.path != _GATE_PATH:
        return None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None
    return url
