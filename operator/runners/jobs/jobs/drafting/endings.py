"""Every drafting reason code, and what it means for the people around the job.

One row per code in ``outcome.REASON_CODES``: who owns what happens next
(``smd`` or ``firm``), how the job ends (``held``: final, the firm is told;
``failed``: our machinery, SMD alerted; ``delivered``: filed with a part left
and named in the notes), whether SMD can resume it, and the key of what the
firm is told (``FIRM_MESSAGES``). The hourly triage's line for each code is
``operator/bin/lib/work_health_drafting.py``; ``tests/test_drafting_endings.py``
holds the three tables to one another, so a code cannot be added without all
three.

Notes lines for a delivered ending come from ``FIRM_MESSAGES`` by code, never
from model text.
"""

from __future__ import annotations

from dataclasses import dataclass

from .outcome import REASON_CODES

OWNERS = ("smd", "firm")
ENDS = ("held", "failed", "delivered")


@dataclass(frozen=True)
class Ending:
    owner: str
    ends: tuple[str, ...]
    resumable: bool
    message: str


#: What the firm is told, per message key. ``none``: nothing (our machinery,
#: SMD alerted). ``relay``: the reply relays the sentence after the reason code.
#: The rest are attorney-notes lines, with ``{item}`` filled by code.
FIRM_MESSAGES: dict[str, str] = {
    "none": "",
    "relay": "",
    "phrase_missing": (
        "{item}: left as drafted and marked for you; the firm's standard wording has no sentence for this objection"
    ),
    "exhibit_unresolved": "{item}: the exhibit it names could not be matched to a file, so it is left as drafted",
    "prior_draft_routed": (
        "{item}: a draft of these responses is already on the matter; reply with the changes you want made to it"
    ),
}


def _f(resumable: bool = True) -> Ending:
    """Our machinery: failed, SMD alerted, the firm told nothing."""
    return Ending("smd", ("failed",), resumable, "none")


def _held() -> Ending:
    """The file or the request needs a person: held, final, the reply says why."""
    return Ending("firm", ("held",), False, "relay")


ENDINGS: dict[str, Ending] = {
    "request_incomplete": _held(),
    "gate_refused": Ending("smd", ("held", "failed"), False, "relay"),
    "format_check": _f(),
    "record_unreadable": _f(),
    "audit_unsettled": _f(),
    "audit_unrepaired": _f(),
    "notes_unclean": _f(),
    "destination_mismatch": _held(),
    "destination_unauthored": _held(),
    "no_readable_documents": _held(),
    "filing_refused": Ending("smd", ("held", "failed"), False, "relay"),
    "stage_unfinished": _f(),
    "limit": _f(),
    "no_verdict": _f(False),
    "unexpected": _f(),
    "config_missing": _f(),
    "caption_restore_incomplete": _f(False),
    "review_held": _f(),
    "readback_mismatch": _f(False),
    "held_for_smd": _f(),
    "markup_lint": _f(),
    "check_invalid": _f(),
    "length_limit": _f(),
    "revise_base_unproven": _f(),
    "revise_unchecked": _f(),
    "revise_layout_lost": _f(),
    "revise_unchanged": _held(),
    "firm_base_unfit": _f(),
    "firm_base_lost": _f(),
    "party_unknown": _f(),
    "party_mismatch": _f(),
    "attorney_not_rostered": _held(),
    "roster_unreadable": _f(),
    "served_unknown": _f(),
    "served_other_set": _f(),
    "served_invalid": _f(),
    "served_none": _f(),
    "served_scan": _f(),
    "served_ambiguous": _f(),
    "served_unnumbered": _f(),
    "served_many": _f(),
    "served_prior_draft": _f(),
    "served_amended": _f(),
    "form_incomplete": _f(),
    "form_unconfirmed": _f(),
    "policy_missing": _f(),
    "rule_unplaced": _f(),
    "request_too_large": _f(),
    # The firm-rules stage (rulebook/): its item grid refused after a retry; a clause
    # code can make still unmet; the rule store moved twice during the job.
    "rule_unchecked": _f(),
    "rule_unmet": _f(),
    "rule_changed": _f(),
    # Filed with the response left as drafted and named in the notes, SMD paged.
    "phrase_missing": Ending("smd", ("delivered",), False, "phrase_missing"),
    "exhibit_unresolved": Ending("smd", ("delivered",), False, "exhibit_unresolved"),
    # The index recompute could not pair the index file with its responses.
    "index_unpaired": _f(),
    # The other side's letter names no request code can verify (meetconfer/).
    "challenge_unread": _f(),
    # A set with a hand-filed draft goes to revision; the reply names the draft.
    "prior_draft_routed": Ending("firm", ("delivered",), False, "prior_draft_routed"),
}


def ending(code: str) -> Ending:
    return ENDINGS[code]


def problems() -> list[str]:
    """Why the table does not cover ``outcome.REASON_CODES`` exactly, or a row is malformed."""
    out = [f"{c}: no ending" for c in REASON_CODES if c not in ENDINGS]
    out += [f"{c}: not a reason code" for c in ENDINGS if c not in REASON_CODES]
    for c, e in ENDINGS.items():
        if e.owner not in OWNERS or not e.ends or not set(e.ends) <= set(ENDS):
            out.append(f"{c}: owner or ends outside {OWNERS} / {ENDS}")
        if e.message not in FIRM_MESSAGES:
            out.append(f"{c}: message key {e.message!r} not in FIRM_MESSAGES")
        if "delivered" in e.ends and not FIRM_MESSAGES.get(e.message):
            out.append(f"{c}: a delivered ending needs a notes line")
    return out
