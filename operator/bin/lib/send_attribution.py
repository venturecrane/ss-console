"""Attribution for the send verifier: WHICH routine does a send belong to?

Sibling of ``send_verify.py`` (which is at the module-size ceiling; this is the
``send_invariants.py`` precedent). Everything here answers one question --
given a CONFIRM_SEND_DISPATCHED row or a mailbox message, which EMITTED_WAKE
authored it -- and hands the answer back so ``send_verify`` can grade the pair.

WHY ATTRIBUTION IS A MODULE OF ITS OWN (claims review 2026-09-04, B3 + B7).
The verifier joined dispatch to wake by ``skill_name`` alone, and joined mailbox
message to wake by TIME alone. Two live consequences:

* B3 -- the broker never wrote ``skill_name`` on its rows (the column existed,
  the value was NULL on every CONFIRM row), so ``declares.get("")`` was None and
  the primary check graded nothing, silently. Now the broker writes the column
  (``transmit_verbs._CALLER_AUDIT_KEYS``) from the overlay's cron-resolved routine, and this
  module attributes SKILL-FIRST when the column is set and HASH-SECOND when it
  is not: an unlabelled dispatch whose ``rendered_body_sha256`` sits in a
  hash-verified wake's stamps IS that wake's send -- a sha256 over a rendered
  body with per-matter identifiers in it does not collide by accident. So the
  join works on a seat whose pinned overlay predates the column (hash), and
  gets stronger once the pin lands (skill). Both paths are COUNTED
  (``attribution_counts``), never only described in a detail string, so a
  regression of the column reads as a number moving rather than as silence.

* B7 -- proximity claimed the wrong message. An in-turn send that happened to
  land inside a tracker wake's hour was graded against the tracker's stamps and
  filed BODY_DIVERGED for a body the tracker never authored. A message is now
  attributed by IDENTITY: its ``message_id`` / ``audit_row_token`` is looked up
  against the dispatch rows' join keys (the same ``_usable_ids`` the reconciler
  uses for its exact pass). The result is TRI-STATE, and the three states are
  graded differently on purpose:

    skill_name (str)  the joined row names a routine (column, or hash-attributed)
                      -> claimable by THAT skill's wake only;
    ""                the joined row is a send with no routine (an in-turn
                      send, an establishment ops note) -> identified, and never
                      claimable by any wake;
    None              no dispatch row joins this message -> the old window
                      claim, but graded HOLD-ONLY: a divergence on a message
                      nobody can tie to a routine is ``channel_mismatch_hold``,
                      never a finding, because a finding accuses a routine.

LEAK SAFETY. Same constraint as the rest of the phase: nothing here holds body
text. Inputs are stamps (hashes, timestamps, names) and the mailbox message
dict's id fields; outputs are names and counts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover -- type names only; send_verify imports us
    from datetime import datetime

    from send_verify import DispatchStamp, RenderDecl, WakeStamp

#: Metadata keys that carry a vendor message id on an audited send (any key
#: containing this substring), and the audit-header key stamped on the message
#: itself (ss#2499). Mirrors reconcile-sends' ``_ID_KEY_SUBSTRING`` /
#: ``msgraph_channel.AUDIT_TOKEN_KEY``; declared here so this module has no
#: import edge back into the script that imports it.
ID_KEY_SUBSTRING = "message_id"
AUDIT_TOKEN_KEY = "audit_row_token"

#: A recorded id that is not an id. The overlay writes this literal on a msgraph
#: REPLY_SENT row when Graph's 202 returned nothing to record (8 of 8 rows on the
#: live seat before the header landed), and it is honest there -- inventing an id
#: would name a message the mailbox does not contain. It must not be treated as
#: an exact key, and a row carrying only this is a row with no usable id. Matched
#: on the leading parenthesis rather than the exact sentence: no RFC2822 id and
#: no Graph id begins with one, so any future note in that field is caught too.
UNRESOLVED_ID_PREFIX = "("

#: How a dispatch came to be paired with its wake. Counted per inbox.
ATTRIBUTED_BY_SKILL = "skill"
ATTRIBUTED_BY_HASH = "hash"


def _usable_ids(meta: dict) -> set[str]:
    """Every value in one row's metadata that can serve as an EXACT join key.

    A key is usable when it is a non-empty string under a ``*message_id*`` key or
    under ``audit_row_token``, and is not the overlay's "no id available" note
    (see ``UNRESOLVED_ID_PREFIX``). Moved verbatim from reconcile-sends.py so the
    reconciler's exact pass and the verifier's identity join read the SAME keys
    off a row -- two copies of this rule would drift, and in the direction of
    the one nobody is reading.
    """
    ids: set[str] = set()
    for key, value in meta.items():
        if not isinstance(value, str) or not value:
            continue
        if value.startswith(UNRESOLVED_ID_PREFIX):
            continue
        if ID_KEY_SUBSTRING in key or key == AUDIT_TOKEN_KEY:
            ids.add(value)
    return ids


def message_keys(message: dict) -> set[str]:
    """The ids a normalized mailbox message offers for the identity join:
    its vendor ``message_id`` (AgentMail) and, on msgraph, the
    ``audit_row_token`` the channel reader lifted off the ``X-SMD-Audit-Row``
    header (msgraph_channel.normalize_graph_message)."""
    return {
        str(message[key])
        for key in ("message_id", AUDIT_TOKEN_KEY)
        if isinstance(message.get(key), str) and message[key]
    }


def _in_window(wake: "WakeStamp", dispatch: "DispatchStamp", window_s: int) -> bool:
    return wake.ts <= dispatch.ts <= wake.ts + timedelta(seconds=window_s)


def _hash_verified(wake: "WakeStamp", declares: dict[str, "RenderDecl"]) -> bool:
    decl = declares.get(wake.skill_name)
    return decl is not None and decl.hash_verified and bool(wake.hashes_full)


def _wakes_for_hash(
    wakes: list["WakeStamp"],
    dispatch: "DispatchStamp",
    declares: dict[str, "RenderDecl"],
    window_s: int,
):
    """Every hash-verified wake, in window, whose stamps hold this dispatch's
    ``rendered_body_sha256`` (full or skeleton), oldest first. Yields nothing
    for a dispatch with no rendered hash: there is nothing to attribute by, and
    a bare unlabelled row is not evidence of anything."""
    if not dispatch.rendered_body_sha256:
        return
    for wake in wakes:
        if not _hash_verified(wake, declares) or not _in_window(wake, dispatch, window_s):
            continue
        if dispatch.rendered_body_sha256 in wake.hashes_full or (dispatch.rendered_body_sha256 in wake.hashes_skeleton):
            yield wake


def wake_for_hash(
    wakes: list["WakeStamp"],
    dispatch: "DispatchStamp",
    declares: dict[str, "RenderDecl"],
    window_s: int,
) -> Optional["WakeStamp"]:
    """The first such wake. A LOOKUP -- nothing is consumed -- so the message
    attributor can ask it without spending a wake's capacity."""
    return next(_wakes_for_hash(wakes, dispatch, declares, window_s), None)


