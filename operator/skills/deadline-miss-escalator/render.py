"""Deterministic renderer for the deadline-miss escalator's triaged alert.

WHY THIS FILE EXISTS (the 2026-08-24..31 outbound review). The digest projection
(``pre_run.project_digest``) already computed every value and every count, and
the model still re-composed the WORDS each morning: format drift across runs,
field names reaching the reader, a band rendered flat, run-on paragraphs. The
durable fix removes the model from routine-email composition: this module
renders ``references/output-format.md`` literally, in code, from the projected
digest — the turn composes nothing.

EVERY PHRASE HERE IS AUTHORED CLIENT-FACING CONTENT under the no-fabrication
policy (CLAUDE.md): the words come from this file's constants (reviewed
template text mirroring ``references/output-format.md``) and the values come
from the digest projection, which reads them off the firm's own records. An
unknown signal renders NOTHING — never a sentinel, never invented urgency.

No em dashes anywhere; matter by number, never caption; plain words, never a
citation (the law-seat first-draft rules; the spec gate still checks the
rendered text).

Stdlib only, loaded by absolute path from ``pre_run.py`` like the vendored
ledger (the scheduler may stage ``pre_run.py`` alone; the skill dir on the
volume carries the siblings).
"""

from __future__ import annotations

import hashlib
import re


# ---------------------------------------------------------------------------
# canonical_body_sha256 — ONE hash function, one arbiter (the cross-workstream
# contract). sha256 over utf-8 of (CRLF -> LF, per-line trailing whitespace
# stripped, trailing newlines stripped). Stamped on the dispatch envelope +
# EMITTED_WAKE here; the overlay's CONFIRM_SEND_DISPATCHED stamp and the
# console verifier (operator/bin/lib/send_verify.py) compute the SAME
# function. The arbiter fixture every implementation is tested against is
# operator/contracts/fixtures/body-canon-vectors.json — change the definition
# nowhere without changing it everywhere, proven by the shared vectors.
# ---------------------------------------------------------------------------


