"""Which seat owns an AgentMail inbox.

Split out of ``reconcile-sends.py`` (module-size ratchet) when matching on the
local part alone was found to map no seat to an inbox not named after its slug:
``brokery-agent@`` for brokery, ``agentcrane@`` for scott. Those sends then
reported as an unowned mailbox, or, once allowlisted to quiet that, were never
reconciled at all.
"""

from __future__ import annotations

import os
from typing import Callable, Optional


def authored_inbox(customers_dir: str, slug: str) -> Optional[str]:
    """The seat's authored ``connectors.Email.inbox_address``, lowercased, or None.

    The same field the workspace broker pins every outbound send to
    (``agentmail_auth.seat_inbox_address``) and the lists check resolves
    (``check-agentmail-lists.py`` ``seat_inbox``). Any read or parse failure is
    None: the convention still applies, and an inbox that then matches nothing
    is reported as unowned, never silently skipped.
    """
    try:
        import yaml  # deferred: only this lookup needs it

        with open(os.path.join(customers_dir, slug, "customer.yaml"), encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 - unreadable config falls back to the convention
        return None
    connectors = config.get("connectors") if isinstance(config, dict) else None
    email = connectors.get("Email") if isinstance(connectors, dict) else None
    authored = email.get("inbox_address") if isinstance(email, dict) else None
    return authored.strip().lower() if isinstance(authored, str) and authored.strip() else None


def slug_for_inbox(
    inbox: str,
    slugs: list[str],
    authored: Optional[dict[str, Optional[str]]],
    read: Callable[[str], Optional[str]],
) -> Optional[str]:
    """The seat that owns this inbox. None means nobody owns it, which is a
    finding in itself rather than a reason to skip the inbox.

    An AUTHORED ``inbox_address`` wins; the ``<slug>@`` local-part convention
    applies only to a seat that authored none. ``authored`` is injectable for
    tests; otherwise ``read`` resolves each seat.
    """
    address = inbox.lower()
    lookup = authored if authored is not None else {s: read(s) for s in slugs}
    owner = next((s for s in slugs if lookup.get(s) == address), None)
    if owner is not None:
        return owner
    local = address.split("@", 1)[0]
    return next((s for s in slugs if s.lower() == local and not lookup.get(s)), None)