def claim_wake(
    wakes: list["WakeStamp"],
    dispatch: "DispatchStamp",
    declares: dict[str, "RenderDecl"],
    window_s: int,
) -> tuple[Optional["WakeStamp"], str]:
    """Pair one dispatch with the wake that authored it, CONSUMING capacity.

    Returns ``(wake, attribution)``:

    * ``(wake, "skill")``  -- the dispatch names a hash-verified skill and the
      oldest same-skill wake in window with capacity left claimed it;
    * ``(None, "skill")``  -- it names a hash-verified skill and no wake in
      window can claim it: the caller reports ``no_wake_hash`` (a hold);
    * ``(wake, "hash")``   -- the dispatch names NO skill and its rendered hash
      sits in a hash-verified wake's stamps: attributed by hash, graded like any
      other pair (it matched, so MATCH or DEGRADED);
    * ``(None, "")``       -- nothing to grade: a compositional or undeclared
      skill, or an unlabelled dispatch no wake's hashes recognise (an
      establishment ops note, an in-turn send). Today's silent skip, kept --
      those rows must not redden every run.

    Capacity is consumed per dispatch entry the wake stamped
    (``dispatch_capacity``), on both paths, so one wake cannot launder an
    unbounded run of dispatches whichever way they were attributed.
    """
    if dispatch.skill_name:
        decl = declares.get(dispatch.skill_name)
        if decl is None or not decl.hash_verified:
            return None, ""
        for wake in wakes:
            if wake.skill_name != dispatch.skill_name or not wake.hashes_full:
                continue
            if not _in_window(wake, dispatch, window_s) or wake.consumed >= wake.dispatch_capacity:
                continue
            wake.consumed += 1
            return wake, ATTRIBUTED_BY_SKILL
        return None, ATTRIBUTED_BY_SKILL
    for wake in _wakes_for_hash(wakes, dispatch, declares, window_s):
        if wake.consumed < wake.dispatch_capacity:
            wake.consumed += 1
            return wake, ATTRIBUTED_BY_HASH
    return None, ""