def canonical_body_sha256(text: str) -> str:
    """The canonical body hash: CRLF->LF, per-line trailing whitespace
    stripped, trailing newlines stripped, sha256 over utf-8."""
    normalized = text.replace("\r\n", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    canonical = "\n".join(lines).rstrip("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The signal -> phrase map (output-format.md rule 1, as computable here).
#
# CLOSED SET. Only signals the projection detects IN CODE render a consequence
# line; the rich phrases output-format.md once suggested (deemed-admission,
# disbursement blocker, opposing-counsel held) have no code-detectable
# authored source and therefore render NOTHING — deleting them from the
# model's vocabulary is the point. Overdue age gets no extra line: the
# overdue-by-N is already in the item line, and rule 1 says most-overdue is
# the plain default.
# ---------------------------------------------------------------------------

#: Authored client-facing phrases (no-fabrication policy). Keyed by the
#: projection's ``priority_marker`` value, which ``parse_pull`` extracts from
#: the Smokeball task subject's own words.
_PRIORITY_PHRASES = {
    "CRITICAL": "the task is marked CRITICAL in Smokeball",
    "URGENT": "the task is marked URGENT in Smokeball",
    "HIGH PRIORITY": "the task is marked HIGH PRIORITY in Smokeball",
}

_COURT_DATE_PHRASE = "a court date the firm authored"


def consequence_line(item: dict) -> str | None:
    """The one plain line of why an item is consequential, or None.

    Authored signal only: a task-priority marker the record carries, else the
    court-date label. Anything else renders nothing (rule 7: no invented
    urgency)."""
    marker = item.get("priority_marker")
    if isinstance(marker, str) and marker in _PRIORITY_PHRASES:
        return _PRIORITY_PHRASES[marker]
    if item.get("label") == "court-date":
        return _COURT_DATE_PHRASE
    return None


# ---------------------------------------------------------------------------
# Value formatting — every helper renders a READ value or a typed absence.
# ---------------------------------------------------------------------------


def _matter_head(item_or_group: dict) -> str:
    """``matter <number>``, or the exact authored absence phrase.

    ``no_number_on_record`` is authored absence (the firm's record carries no
    number); every other absence is a resolution failure and renders the
    generic phrase. Never a GUID, never a supplied value (ss #2390)."""
    number = item_or_group.get("matter_number")
    if isinstance(number, str) and number:
        return f"matter {number}"
    if item_or_group.get("matter_number_absent") == "no_number_on_record":
        return "no number on record"
    return "matter number unavailable"


def _due_phrase(days_out: int) -> str:
    if days_out < 0:
        n = -days_out
        return f"overdue by {n} day" + ("" if n == 1 else "s")
    if days_out == 0:
        return "due today"
    return f"due in {days_out} day" + ("" if days_out == 1 else "s")


def _item_line(item: dict) -> str:
    """``<matter head>, <label> <date> (<due phrase>)`` — the shared core of a
    needs-you and blanket line. Values verbatim from the digest item."""
    return (
        f"{_matter_head(item)}, {item.get('label')} {item.get('authored_date')} "
        f"({_due_phrase(int(item.get('days_out') or 0))})"
    )


def _day_of(ts: str | None) -> str | None:
    """The YYYY-MM-DD day of an ISO timestamp, or None. The under-active band
    renders the day the Operator last raised, which is what the handoff seeds."""
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    day = ts[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day
    return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# ---------------------------------------------------------------------------
# The rekey notice (Q6 — ACK identity correction, authored, self-extinguishing)
# ---------------------------------------------------------------------------

_REKEY_NOTICE = (
    "Item identity was corrected for {n} calendar item{s}; previously "
    "acknowledged items may resurface once and now clear only with a blanket "
    "acknowledgment."
)


# ---------------------------------------------------------------------------
# render_digest — references/output-format.md, literally.
# ---------------------------------------------------------------------------


def render_digest(
    digest: dict,
    *,
    ack_snooze_days: int,
    rekey_count: int = 0,
) -> str:
    """Render the projected digest into the triaged alert body.

    The digest supplies the VALUES and the MEMBERSHIP; this function supplies
    the WORDS and the MARKUP (output-format.md). Counts are copied, never
    recomputed; empty sections are omitted whole (rule 9); the footer is a
    sibling of the lists, never nested (2026-08-14). The subject is NOT
    included — the caller sends it as the message subject, verbatim from
    ``digest['subject']``."""
    lines: list[str] = []
    if rekey_count > 0:
        lines.append(_REKEY_NOTICE.format(n=rekey_count, s="" if rekey_count == 1 else "s"))
        lines.append("")

    needs_you = digest.get("needs_you") or []
    lines.append(f"## Needs you today ({len(needs_you)})")
    lines.append("")
    if needs_you:
        lines.append("Ranked by what the record says, most consequential first.")
        lines.append("")
        for index, item in enumerate(needs_you, start=1):
            code = item.get("ack_code")
            suffix = f" [{code}]" if code else ""
            lines.append(f"{index}. {_item_line(item)}{suffix}")
            reason = consequence_line(item)
            if reason:
                lines.append(f"   {reason}")
        lines.append("")

    admin = digest.get("admin_confirms")
    if isinstance(admin, dict) and admin.get("matters"):
        total = int(admin.get("total") or 0)
        matter_count = int(admin.get("matter_count") or 0)
        lines.append(f"## Admin confirms ({total} across {matter_count} matters)")
        lines.append("")
        lines.append(
            "Routine confirmations, collapsed per matter. Reply with a matter's "
            "ACK codes to clear its items, or open the item in Smokeball."
        )
        lines.append("")
        for group in admin["matters"]:
            count = int(group.get("count") or 0)
            codes = " ".join(f"[{c}]" for c in (group.get("ack_codes") or []))
            line = f"- {_matter_head(group)}: {_plural(count, 'routine confirmation')}."
            if codes:
                line += f" {codes}"
            lines.append(line)
        lines.append("")

    elsewhere = digest.get("under_active_escalation_elsewhere")
    if isinstance(elsewhere, dict) and elsewhere.get("matters"):
        total = int(elsewhere.get("total") or 0)
        matter_count = int(elsewhere.get("matter_count") or 0)
        lines.append(f"## Under active escalation elsewhere ({total} across {matter_count} matters)")
        lines.append("")
        lines.append("Already raised, shown so it is not double-counted. No action here.")
        lines.append("")
        for group in elsewhere["matters"]:
            count = int(group.get("count") or 0)
            raised = _day_of(group.get("last_raised"))
            tail = f" (last raised {raised})" if raised else ""
            lines.append(f"- {_matter_head(group)}: {_plural(count, 'item')} under active escalation{tail}.")
        lines.append("")

    clearance = digest.get("awaiting_clearance") or []
    if clearance:
        lines.append(f"## Awaiting clearance ({len(clearance)})")
        lines.append("")
        lines.append(
            "Held matters with an approaching date. Surfaced for a person to clear; never a client-facing step."
        )
        lines.append("")
        for item in clearance:
            lines.append(
                f"- {_matter_head(item)}: on CONFLICT-HOLD with "
                f"{item.get('label')} {item.get('authored_date')} approaching."
            )
        lines.append("")

    blanket = digest.get("blanket_ack_only") or []
    if blanket:
        lines.append(f"## Blanket-ack only ({len(blanket)})")
        lines.append("")
        lines.append(
            "Items with no stable task id, so they carry no individual ACK "
            "code. A blanket acknowledgement (below) acks exactly the items "
            "quoted here."
        )
        lines.append("")
        for item in blanket:
            lines.append(f"- {_item_line(item)}.")
        lines.append("")

    # The single footer, a SIBLING of the lists (rule 4; the 2026-08-14 HTML
    # rendered it as a list child).
    lines.append(
        "Reply with the ACK code(s) above to acknowledge. Reply "
        "ESCALATION_ACKNOWLEDGED to ack every item quoted in this message; "
        f"items you do not quote stay open. An acked item goes quiet for "
        f"{ack_snooze_days} days, then re-surfaces if it is still open in "
        "Smokeball. Completing the item in Smokeball is the only thing that "
        "closes it. This is an internal alert to a person at the firm; no "
        "client message has been sent."
    )

    probe = digest.get("probe_artifacts")
    if isinstance(probe, dict) and (probe.get("excluded") or probe.get("stale")):
        lines.append("")
        excluded = int(probe.get("excluded") or 0)
        stale_ids = [str(x) for x in (probe.get("stale_task_ids") or [])]
        note = f"Probe artifacts excluded from this digest: {excluded}."
        if stale_ids:
            note += " Stale probe task ids awaiting teardown: " + ", ".join(stale_ids) + "."
        lines.append(note)

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# render_skeleton — the fallback body: counts only, ZERO identifiers and ZERO
# dates, so it passes every gate on a run where the full body cannot.
# ---------------------------------------------------------------------------


def render_skeleton(digest: dict) -> str:
    """Authored minimal body. Identifier-free by construction: no matter
    numbers, no dates, no ACK codes, no task ids — only counts. Tested by
    regex assertion in test_render.py."""
    needs_you = len(digest.get("needs_you") or [])
    admin = digest.get("admin_confirms") or {}
    admin_total = int(admin.get("total") or 0)
    lines = [
        "## Deadline digest (details unavailable)",
        "",
        f"{_plural(needs_you, 'item')} need a person now and "
        f"{_plural(admin_total, 'routine confirmation')} are tracked, but the "
        "detailed digest could not be delivered this run. Open Smokeball or "
        "the tracker view for the items; the next run will retry the full "
        "digest.",
        "",
        "This is an internal alert to a person at the firm; no client message has been sent.",
    ]
    return "\n".join(lines) + "\n"


#: The one-line failure note the turn may send when its Script Output shows
#: ``dispatch_expected: true`` and no dispatch note was injected (the deploy
#: skew window), or on a delivery fault. Authored, no slots; the in-turn
#: rendered-body check accepts exactly this text.
FAILURE_NOTE = (
    "The deadline digest run failed and needs attention; no digest was "
    "delivered this run. The items are in Smokeball and the tracker view."
)

#: Subject for the failure note when the GATE dispatches it out of turn rather
#: than instructing the turn to send it (2026-09-02). Deliberately carries no
#: date and no counts: the run that sends this could read nothing, so every
#: number in it would be invented, and a date in a deadline subject line is the
#: one thing a reader is most likely to mistake for a deadline.
FAILURE_NOTE_SUBJECT = "[Deadlines] run failed, no digest delivered"
