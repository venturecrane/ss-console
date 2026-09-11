"""Descends check: does this export still contain a head we already pinned?

ss#2500. ``verify_chain`` (operator/workspace_broker/chain.py) proves internal
consistency and nothing more. That is enough to catch a mutated or deleted row
in the MIDDLE of the chain, and it is provably not enough to catch a truncated
TAIL, because the surviving prefix is a valid chain in its own right.

Falsified rather than reasoned about (vfy_01M0H8D1CV2X8J9ZACMAC8E6E2, run
against mutated copies of a live 1,473-row export, baseline INTACT):

    mutate a middle row .................. BROKEN
    delete a middle row .................. BROKEN
    delete the last 50 rows .............. INTACT   <- this module
    delete the last 1 row ................ INTACT   <- this module
    append a forged row with a valid hash. INTACT
    mutate a row, re-hash everything after INTACT   <- this module

The missing input is external: a head recorded somewhere the seat cannot reach.
The console records one from every heartbeat (``audit_head_history``, migration
0108). Given such a head, one question answers all three cases this module
covers -- **does the pinned head still appear as some row's ``row_hash`` in the
export?**

* present and equal to the current head -> the ledger has not moved since the
  pin. Fine.
* present and not the head -> the chain GREW past the pin. Fine, and the only
  shape a healthy ledger produces.
* absent -> the pin was rolled back out of existence. Tail truncation, a
  rewrite that re-hashed everything after some row, or a head regression all
  land here, and this module does not try to tell them apart: which one it was
  is a question for a human with two exports, and calling it wrong in a
  compliance artifact is worse than calling it a break.

WHAT THIS STILL CANNOT DO, stated because the docs quote it. A pin protects the
rows OLDER than the last pin. Rows written after it are unpinned until the next
heartbeat lands, so root on the Machine has a beat-sized window in which a
freshly written row can be removed with nothing to notice. Shortening that
window means beating more often, not a different mechanism. It also cannot
distinguish a forged append from a real one: an appended row with a correct
hash still descends from the pin. That case is out of scope by design (ADR 0074
rejected per-row signing) and is stated rather than papered over.

Nothing here imports the chain module. This file deliberately does not touch
``operator/workspace_broker/chain.py``, which is a byte-identical twin of the
overlay's ``shared/audit_chain.py`` (SEC-32, operator/contracts/overlay-pairs.json)
and cannot take a one-sided edit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

#: A chain head is a sha256 hexdigest: 64 lowercase hex characters.
HEAD_RE = re.compile(r"^[0-9a-f]{64}$")

#: Verdicts. ``PIN_ABSENT`` is the only one that is a break.
PIN_UNCHANGED = "pin_unchanged"
PIN_DESCENDS = "pin_descends"
PIN_ABSENT = "pin_absent"
PIN_NOT_SUPPLIED = "pin_not_supplied"
PIN_MALFORMED = "pin_malformed"


def is_head(value: Any) -> bool:
    return isinstance(value, str) and bool(HEAD_RE.match(value))


def check_pinned_head(
    rows: Iterable[Mapping[str, Any]],
    *,
    pinned_head: Optional[str],
    current_head: Optional[str],
) -> dict:
    """Answer the descends question for one export.

    ``rows`` are the export's rows (each carrying ``row_hash``);
    ``current_head`` is ``verify_chain``'s reported head for the same export.

    Returns::

        {
          "ok": bool,            # False ONLY on an absent or malformed pin
          "verdict": str,        # one of the PIN_* constants
          "pinned_head": str|None,
          "current_head": str|None,
          "reason": str,         # one plain sentence, safe to print verbatim
        }

    A missing pin is ``ok=True`` and ``PIN_NOT_SUPPLIED``: this function is not
    the place that decides whether "nobody pinned anything" is acceptable. The
    daily watcher treats it as a HOLD and fails its run; a local operator
    verifying an export by hand does not need a pin to get a useful answer. Both
    callers need the same fact reported the same way, and only one of them
    should be turning it into a verdict about the fleet.
    """
    if pinned_head is None or pinned_head == "":
        return {
            "ok": True,
            "verdict": PIN_NOT_SUPPLIED,
            "pinned_head": None,
            "current_head": current_head,
            "reason": (
                "No pinned head was supplied, so this export was checked for "
                "internal consistency only. Tail truncation is not detectable "
                "without a pin."
            ),
        }

    if not is_head(pinned_head):
        # Loud rather than lenient. A malformed pin can never appear in any
        # export, so treating it as absent would report a break on a healthy
        # ledger every single day -- a false accusation about a client's audit
        # record, which is the one failure this control must not produce.
        return {
            "ok": False,
            "verdict": PIN_MALFORMED,
            "pinned_head": pinned_head,
            "current_head": current_head,
            "reason": (
                "The supplied pinned head is not a sha256 hexdigest, so it "
                "could not appear in any export. This is a broken instrument, "
                "not a finding about the ledger."
            ),
        }

    if pinned_head == current_head:
        return {
            "ok": True,
            "verdict": PIN_UNCHANGED,
            "pinned_head": pinned_head,
            "current_head": current_head,
            "reason": "The chain tip is still the pinned head; no rows were added since the pin.",
        }

    for row in rows:
        if row.get("row_hash") == pinned_head:
            return {
                "ok": True,
                "verdict": PIN_DESCENDS,
                "pinned_head": pinned_head,
                "current_head": current_head,
                "reason": ("The pinned head is still in this export and the chain has grown past it."),
            }

    return {
        "ok": False,
        "verdict": PIN_ABSENT,
        "pinned_head": pinned_head,
        "current_head": current_head,
        "reason": (
            "The pinned head does not appear anywhere in this export. Rows that "
            "existed when the head was pinned are gone: the ledger was "
            "truncated, rewritten, or rolled back."
        ),
    }


__all__ = [
    "HEAD_RE",
    "PIN_ABSENT",
    "PIN_DESCENDS",
    "PIN_MALFORMED",
    "PIN_NOT_SUPPLIED",
    "PIN_UNCHANGED",
    "check_pinned_head",
    "is_head",
]
