"""WHO a silent reply-lane run is about (ss#2581).

Split out of reconcile-outcomes.py for the operator module-size ceiling; the
reconciler re-exports every public name here so its callers and tests are
unchanged. Typed structurally on purpose: this module never imports the
reconciler, it only reads and stamps attributes on its Obligation rows.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "workspace_broker",
    ),
)

from recipient_policy import sender_key as _sender_key

CUSTOMERS_DIR = Path(__file__).resolve().parents[2] / "customers"

# WHO a silent reply-lane run is about (ss#2581). A finding on the paying
# client's seat reads as a client incident until somebody proves otherwise, and
# on 2026-09-02 proving otherwise took an hour of a person's time for two runs
# the SMD operator had triggered himself. The row has carried `sender_key`
# since ss#2497; the report just never said so. Classes are decided at roster
# load time, where the plaintext address is authored, and compared as hashes
# only -- a raw address never enters this report.
SENDER_FIRM = "firm-rostered"  # a person the firm authored in customer.yaml users[]
SENDER_SMD = "smd-operator"  # an @smd.services address on that roster
SENDER_PROBE = "probe"  # the shadow firm's ss-probe-* senders
SENDER_UNKNOWN = "unknown"  # key present, matches nobody authored
SENDER_UNRECORDED = "unrecorded"  # row predates ss#2497, no key to compare
SMD_DOMAIN = "smd.services"
PROBE_LOCAL_PREFIX = "ss-probe"
#: WEBHOOK_ROUTED precedes the INBOUND_RECEIVED it routed by milliseconds and
#: carries no sender (only the receiving row does, webhook-router:170). An
#: inbound obligation with no key of its own adopts the key of the next
#: INBOUND_RECEIVED trigger inside this many seconds, and says so.
SIBLING_KEY_WINDOW_SECONDS = 5


# ---------------------------------------------------------------------------
# who the silent run is about (ss#2581)
# ---------------------------------------------------------------------------


def load_roster(slug: str, customers_dir: Path = CUSTOMERS_DIR) -> dict[str, str]:
    """``sender_key -> SENDER_*`` for every address the seat's customer.yaml
    authors under ``users[]``. The plaintext is read HERE, once, and only the
    hash leaves this function; the class is decided while the address is still
    in hand, because a hash cannot be asked what domain it was.

    A seat with no authored roster returns an empty map, so every keyed sender
    on it classes as ``unknown``. That is the honest answer, not a default.
    """
    path = customers_dir / slug / "customer.yaml"
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roster: dict[str, str] = {}
    for user in doc.get("users") or []:
        email = (user or {}).get("email") if isinstance(user, dict) else None
        key = _sender_key(email)
        if not key:
            continue
        roster[key] = _class_for_address(str(email))
    return roster


def _class_for_address(email: str) -> str:
    local, _, domain = email.strip().lower().rpartition("@")
    if local.startswith(PROBE_LOCAL_PREFIX):
        return SENDER_PROBE
    if domain == SMD_DOMAIN:
        return SENDER_SMD
    return SENDER_FIRM


def _adopt_sibling_keys(obligations: list[Any]) -> None:
    """A WEBHOOK_ROUTED trigger carries no sender; the INBOUND_RECEIVED it
    routed, milliseconds later, does. Give the routed obligation that key and
    say it was borrowed, so two lines for one message name the same person.
    """
    keyed = sorted(
        (o for o in obligations if o.trigger_kind == "inbound" and o.sender_key),
        key=lambda o: o.opened_at,
    )
    for obligation in obligations:
        if obligation.trigger_kind != "inbound" or obligation.sender_key:
            continue
        deadline = obligation.opened_at + timedelta(seconds=SIBLING_KEY_WINDOW_SECONDS)
        for sibling in keyed:
            if obligation.opened_at <= sibling.opened_at <= deadline:
                obligation.sender_key = sibling.sender_key
                obligation.sender_key_via = "sibling"
                break


def classify_senders(obligations: list[Any], roster: dict[str, str]) -> None:
    """Stamp ``sender_class`` on every inbound obligation. Never on the others:
    a scheduled wake or a bare hold has no sender, and inventing ``unknown``
    for it would make a cron silence look like an unidentified person."""
    _adopt_sibling_keys(obligations)
    for obligation in obligations:
        if obligation.trigger_kind != "inbound":
            obligation.sender_class = None
        elif not obligation.sender_key:
            obligation.sender_class = SENDER_UNRECORDED
        else:
            obligation.sender_class = roster.get(obligation.sender_key, SENDER_UNKNOWN)


def sender_label(obligation: Any) -> str:
    if obligation.sender_class is None:
        return "-"
    if obligation.sender_key_via == "sibling":
        return f"{obligation.sender_class}(via-sibling)"
    return obligation.sender_class


def sender_breakdown(findings: list[Any]) -> str:
    """`` (firm-rostered=2 smd-operator=8 unrecorded=3)`` -- only the classes
    that occur, only when at least one finding has a sender. The order is fixed
    so the paying client's people are the first number a reader sees."""
    counts: dict[str, int] = {}
    for obligation in findings:
        if obligation.sender_class is not None:
            counts[obligation.sender_class] = counts.get(obligation.sender_class, 0) + 1
    if not counts:
        return ""
    order = (SENDER_FIRM, SENDER_UNKNOWN, SENDER_UNRECORDED, SENDER_SMD, SENDER_PROBE)
    parts = [f"{cls}={counts[cls]}" for cls in order if cls in counts]
    return " (" + " ".join(parts) + ")"
