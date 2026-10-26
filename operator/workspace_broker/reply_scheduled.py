"""Who a scheduled bound message may reach: the broker's half of the reply
binding's new-message mode (a scheduled litigation list, a negotiation notice,
a new-message SMD note), held apart from ``reply_binding`` (its size ceiling).

The recipient is never named by a caller: it is the job's requester, who must
still be a Named Administrator AND an authored ``scheduled_recipients`` entry of
that kind's skill, re-read from the live customer.yaml at bind and at send time.
"""

from __future__ import annotations

from typing import Any

from .broker_context import BrokerContext

from .recipient_policy import normalize_address


def _authored_recipients(kind: str, customer_path: Any) -> tuple[list[str], bool]:
    """(that kind's skill's authored ``scheduled_recipients``, whether only the
    FIRST entry may be reached). An explicit table that raises on any other
    kind: the old ``else`` read negotiation-watch's list for anything that was
    not litigation, which a note must never inherit (2026-10-16)."""
    if kind == "litigation_job":
        from . import litigation_intake as intake
    elif kind == "negotiation_job":
        from . import negotiation_intake as intake
    elif kind == "note_job":
        from .note_verbs import note_recipients

        return note_recipients(customer_path) or [], False
    else:
        raise ValueError(f"{kind} has no authored scheduled recipients")
    return intake.scheduled_recipients(intake.settings_of(intake.skill_entry(customer_path))), True


def scheduled_recipient_ok(broker: BrokerContext, kind: str, requester: str) -> bool:
    """The scheduled run's requester is still a Named Administrator AND an
    authored ``scheduled_recipients`` entry of that kind's skill, read from the
    live yaml: the FIRST entry for litigation and negotiation, any entry for a
    new-message note."""
    from .medchron_ledger import admins_from_customer_yaml

    who = normalize_address(requester)
    recipients, first_only = _authored_recipients(kind, broker.customer_path)
    admins = admins_from_customer_yaml(str(broker.customer_path)) or ()
    if not recipients or who not in admins:
        return False
    return recipients[0] == who if first_only else who in recipients


__all__ = ["scheduled_recipient_ok"]
