"""The staff reassignment kind of the litigation job (2026-10-15): "update all
cases that were assigned to Pat Doe or Sam to Dana Roe, Lee's new paralegal
(litigation cases only)".

WHY HERE. The first client's office manager asked the Operator to move every
litigation case one paralegal (and a departed one) assisted to the new
paralegal, tasks included. The Operator could not: a seat turn touches one
matter, and it has no tool that changes a matter's Person Assisting. SMD did it
by hand. The same Named Administrators, the same request edge, the same
counts-only reply in her thread and the same proof gate as the litigation
status list, so it is a kind of that job, told apart by the email's OWN words
here, in code (never the model's choice, the deadlines report's rule).

WHAT IS PARSED, and what is not. This module reads the email for its SHAPE:
who the cases move from (one or more names), who they move to (exactly one
name), an optional responsible attorney ("Lee's new paralegal", "Lee's
cases"), litigation-only or not, and the statuses (Open and Pending unless the
email says otherwise). It never resolves a name: the broker holds no copy of
the practice system's staff list, so the runner resolves every name against
``GET /staff`` and refuses one it cannot match to exactly one person. A request
that reads as a reassignment but whose shape this module cannot read is
REFUSED at submit with a sentence the turn relays: guessing which way cases
move is the one mistake this kind must not make.

Why code and not the model for the names: the overlay's tool lets the model
name attorneys and paralegals for a LIST. Reading "from" and "to" correctly is
the whole of the risk here, and a regex over the firm's own sentence is
inspectable, testable and cannot be talked into a different direction. A
request it cannot read is refused, never guessed.
"""

from __future__ import annotations

import re
from typing import Any

#: The skill setting that authorizes this kind on a seat. Unauthored, refused
#: (ADR 0035: omission is refusal). It is a setting, not a new skill.
REASSIGN_KEY = "staff_reassignment"
REASSIGN_ON = "enabled"
STATUSES = ("Open", "Pending", "Closed")
DEFAULT_STATUSES = ("Open", "Pending")
MAX_FROM = 6
MAX_ATTORNEYS = 6
MAX_NAME = 100

#: Capitalized words that open a sentence or an ask, never part of a name.
_NOT_NAMES = frozenset(
    "please move moving reassign re-assign update change switch transfer all the hi hello hey can could would "
    "i we and or also thanks thank kindly just now then so ok okay our my her his their".split()
)
_WORD = r"[A-Z][A-Za-z'’.\-]*"
_NAME = rf"{_WORD}(?:[ \t]+{_WORD}){{0,2}}"
_SEP = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|&)\s+|\s*/\s*)"
_LIST = rf"{_NAME}(?:{_SEP}{_NAME})*"
_THINGS = r"(?:cases|matters|files|case\s+files)"

#: Reads as a reassignment: a moving verb governing cases, then "to" later in
#: the sentence; or the word reassign anywhere.
_INTENT = re.compile(
    rf"(?i)\breassign\w*\b|\b(?:mov(?:e|ing)|transfer\w*|switch\w*|chang(?:e|ing)|updat(?:e|ing))\s+"
    rf"(?:(?:all|of|the|their|her|his|my|our|these|those|any|open|pending|litigation|remaining|[A-Za-z]+['’]s)\s+){{0,4}}"
    rf"{_THINGS}\b[^.?!]*?\bto\b"
)
#: Who from and who to, first match wins: "assigned to A or B to C", "from A to
#: C", "A's (litigation) cases (over) to C".
_SHAPES = (
    re.compile(rf"\b(?:assigned|belonging)\s+to\s+(?P<frm>{_LIST})\s+(?:over\s+)?to\s+(?P<to>{_NAME})"),
    re.compile(rf"\bfrom\s+(?P<frm>{_LIST})\s+(?:over\s+)?to\s+(?P<to>{_NAME})"),
    re.compile(rf"(?P<frm>{_LIST})['’]s\s+(?:[a-z]+\s+){{0,2}}{_THINGS}\s+(?:[a-z]+\s+){{0,3}}?to\s+(?P<to>{_NAME})"),
)
#: A responsible attorney: "for Lee's cases", "on Lee's files", "Lee's new paralegal".
_ATTORNEY = (
    re.compile(rf"\b(?:for|on|of)\s+(?P<att>{_LIST})['’]s?\s+(?:[a-z]+\s+){{0,2}}{_THINGS}"),
    re.compile(rf"(?P<att>{_NAME})['’]s\s+(?:new\s+)?(?:paralegal|assistant)"),
)
_MORE_TO = re.compile(r"^\s*(?:,\s*)?(?:and|or|&|/)\s+[A-Z]")
_LITIGATION = re.compile(r"(?i)(?<![\w-])litigat\w*|\bin suit\b")
_NOT_LITIGATION = re.compile(
    r"(?i)\bnon-?\s?litigat|\bnot\s+(?:just|only)\s+litigat|\bboth\s+litigat|\ball\s+cases,?\s+not\s+just"
)
_WITH_CLOSED = re.compile(
    r"(?i)\b(?:including|incl\.?|and|plus|also|even)\s+(?:the\s+)?closed\b|\bclosed\s+(?:cases|matters|files)\s+(?:too|as\s+well|also)\b"
)
_OPEN_ONLY = re.compile(r"(?i)\bopen\s+(?:cases|matters|files)\s+only\b|\bonly\s+(?:the\s+)?open\b")


