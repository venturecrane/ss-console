"""The daemon's side of proven delivery: a delivered outcome is believed only
with its proof block.

A verdict saying ``delivered`` is the runner's claim. The block it must carry::

    "proof": {
        "readback": "confirmed",          # the far end re-read (readback.py)
        "files": [{name, file_id, size, sha256}]   # what was proven there,
          | "rows": N                              # or rows written and re-read,
          | "nothing_to_file": true,               # or a run with nothing to file
        "already_current": {...},         # a chronology update that found nothing
                                          # new: readback "nothing_filed", no file
        "review": "passed" | "released" | "code_only",
        "review_cents": 123,              # what the review cost (P7)
        "review_model": "model-id",       # "" for a code-only review
        "alerts": ["review_held", ...],   # optional: codes from CODES to page
    }

EVERY delivered outcome is checked, not the worst-ranked one: a chronology job
carries one outcome per unit, and the daemon's rank puts every delivered unit
level, so checking only the one it picks would let an unproven unit ride on a
proven sibling (critique of the plan, 2026-10-13).
"""

from __future__ import annotations

from typing import Any

from . import PROOF_KEY, REVIEW_PASSED

#: The closed vocabulary of proof-failure codes. Each is a wire token the
#: overlay heartbeat carries as ``proof:<code>`` (nothing else of a job reaches
#: the wire); the detail stays in the seat's own audit row.
CODES = frozenset(
    {
        "proof_missing",  # a delivered verdict without a proof block
        "readback_unproven",  # proof block present, read-back not confirmed
        "readback_mismatch",  # the far end's bytes / rows are not ours
        "review_missing",  # delivered with no review status
        "review_held",  # the pre-delivery review held the job
        "dry_run_reported",  # a dry run reached the daemon as a verdict
        "wake_lost",  # the DELIVER turn could not be started
        "reply_unproven",  # the requester's reply was not seen in Sent Items
        "reply_not_in_sent_items",  # the broker sent it and could not find it
        # A HELD job whose hold is ours (proof/holds.py), 2026-10-13: the firm is
        # told SMD is finishing it, so SMD must actually hear.
        "gate_held",  # a gate, a seed value or an unclassified hold stopped it
        "update_held",  # an update into the delivered chronology could not go on
        "upload_held",  # filing or retiring on the matter did not finish
        "stage_refused",  # a stage refused its own input
        "limit_held",  # a cost cap, budget or page allowance held it
        "identity_held",  # a record names another person; the firm is asked
    }
)


def problem(outcome: dict[str, Any]) -> tuple[str, str] | None:
    """``(code, detail)`` when one delivered outcome is not proven, else None."""
    proof = outcome.get(PROOF_KEY)
    unit = str(outcome.get("unit") or outcome.get("kind") or "job")
    if not isinstance(proof, dict):
        return "proof_missing", f"{unit}: the verdict says delivered and carries no proof block"
    if "already_current" in proof or proof.get("readback") == "nothing_filed":
        return _current(unit, proof)
    if proof.get("readback") != "confirmed":
        return "readback_unproven", f"{unit}: read-back {proof.get('readback')!r}, not confirmed"
    if not (proof.get("files") or int(proof.get("rows") or 0) > 0 or proof.get("nothing_to_file")):
        return "readback_unproven", f"{unit}: the proof names nothing it read back"
    if proof.get("review") not in REVIEW_PASSED:
        return "review_missing", f"{unit}: review {proof.get('review')!r}"
    return None


def _current(unit: str, proof: dict[str, Any]) -> tuple[str, str] | None:
    """An already-current update (2026-10-25): it adds nothing to the
    delivered chronology and files one thing, its records review, beside it.
    Believed ONLY with its outcome record's summary showing no entry and no
    exhibit added and every document accounted for, and with the records
    review the one file filed, proven by hash (an earlier runner that filed
    nothing, readback "nothing_filed", is still believed); anything else is an
    unproven delivery."""
    cur = proof.get("already_current")
    if not isinstance(cur, dict) or cur.get("kind") != "already_current":
        return "readback_unproven", f"{unit}: nothing was filed and no outcome record proves nothing new"
    files = proof.get("files") or []
    review = str(cur.get("review_file") or "")
    if int(proof.get("rows") or 0) or (
        [str(f.get("name") or "") for f in files] != [review]
        if files
        else proof.get("readback") != "nothing_filed" or review
    ):
        return "readback_unproven", f"{unit}: already current, yet the proof names a file other than its records review"
    if files and (proof.get("readback") != "confirmed" or not all(f.get("file_id") and f.get("sha256") for f in files)):
        return "readback_unproven", f"{unit}: already current, yet its records review is not proven by hash"
    if cur.get("entries_added") != 0 or cur.get("exhibits_added") != 0:
        return "readback_unproven", f"{unit}: already current, yet the update added entries or exhibits"
    raw = cur.get("by_reason")
    by: dict[str, Any] = raw if isinstance(raw, dict) else {}
    counted = sum(int(v.get("files") or 0) for v in by.values() if isinstance(v, dict))
    if counted != cur.get("files") or int(cur.get("files") or 0) < int(cur.get("requested") or 0):
        return "readback_unproven", f"{unit}: already current, yet its counts do not account for every document"
    if {"unaccounted", "selected_to_read"} & set(by):
        return "readback_unproven", f"{unit}: already current, yet a document was unaccounted for or selected"
    if proof.get("review") not in REVIEW_PASSED:
        return "review_missing", f"{unit}: review {proof.get('review')!r}"
    return None


def check(outcomes: list[Any]) -> tuple[str, str] | None:
    """The first unproven delivered outcome's ``(code, detail)``, else None."""
    for o in outcomes:
        if isinstance(o, dict) and str(o.get("outcome") or o.get("verdict")) == "delivered":
            p = problem(o)
            if p:
                return p
    return None


def review_cents(outcomes: list[Any]) -> int:
    """The review spend the outcomes carry, in cents (already inside the job's
    total; recorded separately so the review's cost is visible per job)."""
    total = 0
    for o in outcomes:
        proof = o.get(PROOF_KEY) if isinstance(o, dict) else None
        if isinstance(proof, dict):
            try:
                total += max(0, int(proof.get("review_cents") or 0))
            except (TypeError, ValueError):
                continue
    return total


__all__ = ["CODES", "check", "problem", "review_cents"]
