"""A defendant's status as its own words state it: by rule, never by prefix order.

2026-11-02 (the A&P ops workspace): three defendants on two cases printed
"Served, no answer in file", one for 3,430 days, beside a note that read
"default entered <date>". The seed converter matched a hand run's status text
by its FIRST words, with "served" ahead of "default", so "served 3/1/2017;
default entered 1/5/2018" became served-and-no-answer; the default lived on
only in the note, and no gate compared the two.

``stated`` reads a status text or a note for the states that outrank service
(dismissed, settled, default entered, answered) and returns the one it states,
whatever order the words come in. A negated or merely requested state is not a
stated one: "no answer", "default not entered", "request for entry of default",
"motion to dismiss", "default set aside" state nothing here.

``reconcile`` applies a defendant's notes to a status that says there is no
answer, in place, so the firm reads the specific status with the note that
gave it instead of a list held unread. ``contradiction`` is what the gate
(``gates._defendant_rules``) refuses: a no-answer status beside a note that
states otherwise, which ``reconcile`` has not settled.
"""

from __future__ import annotations

import re
from typing import Any

from . import vocab

#: A word that, in the same clause just before a state, means the state did
#: not happen (or has not yet): "no", "not", "never", "request for", a motion,
#: a modal ("will be dismissed"), a condition ("until default is entered").
_NEGATOR = re.compile(
    r"(?i)\b(?:no|not|never|without|request(?:ed|s|ing)?|motion|application|seek(?:s|ing)?|intend(?:s|ed)?|"
    r"will|would|may|might|should|could|can|if|until|unless|pending|before|whether|awaiting)\b"
)
#: How far back in the clause a negator is looked for.
_WINDOW = 40
_CLAUSE_END = re.compile(r"[.;|\n]")

_PATTERNS: dict[str, re.Pattern[str]] = {
    vocab.D_DISMISSED: re.compile(r"(?i)\bdismissed\b|\bdismissal (?:was |has been |been )?(?:entered|granted)\b"),
    vocab.D_SETTLED: re.compile(r"(?i)\bsettled\b"),
    vocab.DEFAULT: re.compile(
        r"(?i)\bdefault(?: judgment)? (?:was |has been |been |is )?(?:entered|granted)\b"
        r"|\bentered (?:a |the |his |her |its |their )?default\b|\bdefault judgment\b|\bdefaulted\b|\bin default\b"
    ),
    vocab.ANSWERED: re.compile(
        # "answered the interrogatories" is discovery, not an answer to the complaint.
        r"(?i)\banswered\b(?!\s+(?:the\s+|our\s+|its\s+|plaintiff'?s?\s+)?(?:discovery|interrogator|request|rfa|rog|special|form|question))"
        r"|\bfiled (?:an |its |his |her |their |the )?(?:verified |general )?answer\b"
        r"|\banswer (?:was |has been |been )?(?:filed|served)\b"
    ),
}
#: A default that was undone: it is not the defendant's status now.
_DEFAULT_UNDONE = re.compile(
    r"(?i)\bdefault\b[^.;|\n]{0,60}\b(?:set aside|vacated|lifted)\b|\b(?:set aside|vacated)\b[^.;|\n]{0,30}\bdefault\b"
)
#: A note's state is its finding only when its date follows it in the same
#: clause: "default entered 2018-01-05", "Dismissed without prejudice
#: 2019-04-02". A note's prose names states it
#: does not find (2026-11-06, six of nine on one list: "the answered date stays
#: null", "asked that the County be dismissed", "her Estate ... answered
#: instead", "plaintiff answered" discovery).
_DATED = re.compile(r"[^.;|\n\d]{0,25}?(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
#: An answer the plaintiff gave is never the defendant's answer to the complaint.
_PLAINTIFF = re.compile(r"(?i)\bplaintiffs?\b")
_ORIGINAL_ONLY = re.compile(r"(?i)\boriginal complaint\b")
_AMENDED = re.compile(r"(?i)\bamended\b")


def _affirmed(rx: re.Pattern[str], text: str, dated: bool = False) -> bool:
    """``rx`` matches somewhere in ``text`` with no negator before it in its
    clause (and, when ``dated``, its date right after it)."""
    for m in rx.finditer(text):
        if dated and not _DATED.match(text, m.end()):
            continue
        before = text[max(0, m.start() - _WINDOW) : m.start()]
        cut = list(_CLAUSE_END.finditer(before))
        if cut:
            before = before[cut[-1].end() :]
        if not _NEGATOR.search(before) and not (rx is _PATTERNS[vocab.ANSWERED] and _PLAINTIFF.search(before)):
            return True
    return False


def states(text: Any, dated: bool = False) -> set[str]:
    """Every outranking state ``text`` affirms (``vocab`` defendant statuses)."""
    s = str(text or "")
    found = {status for status, rx in _PATTERNS.items() if _affirmed(rx, s, dated)}
    if vocab.DEFAULT in found and _DEFAULT_UNDONE.search(s):
        found.discard(vocab.DEFAULT)
    return found


def stated(text: Any, dated: bool = False) -> str | None:
    """The one status ``text`` states, by rule: a dismissal outranks a
    settlement, and either outranks a default or an answer (a defendant is
    defaulted or answers first, and is let go after). A default AND an answer
    with neither undone is two findings at odds: ``vocab.D_UNCLEAR``. Nothing
    stated: ``None``."""
    found = states(text, dated)
    if vocab.D_DISMISSED in found:
        return vocab.D_DISMISSED
    if vocab.D_SETTLED in found:
        return vocab.D_SETTLED
    if vocab.DEFAULT in found and vocab.ANSWERED in found:
        return vocab.D_UNCLEAR
    if vocab.DEFAULT in found:
        return vocab.DEFAULT
    if vocab.ANSWERED in found:
        s = str(text or "")
        if _ORIGINAL_ONLY.search(s) and _AMENDED.search(s):
            return vocab.ANSWERED_ORIGINAL
        return vocab.ANSWERED
    return None


def _notes(d: dict[str, Any]) -> str:
    return " | ".join(str(f) for f in d.get("flags") or [])


def contradiction(d: dict[str, Any]) -> str | None:
    """The status a defendant's notes state when its status says no answer
    (``vocab.NO_ANSWER``); ``None`` when the two agree."""
    if d.get("status") not in vocab.NO_ANSWER:
        return None
    return stated(_notes(d), dated=True)


def reconciled(d: dict[str, Any], said: str) -> str:
    """The status ``reconcile`` prints for a note that says ``said``. An
    answer the note states with no answer date found stays a question
    ("Unclear"), never "Answered" without its date (a gate refuses that)."""
    if said in (vocab.ANSWERED, vocab.ANSWERED_ORIGINAL) and not (d.get("answered") or {}).get("date"):
        return vocab.D_UNCLEAR
    return said


def reconcile(result: dict[str, Any], pass_name: str = "status-note") -> list[dict[str, Any]]:
    """Every defendant whose no-answer status its own notes contradict gets
    the status the notes state, in place. Returns one log entry per change
    (no ``source``: parity explains these by the prior's own note)."""
    log = []
    for d in result.get("defendants") or []:
        said = contradiction(d)
        if said is None:
            continue
        new = reconciled(d, said)
        log.append(
            {
                "path": f"defendants[{d.get('name')}].status",
                "old": d.get("status"),
                "new": new,
                "source": None,
                "why": "the defendant's own note states this status",
                "pass": pass_name,
            }
        )
        d["status"] = new
    return log