class ReassignUnreadable(ValueError):
    """The email reads as a reassignment and its shape could not be read."""


def is_reassignment(text: str) -> bool:
    return bool(_INTENT.search(str(text or "")))


def _clean(name: str) -> str:
    words = " ".join(name.split()).split(" ")
    while words and words[0].lower().strip("'’.") in _NOT_NAMES:
        words = words[1:]
    out = " ".join(words)
    return re.sub(r"['’]s$", "", out).strip(" .")


def _names(raw: str) -> list[str]:
    parts = re.split(_SEP, raw)
    return list(dict.fromkeys(n for n in (_clean(p) for p in parts) if n))


def _attorneys(text: str, taken: set[str]) -> list[str]:
    found: list[str] = []
    for rx in _ATTORNEY:
        for m in rx.finditer(text):
            found += [n for n in _names(m.group("att")) if n.lower() not in taken]
    return list(dict.fromkeys(found))


def _statuses(text: str) -> list[str]:
    if _OPEN_ONLY.search(text):
        return ["Open"]
    return [*DEFAULT_STATUSES, *(["Closed"] if _WITH_CLOSED.search(text) else [])]


def parse(text: str) -> dict[str, Any]:
    """The reassignment the email asks for, or ``ReassignUnreadable`` naming
    what could not be read (the sentence the turn relays)."""
    text = str(text or "")
    for rx in _SHAPES:
        m = rx.search(text)
        if m is None:
            continue
        frm, to = _names(m.group("frm")), _clean(m.group("to"))
        if _MORE_TO.match(text[m.end() :]):
            raise ReassignUnreadable(
                "the request names more than one person for the cases to go to, and they can go to one; "
                "nothing was queued"
            )
        if not frm or not to:
            break
        if to.lower() in {f.lower() for f in frm}:
            raise ReassignUnreadable("the request moves cases to the same person they come from; nothing was queued")
        if len(frm) > MAX_FROM:
            raise ReassignUnreadable(
                f"the request names more than {MAX_FROM} people to move cases from; nothing was queued"
            )
        taken = {n.lower() for n in [*frm, to]}
        return {
            "from_names": frm,
            "to_name": to,
            "attorney_names": _attorneys(text, taken)[:MAX_ATTORNEYS],
            "litigation_only": bool(_LITIGATION.search(text)) and not _NOT_LITIGATION.search(text),
            "statuses": _statuses(text),
        }
    raise ReassignUnreadable(
        'I could not tell from the email whose cases move and to whom (for example: "move the cases '
        'assigned to Pat Doe to Dana Roe"); nothing was queued'
    )


def _name_list(raw: Any, key: str, cap: int, allow_empty: bool) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        raise ValueError(f"reassign.{key} must be a list of names")
    names = [" ".join(n.split()) for n in raw]
    if (not names and not allow_empty) or len(names) > cap or any(not n or len(n) > MAX_NAME for n in names):
        raise ValueError(
            f"reassign.{key} must hold {'0' if allow_empty else '1'} to {cap} names of at most {MAX_NAME} characters"
        )
    return names


def validate(raw: Any) -> dict[str, Any]:
    """The envelope's ``reassign`` block, checked (broker and runner alike)."""
    keys = {"from_names", "to_name", "attorney_names", "litigation_only", "statuses"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError(f"reassign carries exactly {sorted(keys)}")
    to = raw["to_name"]
    if not isinstance(to, str) or not to.strip() or len(to) > MAX_NAME:
        raise ValueError("reassign.to_name must be one name")
    if not isinstance(raw["litigation_only"], bool):
        raise ValueError("reassign.litigation_only must be true or false")
    st = raw["statuses"]
    if not (isinstance(st, list) and st and set(st) <= set(STATUSES) and len(set(st)) == len(st)):
        raise ValueError(f"reassign.statuses must be a non-empty subset of {list(STATUSES)}")
    return {
        "from_names": _name_list(raw["from_names"], "from_names", MAX_FROM, False),
        "to_name": " ".join(to.split()),
        "attorney_names": _name_list(raw["attorney_names"], "attorney_names", MAX_ATTORNEYS, True),
        "litigation_only": raw["litigation_only"],
        "statuses": [s for s in STATUSES if s in st],
    }


__all__ = [
    "DEFAULT_STATUSES",
    "REASSIGN_KEY",
    "REASSIGN_ON",
    "STATUSES",
    "ReassignUnreadable",
    "is_reassignment",
    "parse",
    "validate",
]
