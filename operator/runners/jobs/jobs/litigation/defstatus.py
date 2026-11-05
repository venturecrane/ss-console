"""A defendant's status: derived from its dated events, and checked against its words.

``derive`` (2026-11-05, ADR 0104 mechanism 6) is the only source of a status on
a read: the model reports dated, cited events and code applies one rule table
(``_TABLE``). A defendant the events cannot settle is "Unclear", never a guess.

A report already stored on a seat carries a status a read asserted, with no
``status_from`` key (``seed`` for a hand run's). It is never recomputed from
its events alone, because they are not all there: dismissal, settlement and
default had no dated field when it was read. It prints as stored until a read
reaches it. A full read replaces the defendant outright. An update that moves
one of its events derives with the stored status kept as an input
(``stored_status``), so a paper arriving on a "Dismissed" defendant cannot make
it "Served" (``after_update``).

The cross-check against a defendant's words:

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

import datetime as dt
import re
from typing import Any, Callable, NamedTuple

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
_ORIGINAL_ONLY = re.compile(r"(?i)\boriginal complaint\b")
_AMENDED = re.compile(r"(?i)\bamended\b")


def _affirmed(rx: re.Pattern[str], text: str) -> bool:
    """``rx`` matches somewhere in ``text`` with no negator before it in its clause."""
    for m in rx.finditer(text):
        before = text[max(0, m.start() - _WINDOW) : m.start()]
        cut = list(_CLAUSE_END.finditer(before))
        if cut:
            before = before[cut[-1].end() :]
        if not _NEGATOR.search(before):
            return True
    return False


def states(text: Any) -> set[str]:
    """Every outranking state ``text`` affirms (``vocab`` defendant statuses)."""
    s = str(text or "")
    found = {status for status, rx in _PATTERNS.items() if _affirmed(rx, s)}
    if vocab.DEFAULT in found and _DEFAULT_UNDONE.search(s):
        found.discard(vocab.DEFAULT)
    return found


def stated(text: Any) -> str | None:
    """The one status ``text`` states, by rule: a dismissal outranks a
    settlement, and either outranks a default or an answer (a defendant is
    defaulted or answers first, and is let go after). A default AND an answer
    with neither undone is two findings at odds: ``vocab.D_UNCLEAR``. Nothing
    stated: ``None``."""
    found = states(text)
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
    return stated(_notes(d))


def reconciled(d: dict[str, Any], said: str) -> str:
    """The status ``reconcile`` prints for a note that says ``said``. An
    answer the note states with no answer date found stays a question
    ("Unclear"), never "Answered" without its date (a gate refuses that)."""
    if said in (vocab.ANSWERED, vocab.ANSWERED_ORIGINAL) and not (d.get("answered") or {}).get("date"):
        return vocab.D_UNCLEAR
    return said


def reconcile(result: dict[str, Any], pass_name: str = "status-note") -> list[dict[str, Any]]:
    """Every defendant with an ASSERTED status (seeded or stored, ``asserted``)
    whose no-answer status its own notes contradict gets the status the notes
    state, in place. A derived status is not corrected here: a note against it
    holds under the gate (``gates.NOTE_RULE``). Returns one log entry per
    change (no ``source``: parity explains these by the prior's own note)."""
    log = []
    for d in result.get("defendants") or []:
        if not asserted(d):
            continue
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


# ---- the derivation ------------------------------------------------------------------------
def asserted(d: dict[str, Any]) -> bool:
    """The status was stated (a stored read, a seed), not derived from events."""
    return d.get("status_from") != vocab.FROM_DERIVED


def _date(d: dict[str, Any], field: str) -> str | None:
    v = d.get(field)
    date = v.get("date") if isinstance(v, dict) else None
    return str(date) if date else None


def _cited(v: Any) -> bool:
    src = v.get("source") if isinstance(v, dict) else None
    return isinstance(src, dict) and bool(src.get("file_id"))


#: The event a stored status stands for, when the report that holds it has no
#: dated field for it. It counts as having happened, with no date.
_STORED_EVENT = {
    vocab.D_DISMISSED: "dismissed",
    vocab.D_SETTLED: "settled",
    vocab.DROPPED: "dropped",
    vocab.DEFAULT: "default_entered",
    vocab.ANSWERED: "answered",
    vocab.ANSWERED_ORIGINAL: "answered",
    vocab.APPEARED_NO_ANSWER: "appeared",
    vocab.SERVED_NO_ANSWER: "served",
    vocab.SERVICE_REJECTED: "service_rejected",
}
_STORED_ROLE = {vocab.UIM: vocab.ROLE_UIM, vocab.FIRM_CLIENT: vocab.ROLE_FIRM_CLIENT}


class _Facts:
    """One defendant's events as the rule table asks about them."""

    def __init__(self, d: dict[str, Any], today: dt.date, stored: Any) -> None:
        self.d = d
        self.today = today.isoformat()
        self.stored = stored
        self.notes = _notes(d)
        self.dates = {f: _date(d, f) for f in vocab.DEFENDANT_EVENTS}
        role = d.get("role")
        self.role_read = role.get("value") if isinstance(role, dict) else None
        self.role_cited = _cited(role)
        self.role = self.role_read or _STORED_ROLE.get(stored)
        self.ofs = d.get("out_for_service")

    def has(self, field: str) -> bool:
        return bool(self.dates[field]) or _STORED_EVENT.get(self.stored) == field

    def default_live(self) -> bool:
        if not self.has("default_entered"):
            return False
        undone, entered = self.dates["default_set_aside"], self.dates["default_entered"]
        return not undone or bool(entered and entered > undone)

    def role_unreadable(self) -> bool:
        return bool(self.role_read) and (self.role_read not in vocab.DEFENDANT_ROLES or not self.role_cited)

    def uncited(self) -> str | None:
        return next((f for f, x in self.dates.items() if x and not _cited(self.d.get(f))), None)

    def future(self) -> str | None:
        return next((f for f, x in self.dates.items() if x and x > self.today), None)

    def service_undone(self) -> bool:
        served, rejected = self.dates["served"], self.dates["service_rejected"]
        return bool(served and rejected and rejected >= served)

    def out(self) -> bool:
        value = self.ofs.get("value") if isinstance(self.ofs, dict) else None
        if value == "yes" and _cited(self.ofs):
            return True
        return self.stored == vocab.OUT_FOR_SERVICE or (bool(self.dates["sent_for_service"]) and value != "no")

    def original_only(self) -> bool:
        words = _ORIGINAL_ONLY.search(self.notes) and _AMENDED.search(self.notes)
        return bool(words) or self.stored == vocab.ANSWERED_ORIGINAL


class Decision(NamedTuple):
    status: str
    rule: str
    #: The field whose document the status rests on (``basis``), or None.
    field: str | None


#: (rule, test, status, field). First match wins. Every row appears in the
#: answer key (``tests/litigation_status_answer_key.json``), and removing any
#: row fails it.
_TABLE: tuple[tuple[str, Callable[[_Facts], Any], str, str | None], ...] = (
    ("checked_unclear", lambda f: f.d.get("status_unclear"), vocab.D_UNCLEAR, None),
    ("unreadable_role", lambda f: f.role_unreadable(), vocab.D_UNCLEAR, "role"),
    ("uncited_event", lambda f: f.uncited(), vocab.D_UNCLEAR, None),
    ("future_event", lambda f: f.future(), vocab.D_UNCLEAR, None),
    ("role_uim", lambda f: f.role == vocab.ROLE_UIM, vocab.UIM, "role"),
    ("role_firm_client", lambda f: f.role == vocab.ROLE_FIRM_CLIENT, vocab.FIRM_CLIENT, "role"),
    ("dismissed", lambda f: f.has("dismissed"), vocab.D_DISMISSED, "dismissed"),
    ("settled", lambda f: f.has("settled"), vocab.D_SETTLED, "settled"),
    ("dropped", lambda f: f.has("dropped"), vocab.DROPPED, "dropped"),
    ("default_and_answer", lambda f: f.default_live() and f.has("answered"), vocab.D_UNCLEAR, "default_entered"),
    (
        "default_undone_in_notes",
        lambda f: f.default_live() and _DEFAULT_UNDONE.search(f.notes),
        vocab.D_UNCLEAR,
        "default_entered",
    ),
    ("default", lambda f: f.default_live(), vocab.DEFAULT, "default_entered"),
    ("answered_original", lambda f: f.has("answered") and f.original_only(), vocab.ANSWERED_ORIGINAL, "answered"),
    ("answered", lambda f: f.has("answered"), vocab.ANSWERED, "answered"),
    ("appeared", lambda f: f.has("appeared"), vocab.APPEARED_NO_ANSWER, "appeared"),
    ("served_then_rejected", lambda f: f.service_undone(), vocab.D_UNCLEAR, "service_rejected"),
    ("served", lambda f: f.has("served"), vocab.SERVED_NO_ANSWER, "served"),
    ("rejected", lambda f: f.has("service_rejected"), vocab.SERVICE_REJECTED, "service_rejected"),
    ("out_for_service", lambda f: f.out(), vocab.OUT_FOR_SERVICE, "out_for_service"),
    ("not_served", lambda f: True, vocab.NOT_SERVED, None),
)


def stored_input(d: dict[str, Any]) -> Any:
    """The stored status this defendant's events are read beside, if any."""
    return d.get("status") if asserted(d) else d.get("stored_status")


def decide(d: dict[str, Any], today: dt.date, stored: Any = None) -> Decision:
    """The first row of the rule table that fits ``d`` (``stored``: a status
    from a report older than its events, ``stored_input``)."""
    facts = _Facts(d, today, stored)
    for rule, test, status, field in _TABLE:
        if test(facts):
            return Decision(status, rule, field or facts.uncited() or facts.future())
    raise AssertionError("the rule table ends in a row that always matches")


def derive(d: dict[str, Any], today: dt.date) -> str:
    """The status a defendant's events give (``d["stored_status"]`` is read
    beside them when an update kept one)."""
    return decide(d, today, d.get("stored_status")).status


def absorb(d: dict[str, Any], today: dt.date) -> None:
    """``d`` is now derived; a status it asserted until now becomes an input."""
    if asserted(d):
        if d.get("status") is not None:
            d["stored_status"] = d["status"]
        d["status_from"] = vocab.FROM_DERIVED
    d["status"] = derive(d, today)


def settle_derived(result: dict[str, Any], today: dt.date) -> None:
    """Every derived status worked out afresh from the events as they now
    stand (a carried date, a corrected one)."""
    for d in result.get("defendants") or []:
        if not asserted(d):
            d["status"] = derive(d, today)


def settle(result: dict[str, Any], today: dt.date) -> list[dict[str, Any]]:
    """``settle_derived``, and every asserted status reconciled against its
    notes. Returns ``reconcile``'s log."""
    settle_derived(result, today)
    return reconcile(result)


def _moved(d: dict[str, Any], old: dict[str, Any]) -> bool:
    if any(_date(d, f) != _date(old, f) for f in vocab.DEFENDANT_EVENTS):
        return True
    return any((d.get(f) or {}).get("value") != (old.get(f) or {}).get("value") for f in ("out_for_service", "role"))


def after_update(d: dict[str, Any], old: dict[str, Any], today: dt.date) -> None:
    """``d``'s status after an update, in place, ``old`` being the last list's
    entry. No event moved on an asserted status: it stands, whatever the read
    said. An event moved: derived, with the asserted status kept as an input.
    A reader's "unclear" on the last list clears when a new paper arrives."""
    moved = _moved(d, old)
    marked = d.get("status_unclear")
    if asserted(old) and not moved and not marked:
        for k in ("status", "status_from", "stored_status"):
            d.pop(k, None)
            if k in old:
                d[k] = old[k]
        return
    if moved and marked and marked == old.get("status_unclear"):
        d.pop("status_unclear", None)
    if asserted(old) and old.get("status") is not None:
        d["stored_status"] = old["status"]
    d["status_from"] = vocab.FROM_DERIVED
    d["status"] = derive(d, today)


#: Most specific first: the table's own order, every status once, Unclear last.
_ORDER = (*dict.fromkeys(s for _r, _t, s, _f in _TABLE if s != vocab.D_UNCLEAR), vocab.D_UNCLEAR)


def rank(status: Any) -> int:
    """Where a status sits in the table (lower outranks; an unknown one is last)."""
    return _ORDER.index(status) if status in _ORDER else len(_ORDER)


def basis(d: dict[str, Any]) -> Any:
    """The value a defendant's status rests on: for a derived one the deciding
    event; else the answer or service the defendant has a document for."""
    field = decide(d, dt.date.max, d.get("stored_status")).field if not asserted(d) else None
    obj = d.get(field) if field else None
    if _cited(obj):
        return obj
    return d.get("answered") if _cited(d.get("answered")) else d.get("served")
