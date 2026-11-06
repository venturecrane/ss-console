"""The fixed words a job's reply says, held or delivered, and the check that
refuses any other words at the bound send (mechanism 4, ADR 0104).

WHY (2026-10-24 01:19Z, the first client seat). Litigation job 01M5MWGB was
held; its DELIVER wake carried ``Hold class: smd_finishing.`` and the
litigation-status skill held the one sentence for that class. The model bound
the reply and sent its own words: "The litigation status list was not produced
... tell me if you want the run requested again. SMD will also need to look at
why it stopped." The broker sent them, because the wording lived only in a
skill and nothing checked it.

So the held wording moved HERE, and on 2026-11-04 the delivered wording joined
it (``delivered_wording``, ``delivered_matter``): every job kind's reply, held
or delivered, is rendered by code when the model binds (``render``), from the
job's ledger row and the broker's own reads, and the send refuses any other
words (``check``). The refusal quotes the exact text, so the model's retry
succeeds in one step and the one reply is still unspent. A delivered reply
used to be the model's composition from counts under exact-label rules in skill
prose; a count it restated was a count it could restate wrong.

THE SWITCH. This module renders and compares; whether a send is refused is the
seat's ``logic_in_code.fixed_wording`` (``reply_text.refuses``). In
``observe``, which is every seat until the proofs need otherwise, only a held
litigation or case file reply is refused, as it has been since the incident.

THE CHRONOLOGY HAS NO HELD WORDING (2026-10-24, the Captain): the firm hears
about a chronology only when it is delivered, so ``reply_binding`` refuses a
held chronology its bind outright, before any wording could matter. Its
delivered reply is rendered like every other.

THE HELD REGISTRY. ``WORDING[lane][hold class]`` is one entry per class:

* a string: that class's wording for every report of the lane, or
* a dict ``{report: wording or (wording, ...)}``: per report (litigation's
  ``status``, ``deadlines``, ``reassign``). A tuple lists every wording the
  class may take.

Adding a class is one entry here plus its sentence in the skill's held step.
A class with no entry for a report falls back to that report's
``smd_finishing``, as the skills say. Demand and drafting holds are worded by
``delivered_matter`` from the hold's reason (``hold_class`` names no class for
those lanes).

SLOTS. Only a held wording may carry a slot: ``<number>`` (a matter number,
from the job row), ``<name>`` and ``<why>`` (a staff reassignment's unresolved
name and its kind, from the request's own names on the row and the hold's
reason), and ``<file name>``. The first three are filled from the job and so
are verified, not accepted. ``<name>`` survives only on a row that predates its
recorded request names; ``<file name>`` is carried by no wording today and the
matcher keeps it for a lane that may. A delivered wording has no slot at all:
a value code cannot fill omits its sentence.

MATCHING. HTML tags become spaces and entities are decoded, curly quotes become
straight ones, and runs of whitespace (line breaks included) collapse to one
space. Nothing else is forgiven: a greeting, a sign-off, a changed word or a
missing period is refused. Every part the send carries (the text and the HTML)
must match.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from . import delivered_matter, delivered_wording, hold_class
from .reply_binding import BindingRefused

SLOT_NUMBER = "<number>"
SLOT_NAME = "<name>"
SLOT_WHY = "<why>"
SLOT_FILE = "<file name>"

WORDING: dict[str, dict[str, Any]] = {
    "litigation": {
        hold_class.SMD_FINISHING: {
            "status": (
                "The litigation status list is not filed yet, and nothing new has been put in the firm's "
                "Operator Library. Nothing is needed from you; SMD is finishing it, and the next message you "
                "get about this list will be the filed list."
            ),
            "deadlines": (
                "The deadlines report is not filed yet, and nothing new has been put in the firm's Operator "
                "Library. Nothing is needed from you; SMD is finishing it, and the next message you get about "
                "this report will be the filed report."
            ),
            "reassign": (
                "The cases have not all been moved yet. Nothing is needed from you; SMD is finishing it, and "
                "the next message you get about this will say what was moved."
            ),
        },
        hold_class.NOTHING_IN_SCOPE: {
            "status": (
                "No list was filed: none of the firm's open litigation matters fit this request. Nothing is "
                "needed from you."
            ),
            "deadlines": (
                "No report was filed: none of the firm's Open or Pending matters fit this request. Nothing is "
                "needed from you."
            ),
        },
        hold_class.STAFF_UNRESOLVED: {
            "reassign": (
                "Nothing was changed: <name> <why>, so I did not guess. Which person did you mean? Send the "
                "request again with their full name as Smokeball shows it; that is all that is needed."
            ),
        },
        # 2026-10-24: "my cases" from an address no staff record in Smokeball
        # carries (a status list or a deadlines report); whose files is the firm's.
        hold_class.REQUESTER_UNRESOLVED: (
            "I could not match your email address to a staff member in Smokeball, so I did not guess whose "
            "files to list. Which attorney did you mean? Reply with their name as Smokeball shows it; that is "
            "all that is needed."
        ),
    },
    # The Case File Manager (2026-10-26). Its reports are what the hold leaves
    # true: ``full`` (a review that filed nothing), ``partial`` (a review that
    # filed some File Statuses before it held, so "nothing new" would be false)
    # and ``apply`` (approved changes). Every smd_finishing hold of this lane
    # pages SMD (hold_class._CASEFILE, proof/watch.settle), which is what lets
    # the words say so; none promises a later message.
    "casefile": {
        hold_class.SMD_FINISHING: {
            "full": (
                "The case file review is not filed yet, and nothing new has been put in the firm's files. "
                "Nothing is needed from you; SMD has been told and is finishing it."
            ),
            "partial": (
                "The case file review is not finished: some files have a new File Status, and the ranked list "
                "is not filed yet. Nothing is needed from you; SMD has been told and is finishing it."
            ),
            "apply": (
                "The approved changes have not all been made. Nothing is needed from you; SMD has been told "
                "and is looking at them."
            ),
        },
        hold_class.NOTHING_IN_SCOPE: {
            "full": (
                "No files were reviewed: none of the firm's Open or Pending matters fit this request. Nothing "
                "is needed from you."
            ),
        },
        hold_class.STAFF_UNRESOLVED: {
            "full": (
                "Nothing was read: a name in the request does not match exactly one person on the firm's staff "
                "list in Smokeball, so I did not guess. Whose files did you mean? Reply with their full name as "
                "Smokeball shows it; that is all that is needed."
            ),
        },
    },
}

#: ``<why>`` for staff_unresolved, by the kind the runner named (reassign/spec.Unresolved).
STAFF_WHY = {
    "ambiguous": "matches more than one person on the firm's staff list in Smokeball",
    "unknown": "is not on the firm's staff list in Smokeball",
    "inactive": "is not an active staff member in Smokeball, so cases cannot move to them",
    "same": "is both who the cases move from and who they move to",
}
#: Which request names a role's unresolved name can be (litigation_reassign.validate keys).
_ROLE_NAMES = {"from": "from_names", "to": "to_name", "attorney": "attorney_names"}
_UNRESOLVED = re.compile(r"^staff_unresolved:\s*(from|to|attorney)\s+(unknown|ambiguous|inactive|same)\b")
#: A legacy row with no recorded request names: the wake's own name alphabet.
_NAME = re.compile(r"[A-Za-z][A-Za-z .'-]{0,79}")
#: Every job kind whose reply this module words, and its lane. The
#: chronology's reply is worded only when delivered (a held chronology is
#: refused its reply in reply_binding); a negotiation JOB never binds (only its
#: notices are announced, and a notice reads as a delivered negotiation_job).
KIND_LANE = {
    "litigation_job": "litigation",
    "casefile_job": "casefile",
    "medchron_job": "medchron",
    "demand_job": "demand",
    "drafting_job": "drafting",
    "negotiation_job": "negotiation",
    "note_job": "note",
}
#: The lanes whose HELD reply is a hold class's wording (WORDING).
HELD_CLASS_LANES = frozenset(WORDING)
_FILE_MAX = 200

# A drafting job that held, failed or waited on its lane's proof run has no
# wording here: its requester is told nothing and SMD is paged (Captain,
# 2026-11-06; ``stop_note``, which ``reply_binding`` refuses those binds by).
# The fixed "we are checking it" notes #3816 added the same morning are gone
# with it, so no code path can send them.


class FixedWordingRefused(BindingRefused):
    """A job's reply carried words other than its fixed wording. The message
    quotes the text to send; the overlay hands it to the model."""


#: The name the overlay's error mapping knew until 2026-11-04. The broker's
#: error code is the class name (``request_errors``), now ``FixedWordingRefused``;
#: the overlay maps both, and this alias keeps old imports working.
HeldWordingRefused = FixedWordingRefused


def _casefile_report(row: dict[str, Any]) -> str:
    """``apply``, ``partial`` (a File Status was filed before the hold, read
    back: the job row's own files) or ``full``."""
    if row.get("mode") == "apply":
        return "apply"
    try:
        files = (json.loads(row.get("delivery_json") or "{}") or {}).get("files") or []
    except (ValueError, AttributeError):
        files = []
    return "partial" if any(isinstance(f, dict) and f.get("role") == "file_status" for f in files) else "full"


def _report(lane: str, row: dict[str, Any]) -> str:
    if lane == "casefile":
        return _casefile_report(row)
    report = str(row.get("report") or "status")
    return report if report in ("status", "deadlines", "reassign") else "status"


def _wordings(lane: str, cls: str, report: str) -> tuple[str, ...]:
    entry = WORDING[lane].get(cls)
    if isinstance(entry, dict):
        entry = entry.get(report)
    if entry is None:
        if cls == hold_class.SMD_FINISHING:
            raise KeyError(f"{lane}/{report} has no smd_finishing wording")
        return _wordings(lane, hold_class.SMD_FINISHING, report)
    return (entry,) if isinstance(entry, str) else tuple(entry)


def templates() -> list[tuple[str, str, str, str]]:
    """Every ``(lane, class, report, wording)`` the registry holds, slots
    unfilled: what the skills must carry word for word."""
    out = []
    for lane, classes in WORDING.items():
        for cls, entry in classes.items():
            per_report = entry if isinstance(entry, dict) else {"*": entry}
            for report, words in per_report.items():
                for w in (words,) if isinstance(words, str) else words:
                    out.append((lane, cls, report, w))
    return out


def _staff_fills(row: dict[str, Any]) -> list[tuple[str | None, str]]:
    """``(name or None for "any name in the wake's alphabet", why)`` pairs."""
    m = _UNRESOLVED.match(str(row.get("reason") or "").strip())
    roles = [m.group(1)] if m else list(_ROLE_NAMES)
    whys = [STAFF_WHY[m.group(2)]] if m else list(STAFF_WHY.values())
    try:
        recorded = json.loads(row["reassign_json"]) if row.get("reassign_json") else None
    except ValueError:
        recorded = None
    if not isinstance(recorded, dict):
        return [(None, why) for why in whys]
    names: list[str] = []
    for role in roles:
        got = recorded.get(_ROLE_NAMES[role])
        names.extend([got] if isinstance(got, str) else [n for n in got or [] if isinstance(n, str)])
    return [(" ".join(n.split()), why) for n in dict.fromkeys(names) for why in whys]


def allowed(kind: str, row: dict[str, Any]) -> list[str]:
    """The texts a HELD job's reply may be on a lane with hold classes, every
    verifiable slot filled. ``<file name>`` (and ``<name>`` on a row that
    predates its recorded request names) stays a slot."""
    lane = KIND_LANE[kind]
    cls = hold_class.classify(lane, row.get("reason"))[0]
    number = str(row.get("matter_number") or "").strip() or SLOT_NUMBER
    out: list[str] = []
    for w in _wordings(lane, cls, _report(lane, row)):
        w = w.replace(SLOT_NUMBER, number)
        if SLOT_NAME in w or SLOT_WHY in w:
            for name, why in _staff_fills(row):
                out.append(w.replace(SLOT_NAME, name or SLOT_NAME).replace(SLOT_WHY, why))
        else:
            out.append(w)
    return list(dict.fromkeys(out))


def _rendered(lane: str, row: dict[str, Any], reads: dict[str, Any]) -> str:
    """One text for a row whose wording is not a hold class's, or "" when its
    lane words nothing in its state."""
    state = row.get("state")
    if lane == "negotiation":
        return delivered_matter.stored(row) if row.get("notice") is True else ""
    if lane == "note":
        return delivered_matter.stored(row)
    if lane in ("demand", "drafting") and state in ("delivered", "held"):
        return (delivered_matter.demand if lane == "demand" else delivered_matter.drafting)(row)
    if state != "delivered":
        return ""
    if lane == "medchron":
        return delivered_matter.medchron(row, reads)
    return (delivered_wording.litigation if lane == "litigation" else delivered_wording.casefile)(row)


def render(kind: str, row: dict[str, Any], reads: dict[str, Any] | None = None) -> list[str]:
    """Every text this job's reply may be, held or delivered: one text for a
    delivered reply, the hold class's wordings for a held one. ``[]`` when the
    kind has no fixed wording in this state (the binding refuses those states
    before any wording could matter). ``reads`` is what the broker read at bind
    time that is not on the row (the chronology's allowance)."""
    lane = KIND_LANE.get(kind)
    if lane is None:
        return []
    if row.get("state") == "held" and lane in HELD_CLASS_LANES:
        return allowed(kind, row)
    text = _rendered(lane, row, reads or {})
    return [text] if text.strip() else []


def delivered_templates() -> list[tuple[str, str, str]]:
    """Every ``(lane, key, sentence)`` a delivered (or a demand or drafting
    held) reply is built from, fields unfilled, plus each fixed phrase the
    sentences take as a value: what the skills carry word for word."""
    out = [
        (lane, key, text)
        for table in (delivered_wording.SENTENCES, delivered_matter.SENTENCES)
        for lane, sentences in table.items()
        for key, text in sentences.items()
    ]
    out += [("litigation", f"label:{key}", label) for key, label in delivered_wording.DEADLINE_LABELS]
    out += [("medchron", f"kind:{key}", phrase) for key, phrase in delivered_matter.CURRENT_KINDS.items()]
    return out


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.replace(" ", " ").split())


