"""A job that stopped tells SMD, never the firm (Captain, 2026-11-06).

THE CALL. While a lane is still being refined and watched, a job of a kind in
``SILENT_KINDS`` that held, failed or waited on its lane's proof run sends the
requester NOTHING: no note, no held reply, no word from the model. SMD is paged
instead (the runner's ``jobs/job_notes.tell``, one ``job_stopped`` alert per
``attempt:state``, naming the state, the class below and the job's reason).

This reverses the same day's #3816, which sent the attorney a fixed "we are
checking it" note for these states. The Captain's words: SMD should know, the
firm should not. Only a delivered job's reply reaches the requester.

WHERE IT BITES. ``reply_binding._verify_job`` refuses the bind for every such
state (failed: ``job_failed``, waiting: ``not_ended``, held: ``job_held``), so
neither the gateway's DELIVER turn nor the broker's own ``job_note_send`` can
send one; the seat's ``job_reply`` treats those codes as SMD's already, not as
an undelivered reply. Kept out of ``reply_binding.py``, which sits at its
module size ceiling.

HOW A KIND LEAVES. Remove it from ``SILENT_KINDS`` and its held reply binds as
it did before 2026-11-06 (drafting: ``delivered_matter.drafting``'s held words;
litigation: its hold class's fixed wording); failed
and waiting jobs were never replied on before #3816 either way.
"""

from __future__ import annotations

from typing import Any

#: The job kinds whose stopped states the requester is told nothing about.
#: Litigation joined the same day (the Captain's same words), its held
#: replies included: until then a held list was replied on in its hold
#: class's fixed wording.
SILENT_KINDS = frozenset({"drafting_job", "litigation_job"})

#: A drafting hold whose cause is in the request or the file: SMD's page says
#: so, because the firm would have been asked for it (``delivered_matter.drafting``).
DRAFTING_FIRM_HOLDS = frozenset({"request_incomplete", "no_readable_documents", "record_unreadable", "gate_refused"})
#: The reason a job waiting on its lane's proof run carries (``lane_proof.HOLD_PREFIX``).
LANE_UNPROVEN = "lane_unproven"
#: A failed drafting job whose draft exists and the review held with nothing
#: filed (the lane records ``failed``, since its ``held`` is final).
DRAFTING_CHECKING = ("review_held", "refused: review_held")


def _drafting(row: dict[str, Any]) -> str | None:
    state = row.get("state")
    reason = str(row.get("reason") or "").strip()
    if state == "failed":
        return "review_held" if reason.startswith(DRAFTING_CHECKING) else "failed"
    if state == "submitted" and reason.startswith(LANE_UNPROVEN):
        return "waiting"
    if state == "held":
        if row.get("revise_of"):
            return "held_revision"
        return "held_firm_cause" if reason.split(":", 1)[0].strip() in DRAFTING_FIRM_HOLDS else "held"
    return None


#: A litigation hold whose class is the firm's answer (``hold_class``): which
#: staff, whose files, or a scope with no case. SMD's page says so, because the
#: firm was asked for it before 2026-11-06 and is not now.
LITIGATION_FIRM_HOLDS = frozenset({"nothing_in_scope", "staff_unresolved", "requester_unresolved"})


def _litigation(row: dict[str, Any]) -> str | None:
    from . import hold_class

    state = row.get("state")
    reason = str(row.get("reason") or "").strip()
    if state == "failed":
        return "failed"
    if state == "submitted" and reason.startswith(LANE_UNPROVEN):
        return "waiting"
    if state == "held":
        cls = hold_class.classify("litigation", reason)[0]
        return "held_firm_cause" if cls in LITIGATION_FIRM_HOLDS else "held"
    return None


_CLASSIFIERS = {"drafting_job": _drafting, "litigation_job": _litigation}


def stop_class(kind: str, row: dict[str, Any] | None) -> str | None:
    """The class SMD's page names for a silent kind's stopped job, or None
    (the job has not stopped, or its kind is not silent)."""
    if kind not in SILENT_KINDS or not row:
        return None
    return _CLASSIFIERS[kind](row)


def held_silent(kind: str, row: dict[str, Any]) -> bool:
    """A held job whose requester is told nothing (the bind refuses ``job_held``)."""
    return row.get("state") == "held" and stop_class(kind, row) is not None


def refusal(kind: str, noun: str, ident: str, row: dict[str, Any], replyable: frozenset[str]) -> tuple[str, str] | None:
    """``(message, code)`` the bind refuses with: a failed job of any kind (as
    since before 2026-11-06), or a silent kind's held job; None otherwise. A
    held chronology keeps its own branch in ``reply_binding``."""
    if row.get("state") == "failed":
        return (
            f"{noun} job {ident} failed on SMD's side; the requester is told nothing until it is "
            f"{' or '.join(sorted(replyable))}, and SMD has been alerted. Send nothing to anyone.",
            "job_failed",
        )
    if held_silent(kind, row):
        return (
            f"{noun} job {ident} stopped and SMD is finishing it; the requester hears nothing until it is "
            "delivered, and SMD has been alerted. Send nothing to anyone.",
            "job_held",
        )
    return None


__all__ = [
    "DRAFTING_CHECKING",
    "DRAFTING_FIRM_HOLDS",
    "LANE_UNPROVEN",
    "LITIGATION_FIRM_HOLDS",
    "SILENT_KINDS",
    "held_silent",
    "refusal",
    "stop_class",
]