def dispatch_index(dispatches: list["DispatchStamp"]) -> dict[str, "DispatchStamp"]:
    """Join key -> the CONFIRM_SEND_DISPATCHED stamp that carries it.

    Only CONFIRM rows are indexed (``index_dispatches`` admits nothing else), which
    is what "prefer the CONFIRM row" means for a msgraph reply: the reply
    plugin's REPLY_SENT twin describes the same event and is never a candidate
    here. First writer wins on a duplicate key, which cannot happen on a real
    ledger (a ULID token and a vendor id are each minted once).
    """
    index: dict[str, "DispatchStamp"] = {}
    for stamp in dispatches:
        for key in stamp.join_keys:
            index.setdefault(key, stamp)
    return index


def message_attributor(
    dispatches: list["DispatchStamp"],
    wakes: list["WakeStamp"],
    declares: dict[str, "RenderDecl"],
    window_s: int,
) -> Callable[[dict], Optional[str]]:
    """Build the TRI-STATE callable ``verify_channel_bodies`` consults per
    message: ``skill_name`` | ``""`` | ``None`` (module docstring). Identity
    first (the joined row's column), hash second (the joined row's rendered
    hash against the wakes), and an identified row with neither is ``""``."""
    index = dispatch_index(dispatches)

    def attribute(message: dict) -> Optional[str]:
        stamp = next((index[key] for key in message_keys(message) if key in index), None)
        if stamp is None:
            return None
        if stamp.skill_name:
            return stamp.skill_name
        wake = wake_for_hash(wakes, stamp, declares, window_s)
        return wake.skill_name if wake is not None else ""

    return attribute


def claim_dispatch_stamp(
    dispatches: list["DispatchStamp"],
    wake: "WakeStamp",
    window_s: int,
    message: dict,
) -> Optional["DispatchStamp"]:
    """The dispatch stamp that vouches for this channel body, consumed one-to-one.

    IDENTITY FIRST: the stamp whose join keys carry the message's own id is the
    stamp, wherever in the window it sits -- the row and the message are the
    same event. Only when no row joins the message does the old PROXIMITY rule
    apply: the oldest unconsumed stamp in the window that this wake could have
    authored -- same skill, or (B3's hash pre-pass, the twin of ``claim_wake``)
    an unlabelled stamp whose rendered hash the wake stamped.

    A stamp carrying NEITHER hash is skipped rather than claimed -- there is
    nothing to compare against, and the primary check already reports that row
    as ``no_dispatch_stamp``. One stamp vouches for one body, so a single
    conformant dispatch cannot launder a run of mailbox messages.
    """
    keys = message_keys(message)
    for stamp in dispatches:
        if stamp.plain_consumed or not (stamp.plain_body_sha256 or stamp.rendered_body_sha256):
            continue
        if keys & stamp.join_keys:
            stamp.plain_consumed = True
            return stamp
    for stamp in dispatches:
        if stamp.plain_consumed or not (stamp.plain_body_sha256 or stamp.rendered_body_sha256):
            continue
        if not _in_window(wake, stamp, window_s):
            continue
        by_hash = not stamp.skill_name and (
            stamp.rendered_body_sha256 in wake.hashes_full or stamp.rendered_body_sha256 in wake.hashes_skeleton
        )
        if stamp.skill_name == wake.skill_name or by_hash:
            stamp.plain_consumed = True
            return stamp
    return None


