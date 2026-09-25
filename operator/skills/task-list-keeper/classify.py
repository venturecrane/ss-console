"""Sort one overdue task into open, done, stale or at_stake. Pure; no I/O.

The rules are CLOSED lists, written here and in ``references/classification.md``
in the same words, because the class decides what the Operator offers to do to
a firm's record:

* ``at_stake`` - money or a court date rides on it. Never proposed for
  closure; the escalator keeps watching it. Checked FIRST, so evidence that a
  lien task "looks done" can never close it.
* ``done`` - the record shows the work happened: a document of the task's
  kind, dated on or after the task, every distinctive word of whose name the
  task also names (the provider, the set, the party), or the records chase for
  this task resolved.
* ``stale`` - the matter is closed, or the task repeats another open task on
  the same matter.
* ``open`` - everything else.

Every piece of evidence is an ATOM read off the record (a document's kind and
its day, a ledger resolution), never a sentence the model wrote, and it rides
the proposal row's ``payload.evidence`` so the reason a line said "looks done"
is on file beside the approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

#: Words that put money or a court date on a task. Whole words, any case.
AT_STAKE_WORDS = (
    "lien",
    "liens",
    "payoff",
    "settlement",
    "disburse",
    "disbursement",
    "trust",
    "retainer",
    "fee",
    "fees",
    "costs",
    "invoice",
    "payment",
    "refund",
    "check",
    "medi-cal",
    "medicare",
    "medicaid",
    "court",
    "hearing",
    "trial",
    "motion",
    "deposition",
    "mediation",
    "arbitration",
    "sanctions",
    "statute",
    "sol",
    "summons",
    "judgment",
    "appeal",
    "subpoena",
)
_AT_STAKE_RE = re.compile(
    r"(?<![a-z0-9-])(?:" + "|".join(re.escape(w) for w in AT_STAKE_WORDS) + r")(?![a-z0-9-])", re.I
)

#: The authored priority markers the escalator already reads (render.py).
PRIORITY_MARKERS = ("CRITICAL", "URGENT", "HIGH PRIORITY")

#: Matter statuses that mean the matter is over. Anything else, or no status,
#: is treated as open: a guess that a matter is closed would close its tasks.
CLOSED_STATUSES = frozenset({"closed", "archived", "cancelled", "canceled"})

#: topic -> (subject pattern, document-name pattern, evidence phrase). A task is
#: done on a topic only when its subject names the topic AND a document of that
#: kind, dated on or after the task, is on the matter.
_TOPICS: tuple[tuple[str, re.Pattern[str], re.Pattern[str], str], ...] = (
    (
        "proof_of_service",
        re.compile(r"\bserv(?:e|ed|ice)\b", re.I),
        re.compile(r"proof[\s_-]+of[\s_-]+service", re.I),
        "a proof of service",
    ),
    (
        "verification",
        re.compile(r"\bverif(?:y|ied|ication)\b", re.I),
        re.compile(r"\bverification\b", re.I),
        "a verification",
    ),
    ("records", re.compile(r"\brecords?\b", re.I), re.compile(r"\brecords?\b", re.I), "a records file"),
)

#: The closed "why I can't finish it" phrases for an Operator-own task handed
#: to a person, chosen by the subject's topic. First match wins.
_CANT_FINISH: tuple[tuple[re.Pattern[str], str], ...] = (
    (_AT_STAKE_RE, "money or a court date rides on it, so a person should own it"),
    (re.compile(r"\bverif(?:y|ied|ication)\b", re.I), "the signed verification is not on file yet"),
    (re.compile(r"\bserv(?:e|ed|ice)\b", re.I), "it needs a person to confirm what was served and when"),
    (re.compile(r"\brecords?\b", re.I), "the records are not on file yet"),
    (
        re.compile(r"\b(?:discovery|interrogator|admission|production)", re.I),
        "it needs a person to confirm the discovery dates",
    ),
)
CANT_FINISH_DEFAULT = "it needs a person to decide the next step"

#: Words that name the TOPIC (or are filler) and so cannot tie a document to a
#: task: "proof of service" shares "service" with every service task.
_STOP = frozenset(
    {
        "records",
        "record",
        "request",
        "requested",
        "certified",
        "medical",
        "from",
        "with",
        "that",
        "this",
        "chase",
        "confirm",
        "proof",
        "service",
        "served",
        "serve",
        "serving",
        "verification",
        "verify",
        "verified",
        "signed",
        "responses",
        "response",
        "pdf",
        "and",
        "the",
        "for",
        "doc",
        "docx",
        "copy",
        "final",
        "draft",
        "review",
    }
)


@dataclass(frozen=True)
class FileRef:
    name: str
    day: date | None


@dataclass(frozen=True)
class MatterFacts:
    matter_id: str
    status: str | None = None
    files: tuple[FileRef, ...] = ()
    court_days: tuple[date, ...] = ()
    court_event_ids: tuple[str, ...] = ()
    calendar_read: bool = True


@dataclass(frozen=True)
class TaskFacts:
    task_id: str
    matter_id: str
    subject: str
    due: date
    created: date | None = None
    event_id: str | None = None
    chaser_resolved: bool = False


@dataclass(frozen=True)
class Verdict:
    klass: str  # open | done | stale | at_stake
    reason: str  # a closed-phrase key: see REASON_TEXT
    evidence: tuple[str, ...] = field(default=())
    evidence_phrase: str | None = None
    evidence_day: date | None = None


#: The closed reason keys and the words each renders as on a proposal line.
REASON_TEXT = {
    "at_stake_word": "Money or a court date rides on it.",
    "priority_marker": "It is marked as a priority.",
    "court_event": "It is tied to a court date.",
    "court_date_in_window": "The matter has a court date coming up.",
    "calendar_unread": "I could not read the matter's calendar.",
    "document_on_file": "Looks done: {phrase} dated {day} is on the matter.",
    "records_chase_resolved": "Looks done: the records chase for it is resolved.",
    "matter_closed": "The matter is closed.",
    "duplicate": "Same task as another open one on this matter.",
    "still_open": "Still open.",
}


def normalized_subject(subject: str) -> str:
    """The subject with the provenance stamp, case and punctuation folded out:
    two tasks that differ only in those are the same task."""
    text = subject.strip()
    if text.lower().startswith("[operator]"):
        text = text[len("[operator]") :]
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def duplicate_ids(tasks: list[TaskFacts]) -> set[str]:
    """Task ids that repeat another open task on the same matter. The copy with
    the LATEST due date (then the highest id) is the one kept; the rest are stale."""
    groups: dict[tuple[str, str], list[TaskFacts]] = {}
    for task in tasks:
        norm = normalized_subject(task.subject)
        if norm:
            groups.setdefault((task.matter_id, norm), []).append(task)
    dupes: set[str] = set()
    for group in groups.values():
        if len(group) > 1:
            keeper = max(group, key=lambda t: (t.due, t.task_id))
            dupes |= {t.task_id for t in group if t.task_id != keeper.task_id}
    return dupes


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOP}


def _document_evidence(task: TaskFacts, matter: MatterFacts) -> Verdict | None:
    reference = task.created or task.due
    for topic, subject_re, file_re, phrase in _TOPICS:
        if not subject_re.search(task.subject):
            continue
        for doc in sorted(matter.files, key=lambda f: (f.day or date.min, f.name)):
            if doc.day is None or doc.day < reference or not file_re.search(doc.name):
                continue
            # Every distinctive word of the document's name must appear in the
            # task (the provider, the set, the party): one proof of service on
            # a matter must not close every service task on it, and "Set Two"
            # must not close "Set One".
            named = _tokens(doc.name)
            if not named or not named <= _tokens(task.subject):
                continue
            atom = f"document:{topic}:{doc.day.isoformat()}"
            return Verdict("done", "document_on_file", (atom,), phrase, doc.day)
    return None


def classify(task: TaskFacts, matter: MatterFacts, *, today: date, window_days: int, duplicates: set[str]) -> Verdict:
    """The one verdict for this task. ``at_stake`` wins over every other class."""
    if _AT_STAKE_RE.search(task.subject):
        return Verdict("at_stake", "at_stake_word", ("subject:at_stake_word",))
    upper = task.subject.upper()
    if any(marker in upper for marker in PRIORITY_MARKERS):
        return Verdict("at_stake", "priority_marker", ("subject:priority_marker",))
    if task.event_id and task.event_id in matter.court_event_ids:
        return Verdict("at_stake", "court_event", (f"event:{task.event_id}",))
    if not matter.calendar_read:
        return Verdict("at_stake", "calendar_unread", ("calendar:unread",))
    upcoming = [d for d in matter.court_days if today <= d and (d - today).days <= window_days]
    if upcoming:
        return Verdict("at_stake", "court_date_in_window", (f"court_date:{min(upcoming).isoformat()}",))
    evidence = _document_evidence(task, matter)
    if evidence is not None:
        return evidence
    if task.chaser_resolved:
        return Verdict("done", "records_chase_resolved", ("ledger:records_chase_resolved",))
    if (matter.status or "").strip().lower() in CLOSED_STATUSES:
        return Verdict("stale", "matter_closed", (f"matter_status:{(matter.status or '').strip().lower()}",))
    if task.task_id in duplicates:
        return Verdict("stale", "duplicate", ("subject:duplicate_on_matter",))
    return Verdict("open", "still_open")


def cant_finish_reason(subject: str) -> str:
    """The closed phrase for why the Operator cannot finish its own task."""
    for pattern, phrase in _CANT_FINISH:
        if pattern.search(subject):
            return phrase
    return CANT_FINISH_DEFAULT
