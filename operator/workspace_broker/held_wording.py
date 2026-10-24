"""The fixed words a HELD job's reply says, by lane, report and hold class,
and the check that refuses any other words at the bound send.

WHY (2026-10-24 01:19Z, ashton-price). Litigation job 01M5MWGB was held; its
DELIVER wake carried ``Hold class: smd_finishing.`` and the litigation-status
skill held the one sentence for that class. The model bound the reply and sent
its own words: "The litigation status list was not produced ... tell me if you
want the run requested again. SMD will also need to look at why it stopped."
The broker sent them, because the wording lived only in a skill and nothing
checked it. A hold of ours must never come back to the firm as a question or a
route to SMD; the skill said so and the model did it anyway.

So the wording lives HERE, the broker refuses a held litigation reply whose
words are not one of these (``check``), and the refusal quotes the exact text,
so the model's retry succeeds in one step. The skill carries the same
sentences (``tests/test_held_wording_parity.py`` pins skill == code).

THE CHRONOLOGY HAS NO HELD WORDING (2026-10-24, the Captain): the firm hears
about a chronology only when it is delivered, so ``reply_binding`` refuses a
held chronology its bind outright, before any wording could matter.

THE REGISTRY. ``WORDING[lane][hold class]`` is one entry per class:

* a string: that class's wording for every report of the lane, or
* a dict ``{report: wording or (wording, ...)}``: per report (litigation's
  ``status``, ``deadlines``, ``reassign``). A tuple lists every wording the
  class may take.

Adding a class is one entry here plus its sentence in the skill's held step.
A class with no entry for a report falls back to that report's
``smd_finishing``, as the skills say.

SLOTS. A wording may carry ``<number>`` (a matter number, from the job row),
``<name>`` and ``<why>`` (a staff reassignment's unresolved name and its kind,
from the request's own names on the row and the hold's reason), and
``<file name>``. The first three are filled from the job and so are verified,
not accepted. ``<file name>`` is one the broker cannot look up: it is
constrained to one short line with no question and no new sentence, and every
other word around it must match. (``<file name>`` was the chronology's
another_person question; no wording carries it today, and the matcher keeps it
for a lane that may.)

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

from . import hold_class
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
#: The kinds whose state is held and whose reply this module words. Not the
#: chronology: a held chronology is refused its reply (reply_binding).
KIND_LANE = {"litigation_job": "litigation"}
_FILE_MAX = 200


class HeldWordingRefused(BindingRefused):
    """A held job's reply carried words other than its fixed wording. The
    message quotes the text to send; the overlay hands it to the model."""


def _report(lane: str, row: dict[str, Any]) -> str:
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
    """The texts a held job's reply may be, every verifiable slot filled.
    ``<file name>`` (and ``<name>`` on a row that predates its recorded
    request names) stays a slot."""
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


def check(kind: str, row: dict[str, Any], comment: str, html: str) -> None:
    """Refuse a held job's reply unless every part it carries is one of
    ``allowed``. A no-op for any other kind or state."""
    if kind not in KIND_LANE or row.get("state") != "held":
        return
    texts = allowed(kind, row)
    parts = [_norm(p) for p in (comment, _from_html(html) if html else "") if p and p.strip()]
    if parts and all(any(_matches(p, t) for t in texts) for p in parts):
        return
    raise HeldWordingRefused(refusal(texts))


def refusal(texts: list[str]) -> str:
    """The sentence the model reads: the exact text, nothing to interpret."""
    quoted = " OR ".join(f'"{t}"' for t in texts)
    slot = f" Replace {SLOT_FILE} with the file's name and nothing else." if any(SLOT_FILE in t for t in texts) else ""
    return (
        "held_wording: nothing was sent and the one reply is still yours. A held job's reply is its fixed "
        f"wording, nothing before or after it. Send exactly {'one of these' if len(texts) > 1 else 'this'}: "
        f"{quoted}.{slot}"
    )


__all__ = ["KIND_LANE", "STAFF_WHY", "WORDING", "HeldWordingRefused", "allowed", "check", "refusal", "templates"]
