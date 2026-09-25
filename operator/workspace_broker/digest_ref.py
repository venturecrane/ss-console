"""Tie a digest's numbered raise rows to the mail thread that carried them.

The deadline digest numbers its items ("1. matter X, ...") and the reader
answers in plain words ("got it on 1"). For a reply to quiet the right item, the
number has to resolve to a ledger row, and the only honest scope for that
resolution is the thread the reply arrived in: the same number means a
different item in yesterday's digest. So every numbered raise row carries
``thread_ref``, the mail thread of the send that delivered it.

WHO MAY SAY WHICH THREAD. Not the caller. The overlay stamps each raise with the
per-dispatch nonce it sent on (``dispatch_ref``, carried through ``audit_extra``
onto the broker's own ``CONFIRM_SEND_DISPATCHED`` row) and the item's number
``n``. This module joins that nonce, within the raise's own session, to the
confirm row the broker wrote itself, and copies the vendor's thread id from it:
``thread_id`` on AgentMail, ``conversation_id`` on Graph. A caller-supplied
``thread_ref`` is always discarded, because a caller that could name the thread
could make a reply in one thread quiet an item raised in another.

FAILURE POSTURE. A raise that cannot be joined is still written, with ``n`` and
``dispatch_ref`` stripped. The raise records that an alarm reached a person,
which the send witness decides independently; losing it would make the item
re-fire on a delivered alarm. Losing only the number costs one thing: a plain
reply to that item finds no row, and the reader is asked which item they meant.

``snooze_days`` rides with the number: how long an ack of this item stays
quiet (the escalator's ``ack_snooze_days``), so the overlay's confirmation can
state the window from the row. It is validated (1..365) and stripped with the
number when the join fails.

Refusals are reserved for shapes that are wrong on their face: digest fields on
an event that is not a raise, a malformed nonce, a number or snooze out of
range.

Lives apart from ``send_witness.py`` so the witness keeps one job, and apart from
the vendored ``escalation_ledger`` twin, whose bytes are pinned against the
overlay's copy (``operator/contracts/overlay-pairs.json``).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from . import escalation_ledger

logger = logging.getLogger(__name__)

#: The overlay mints ``uuid4().hex``: 32 lowercase hex characters. Checked here
#: and in ``transmit_verbs._audit_extra`` so the two ends agree on one shape.
DISPATCH_REF_RE = re.compile(r"^[0-9a-f]{32}$")

#: The digest never numbers past the append cap (200); 999 is the ceiling a
#: human could plausibly type back, and anything past it is not a digest number.
MAX_ITEM_NUMBER = 999

#: The digest fields a caller may send on a raise. ``thread_ref`` is not among
#: them: it is the broker's to set.
_DIGEST_FIELDS = ("n", "dispatch_ref", "snooze_days")

#: The longest ack window a digest may state, in days.
MAX_SNOOZE_DAYS = 365

_SQL = "SELECT metadata FROM audit_log WHERE action_type = 'CONFIRM_SEND_DISPATCHED' AND metadata LIKE ?"


def valid_dispatch_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(DISPATCH_REF_RE.match(value))


def _valid_n(value: Any) -> bool:
    # bool is an int in Python; True is not item 1.
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_ITEM_NUMBER


def _thread_of(meta: dict[str, Any]) -> str:
    for key in ("thread_id", "conversation_id"):
        found = meta.get(key)
        if isinstance(found, str) and found.strip():
            return found.strip()
    return ""


def dispatched_thread(audit_db_path: str | None, session_id: str, dispatch_ref: str) -> str:
    """The thread id on this session's confirm row for ``dispatch_ref``, or ``""``.

    Never raises: an unreadable audit DB means no number, never no raise.
    """
    if not audit_db_path or not session_id or not valid_dispatch_ref(dispatch_ref):
        return ""
    # The row's metadata is written by ``append_send_row`` with sort_keys and
    # compact separators, so this LIKE is an exact prefilter; the nonce is hex,
    # so it holds no LIKE wildcard. The parse below is the actual match.
    pattern = f'%"dispatch_ref":"{dispatch_ref}"%'
    try:
        conn = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        logger.warning("digest thread join: audit DB unreadable; raise written without its number")
        return ""
    try:
        rows = conn.execute(_SQL, (pattern,)).fetchall()
    except sqlite3.Error:
        logger.warning("digest thread join: audit query failed; raise written without its number")
        return ""
    finally:
        conn.close()
    for (metadata,) in rows:
        try:
            meta = json.loads(metadata or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("dispatch_ref") != dispatch_ref:
            continue
        if str(meta.get("session_id") or "").strip() != session_id:
            continue
        thread = _thread_of(meta)
        if thread:
            return thread
    return ""


def stamp_thread_ref(audit_db_path: str | None, event: dict[str, Any]) -> None:
    """Complete (or strip) the digest fields on ``event`` in place, before validation.

    Raises ValueError for a shape that is wrong on its face; otherwise leaves the
    event either carrying ``n``, ``dispatch_ref`` and a broker-derived
    ``thread_ref``, or carrying none of the three.
    """
    event.pop("thread_ref", None)
    present = [field for field in _DIGEST_FIELDS if field in event]
    if not present:
        return
    kind = event.get("event")
    if kind not in escalation_ledger.RAISING_EVENTS:
        raise ValueError(
            f"{' and '.join(present)} number an item in a delivered digest, and only a raise "
            f"delivers one; a {kind} carries no digest number. Drop "
            f"{'them' if len(present) > 1 else 'it'} from this append."
        )
    dispatch_ref = event.get("dispatch_ref")
    if dispatch_ref is not None and not valid_dispatch_ref(dispatch_ref):
        raise ValueError("dispatch_ref must be the 32-character lowercase hex nonce the send carried")
    n = event.get("n")
    if n is not None and not _valid_n(n):
        raise ValueError(f"n must be a whole number from 1 to {MAX_ITEM_NUMBER}")
    snooze = event.get("snooze_days")
    if snooze is not None and not (
        isinstance(snooze, int) and not isinstance(snooze, bool) and 1 <= snooze <= MAX_SNOOZE_DAYS
    ):
        raise ValueError(f"snooze_days must be a whole number from 1 to {MAX_SNOOZE_DAYS}")
    session_id = str(event.get("session_id") or "").strip()
    thread = dispatched_thread(audit_db_path, session_id, dispatch_ref) if dispatch_ref and n else ""
    if not thread:
        for field in _DIGEST_FIELDS:
            event.pop(field, None)
        return
    event["thread_ref"] = thread
