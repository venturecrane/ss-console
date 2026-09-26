"""Every sentence the task review puts in front of a person, in code.

EVERY PHRASE HERE IS AUTHORED CLIENT-FACING CONTENT under the no-fabrication
policy (CLAUDE.md): the words are this file's constants, reviewed with
``references/output-format.md``, and every value is one read off the record
this run (a task label masked by ``digest_items.display_label``, a due day, a
document's kind and day, a matter number the connector projected). A value the
record does not carry renders as its authored absence, never as a guess.

No em dashes; the matter by number, never caption; no literal example numbers
in the reply line (a reader who copies an example answers an item they never
read, the escalator's rule).
"""

from __future__ import annotations

from datetime import date

#: ws2 envelope limits (overlay ``casework_finish``): longer text refuses the
#: whole envelope, so every renderer below checks its own length and falls back
#: to the label-free wording rather than risk it.
ITEM_MAX = 240
CLOSE_MAX = 160
LEAD_MAX = 600
MEMO_MAX = 600

FOOTER = (
    "Reply here in words: say yes to all, or name the numbers to leave as they are. "
    "Nothing on your task list changes until you answer. This is an internal note; no client was contacted."
)

SUGGEST = {
    "close": "Suggest: close it.",
    "keep": "Suggest: leave it open, and I won't list it again for {days} days.",
    "reassign_you": "Suggest: assign it to you.",
}

_EVIDENCE_PHRASES = {
    "proof_of_service": "a proof of service",
    "verification": "a verification",
    "records": "a records file",
}


def matter_head(number: str | None, absent: str | None) -> str:
    """``matter <number>`` or the authored absence, the escalator's wording."""
    if number:
        return f"matter {number}"
    if absent == "no_number_on_record":
        return "matter with no number on record"
    return "matter number unavailable"


def task_phrase(label: str | None, due: date, *, start: bool = True) -> str:
    """``"<label>", due <day>`` or ``a task due <day>`` when no label is safe
    (capitalized when it starts a sentence)."""
    if label:
        return f'"{label}", due {due.isoformat()}'
    return f"{'A' if start else 'a'} task due {due.isoformat()}"


def evidence_text(atoms, fallback: str | None = None) -> str | None:
    """The first document atom as words ("a proof of service dated D"), or
    ``fallback`` (e.g. the records-chase phrase) when no document shows it."""
    for atom in atoms or ():
        parts = str(atom).split(":")
        if len(parts) == 3 and parts[0] == "document" and parts[1] in _EVIDENCE_PHRASES:
            return f"{_EVIDENCE_PHRASES[parts[1]]} dated {parts[2]}"
    return fallback


def evidence_day(atoms) -> date | None:
    """The day of the first document atom, for the provenance handoff."""
    for atom in atoms or ():
        parts = str(atom).split(":")
        if len(parts) == 3 and parts[0] == "document":
            try:
                return date.fromisoformat(parts[2])
            except ValueError:
                return None
    return None


def _fit(primary: str, fallback: str, limit: int) -> str:
    return primary if len(primary) <= limit else fallback[:limit]


def item_line(label: str | None, due: date, why: str, suggest: str) -> str:
    """One numbered decision: the task, why, and the suggested call."""
    return _fit(
        f"{task_phrase(label, due)}. {why} {suggest}",
        f"{task_phrase(None, due)}. {why} {suggest}",
        ITEM_MAX,
    )


def handover_line(label: str | None, due: date, reason: str, suggest: str | None) -> str:
    """An Operator-own task it cannot finish, handed over once."""
    tail = f" {suggest}" if suggest else " It needs an owner at the firm."
    return _fit(
        f"I opened {task_phrase(label, due, start=False)} and can't finish it: {reason}.{tail}",
        f"I opened {task_phrase(None, due, start=False)} and can't finish it: {reason}.{tail}",
        ITEM_MAX,
    )


def close_line(head: str, label: str | None, due: date, evidence: str | None) -> str:
    """A task the Operator closed this run on the record's evidence."""
    why = f" ({evidence} is on file)" if evidence else ""
    return _fit(
        f"{head}: {task_phrase(label, due, start=False)}{why}",
        f"{head}: {task_phrase(None, due, start=False)}{why}",
        CLOSE_MAX,
    )


def memo_text(evidences: list[str | None]) -> str:
    """The matter memo for the tasks closed on the record's evidence (Job 3).
    Names no matter number: the memo is filed ON the matter it describes."""
    count = len(evidences)
    shown = [e for e in evidences if e]
    detail = f" ({'; '.join(shown)})" if shown else ""
    noun = "task" if count == 1 else "tasks"
    text = f"Task list upkeep: I closed {count} {noun} on this matter that the record showed were done{detail}."
    return (
        text
        if len(text) <= MEMO_MAX
        else f"Task list upkeep: I closed {count} {noun} on this matter that the record showed were done."
    )


LEAD_REVIEW = "These tasks on your matters are past due. Each has my suggested call."
LEAD_HANDOVER = "I opened these tasks and can't finish them. Each has my suggested call."


def lead_text(overflow: int, review_note: str | None, *, handover_only: bool = False) -> str | None:
    """The opening paragraph: what this list is, and how many wait for next time."""
    parts = [LEAD_HANDOVER if handover_only else LEAD_REVIEW]
    if review_note:
        parts.append(review_note)
    if overflow:
        more = "task waits" if overflow == 1 else "tasks wait"
        parts.append(f"{overflow} more {more} for the next review.")
    text = " ".join(parts)
    return text[:LEAD_MAX]


def subject_line(count: int) -> str:
    noun = "task" if count == 1 else "tasks"
    return f"[Tasks] {count} {noun} to review on your matters"