def _from_html(markup: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", " ", markup))


def _slot_ok(slot: str, value: str) -> bool:
    if slot == SLOT_NAME:
        return bool(_NAME.fullmatch(value))
    # <file name>: one short line, no question, no second sentence.
    return 0 < len(value) <= _FILE_MAX and not re.search(r"[?!\n]|\.\s+[A-Z]", value)


def _matches(text: str, wording: str) -> bool:
    target = _norm(wording)
    slots = [s for s in (SLOT_FILE, SLOT_NAME) if s in target]
    if not slots:
        return text == target
    pattern = re.escape(target)
    for s in slots:
        pattern = pattern.replace(re.escape(s), f"(?P<{s.strip('<>').replace(' ', '_')}>.+?)")
    m = re.fullmatch(pattern, text)
    return bool(m) and all(_slot_ok(s, m.group(s.strip("<>").replace(" ", "_"))) for s in slots)


def check(kind: str, row: dict[str, Any], comment: str, html: str, texts: list[str] | None = None) -> None:
    """Refuse a job's reply unless every part it carries is one of ``texts``
    (``render``'s, by default). A no-op for a kind this module does not word.
    A worded kind whose row renders nothing is refused too: the model never
    composes the words a code renderer could not."""
    if kind not in KIND_LANE:
        return
    texts = render(kind, row) if texts is None else texts
    if not texts:
        raise FixedWordingRefused(
            "fixed_wording: nothing was sent. This reply has no words the record can give, and no other words may "
            "be sent in their place. Send nothing and end the turn."
        )
    parts = [_norm(p) for p in (comment, _from_html(html) if html else "") if p and p.strip()]
    if parts and all(any(_matches(p, t) for t in texts) for p in parts):
        return
    raise FixedWordingRefused(refusal(texts))


def refusal(texts: list[str]) -> str:
    """The sentence the model reads: the exact text, nothing to interpret."""
    quoted = " OR ".join(f'"{t}"' for t in texts)
    slot = f" Replace {SLOT_FILE} with the file's name and nothing else." if any(SLOT_FILE in t for t in texts) else ""
    return (
        "fixed_wording: nothing was sent and the one reply is still yours. This reply is its fixed wording, "
        f"nothing before or after it. Send exactly {'one of these' if len(texts) > 1 else 'this'}: "
        f"{quoted}.{slot}"
    )


__all__ = [
    "HELD_CLASS_LANES",
    "KIND_LANE",
    "STAFF_WHY",
    "WORDING",
    "FixedWordingRefused",
    "HeldWordingRefused",
    "allowed",
    "check",
    "delivered_templates",
    "refusal",
    "render",
    "templates",
]