def plain_stamp_edge(dispatches: list["DispatchStamp"]) -> Optional["datetime"]:
    """THE DEPLOY EDGE: the timestamp of the earliest dispatch row on the inbox
    that carries ``plain_body_sha256``, or None when no row does.

    What an ABSENT plain stamp means depends on when the row was written. On
    a seat whose pinned overlay predates hermes-smd-overlay#338, every row
    lacks the stamp and the send may well have been down-rendered with nothing
    recording it -- absence carries no information and the channel check must
    hold. Once the pin lands the overlay stamps every down-render and OMITS
    the key only when none ran, so absence is deliberate and the rendered hash
    is the right counterpart.

    The first version of this discriminator was a per-INBOX boolean ("any row
    on this inbox carries the stamp") on the reasoning that the overlay version
    is a property of the seat. It is -- but the seat's version CHANGES, and a
    window that spans the reprovision holds rows from both sides. Live
    2026-09-04, pilot-smokeball, ``--days 7``: the 09-01 escalator send
    (pre-reprovision, no plain stamp) shared the window with 09-03/09-04 rows
    that carried it, the boolean read the whole inbox as post-deploy, and a
    conformant send graded BODY_DIVERGED as "no down-render on this send". So
    the boundary is per ROW: rows before the edge are pre-deploy and hold,
    rows at or after it grade. The edge is the earliest stamped row because
    the overlay stamps every down-render from the moment it can; the first
    stamped row is the first row written by the new pin, and nothing before
    it could have been.
    """
    stamped = [stamp.ts for stamp in dispatches if stamp.plain_body_sha256]
    return min(stamped) if stamped else None


def stamps_plain_at(edge: Optional["datetime"], stamp: "DispatchStamp") -> bool:
    """Was the overlay that wrote THIS row one that stamps plain hashes? True
    iff an edge exists and the row is not before it. Fail-safe direction:
    no edge => never a finding on the absence path."""
    return edge is not None and stamp.ts >= edge


def attribution_counts(verdicts: list) -> dict[str, int]:
    """The two counted metrics: how many graded pairs were attributed by the
    ``skill_name`` column and how many fell through to the hash. Read off the
    verdicts' ``attribution`` field so the number is what the grader actually
    used, not a parallel tally that could drift from it. Expected trajectory on
    a seat: ``attributed_by_hash > 0`` before the overlay pin that writes the
    column, ``attributed_by_skill > 0`` and ``attributed_by_hash == 0`` after.
    """
    return {
        "attributed_by_skill": sum(1 for v in verdicts if getattr(v, "attribution", "") == ATTRIBUTED_BY_SKILL),
        "attributed_by_hash": sum(1 for v in verdicts if getattr(v, "attribution", "") == ATTRIBUTED_BY_HASH),
    }


__all__ = [
    "ATTRIBUTED_BY_HASH",
    "ATTRIBUTED_BY_SKILL",
    "AUDIT_TOKEN_KEY",
    "ID_KEY_SUBSTRING",
    "UNRESOLVED_ID_PREFIX",
    "attribution_counts",
    "claim_dispatch_stamp",
    "claim_wake",
    "dispatch_index",
    "message_attributor",
    "message_keys",
    "plain_stamp_edge",
    "stamps_plain_at",
    "wake_for_hash",
]
