"""The hourly triage's next step for a drafting job that ended held or failed,
by the leading code of its reason (``runners/jobs/jobs/drafting/outcome.py``
REASON_CODES; who owns each and how it ends is ``drafting/endings.py``).

Every drafting reason code has a row here: ``runners/jobs/tests/
test_drafting_endings.py`` fails when one is missing or its class disagrees
with the code's ending. A code with no row falls back to the generic line in
``work_health.py``. Pure data; no import of the runner, which work-health
does not ship with.
"""

from __future__ import annotations

from typing import Optional

LANE = "drafting"

#: Triage class -> the line the triage email prints as the next step.
STEPS: dict[str, str] = {
    "smd_resume": (
        "SMD resolves what the job names (smd-needs.json or its reason), then resumes it; "
        "nothing reaches the requester until then."
    ),
    "smd_fix": "Our machinery failed and does not resume: find the cause, fix it in code, then run the job again.",
    "smd_hand": "Outside what the Operator drafts today: SMD drafts it by hand, or tells the requester.",
    "smd_check": (
        "It held or failed on our side: read the reason on the seat, fix the cause, then rerun or tell the requester."
    ),
    "firm_told": "Held, and the requester was told why: confirm the reply went, then close it.",
    "smd_noted": "Delivered with a part left for SMD and named in the notes: finish that part.",
    # The requester is never asked (no client loops, 2026-11-23): SMD settles it.
    "smd_request": (
        "Held: code could not tell what to draft from the request. Read the request and the reason on the seat, "
        "then queue it again with what it needs, or draft it by hand; the firm hears only the delivery."
    ),
}

#: Reason code -> triage class.
TRIAGE: dict[str, str] = {
    "request_incomplete": "smd_request",
    "gate_refused": "smd_check",
    "format_check": "smd_resume",
    "record_unreadable": "smd_resume",
    "audit_unsettled": "smd_resume",
    "audit_unrepaired": "smd_resume",
    "notes_unclean": "smd_resume",
    "destination_mismatch": "firm_told",
    "destination_unauthored": "firm_told",
    "no_readable_documents": "firm_told",
    "filing_refused": "smd_check",
    "stage_unfinished": "smd_resume",
    "limit": "smd_resume",
    "no_verdict": "smd_fix",
    "unexpected": "smd_resume",
    "config_missing": "smd_resume",
    "caption_restore_incomplete": "smd_fix",
    "review_held": "smd_resume",
    "readback_mismatch": "smd_fix",
    "held_for_smd": "smd_resume",
    "markup_lint": "smd_resume",
    "check_invalid": "smd_resume",
    "length_limit": "smd_resume",
    "revise_base_unproven": "smd_resume",
    "revise_unchecked": "smd_resume",
    "revise_layout_lost": "smd_resume",
    "revise_unchanged": "firm_told",
    "firm_base_unfit": "smd_resume",
    "firm_base_lost": "smd_resume",
    "party_unknown": "smd_resume",
    "party_mismatch": "smd_resume",
    "attorney_not_rostered": "firm_told",
    "roster_unreadable": "smd_resume",
    "served_unknown": "smd_resume",
    "served_other_set": "smd_resume",
    "served_invalid": "smd_resume",
    "served_none": "smd_resume",
    "served_scan": "smd_resume",
    "served_ambiguous": "smd_resume",
    "served_unnumbered": "smd_resume",
    "served_many": "smd_resume",
    "served_prior_draft": "smd_hand",
    "served_amended": "smd_hand",
    "form_incomplete": "smd_resume",
    "form_unconfirmed": "smd_resume",
    "policy_missing": "smd_resume",
    "rule_unplaced": "smd_resume",
    "request_too_large": "smd_resume",
    "request_door_unopenable": "smd_resume",
    "rule_unchecked": "smd_resume",
    "rule_unmet": "smd_resume",
    "rule_changed": "smd_resume",
    "phrase_missing": "smd_noted",
    "exhibit_unresolved": "smd_noted",
    "index_unpaired": "smd_resume",
    "challenge_unread": "smd_resume",
    "prior_draft_routed": "firm_told",
}


def next_step(lane: str, code: Optional[str]) -> Optional[str]:
    """The triage line for a drafting job's reason code; None for another lane
    or a code with no row (the caller prints its generic line)."""
    if lane != LANE or not code:
        return None
    cls = TRIAGE.get(code)
    return STEPS[cls] if cls else None
