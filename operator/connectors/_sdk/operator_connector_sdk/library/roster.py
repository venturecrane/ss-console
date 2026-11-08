"""Who a firm's attorneys are: ONE roster per firm, read by every document path
(design 2026-11-06, "Unified: who an attorney is").

THE FILE. ``attorneys.json``, a firm input authored in the private engagements
repo under ``operator/customers/<slug>/attorneys/`` and installed on the seat by
the root firm-inputs applier as ``/var/lib/smd-config/attorneys/attorneys.json``
(``SMD_ATTORNEY_ROSTER_DIR`` overrides the directory). One entry per attorney:

    {"schema_version": 1,
     "attorneys": [
       {"name": "Jane Attorney",                  the case system's staff name, exactly
        "emails": ["jane@firm.example"],          the staff member's mail, folded
        "bar_number": "123456",                   optional; printed in caption_block
        "caption_block": ["Jane Q. Attorney, Esq. (SBN 123456)", "..."],  optional
        "signs": {"demand": {"signer": "...", "title": "...", "initials": "..."},
                  "letters": {"name": "...", "title": "...", "initials": "..."}}}],
     "staff_signers": [
       {"name": "Pat Assistant", "signs": {"letters": {"name": "...", "title": "..."}}}]}

``name`` is the identity: the matter's responsible-attorney field resolves to it
("first last" off the staff record), and the library ledger files an attorney's
own file under it folded (:func:`fold`, the same fold as ``caller.attorney_key``).
``signs`` is how that person signs each family of document, authored from the
firm's own documents and never inferred: a demand's signer, title and initials
line differ from the same attorney's form letters at a real firm, so one block
cannot serve both without changing a delivered letter. ``staff_signers`` are
people who sign form letters and are NOT attorneys: they are never an attorney
for a caption, a demand or a library file.

THE THREE STATES. :func:`load` answers ``ok``, ``absent`` (no roster installed:
every reader uses the list it used before the roster existed, so nothing on a
seat changes until a roster is published there) or ``unusable`` (a roster is
installed and cannot be read or does not validate: every reader stops, never
treating "could not read" as "nobody").

TWIN. ``operator/runners/jobs/jobs/attorneys/roster.py`` is this file byte for
byte (``runners/jobs/tests/test_attorney_roster.py``): the firm-inputs publish
validates a roster with the runner alone on its path, where this toolkit is not
installed. STANDARD LIBRARY ONLY, no relative import.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
FILE_NAME = "attorneys.json"
DIR_ENV = "SMD_ATTORNEY_ROSTER_DIR"
DEFAULT_DIR = "/var/lib/smd-config/attorneys"

OK = "ok"
ABSENT = "absent"
UNUSABLE = "unusable"

DEMAND = "demand"
LETTERS = "letters"
FAMILIES = (DEMAND, LETTERS)

_TOP = frozenset({"schema_version", "attorneys", "staff_signers"})
_ATTORNEY = frozenset({"name", "emails", "bar_number", "caption_block", "signs"})
_STAFF = frozenset({"name", "signs"})
_DEMAND_KEYS = ("signer", "title", "initials")
_LETTER_KEYS = frozenset({"name", "title", "initials"})
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BAR = re.compile(r"^[A-Za-z0-9-]{1,20}$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def fold(name: str | None) -> str:
    """A name with case and spacing folded: the key a roster entry is found by."""
    return re.sub(r"\s+", " ", name or "").strip().casefold()


@dataclass(frozen=True)
class Person:
    """One roster entry. ``attorney`` is False for a staff signer."""

    name: str
    attorney: bool
    emails: tuple[str, ...] = ()
    bar_number: str = ""
    caption_block: tuple[str, ...] = ()
    signs: dict[str, dict[str, str]] = field(default_factory=dict, compare=False)

    @property
    def key(self) -> str:
        return fold(self.name)


@dataclass(frozen=True)
class Roster:
    """A firm's roster as the seat read it. Only ``ok`` carries people."""

    status: str
    reason: str = ""
    people: tuple[Person, ...] = ()

    @property
    def attorneys(self) -> tuple[Person, ...]:
        return tuple(p for p in self.people if p.attorney)

    def by_name(self, name: str | None) -> Person | None:
        """The one person ``name`` is, by case system name folded; None when nobody."""
        want = fold(name)
        return next((p for p in self.people if want and p.key == want), None)

    def by_email(self, email: str | None) -> Person | None:
        """The one ATTORNEY who has ``email``; staff signers carry no mail."""
        want = (email or "").strip().casefold()
        return next((p for p in self.attorneys if want and want in p.emails), None)

    def is_attorney(self, name: str | None) -> bool:
        found = self.by_name(name)
        return found is not None and found.attorney

    def signature(self, person: Person, family: str) -> dict[str, str] | None:
        """How ``person`` signs a document of ``family`` (DEMAND or LETTERS), as
        authored; None when the roster authors none for that family."""
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}")
        found = person.signs.get(family)
        return dict(found) if found else None


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _lines(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(x) for x in value)


def _demand_problems(where: str, value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != set(_DEMAND_KEYS):
        return [f"{where}: expected exactly {{signer, title, initials}}"]
    out = [f"{where}.{k}: expected a non-empty string" for k in ("signer", "title") if not _nonempty(value[k])]
    if not isinstance(value["initials"], str):
        out.append(f"{where}.initials: expected a string (empty for no initials line)")
    if isinstance(value["title"], str) and any(m != "client" for m in _PLACEHOLDER.findall(value["title"])):
        out.append(f"{where}.title: the only placeholder a title may carry is {{client}}")
    return out


def _letters_problems(where: str, value: Any) -> list[str]:
    if not isinstance(value, dict) or not value or not set(value) <= _LETTER_KEYS:
        return [f"{where}: expected some of {{name, title, initials}}"]
    return [f"{where}.{k}: expected a non-empty string" for k, v in value.items() if not _nonempty(v)]


def _signs_problems(where: str, value: Any, families: tuple[str, ...]) -> list[str]:
    if not isinstance(value, dict) or not set(value) <= set(families):
        return [f"{where}: expected a map of {list(families)}"]
    out = _demand_problems(f"{where}.{DEMAND}", value[DEMAND]) if DEMAND in value else []
    return out + (_letters_problems(f"{where}.{LETTERS}", value[LETTERS]) if LETTERS in value else [])


def _attorney_problems(where: str, a: dict[str, Any]) -> list[str]:
    out: list[str] = []
    emails = a.get("emails")
    if not isinstance(emails, list) or not all(isinstance(e, str) and _EMAIL.match(e.strip()) for e in emails):
        out.append(f"{where}.emails: expected a list of email addresses")
    bar = a.get("bar_number")
    if bar is not None and not (isinstance(bar, str) and _BAR.match(bar)):
        out.append(f"{where}.bar_number: expected letters, digits or dashes")
    block = a.get("caption_block")
    if block is not None and not _lines(block):
        out.append(f"{where}.caption_block: expected a non-empty list of non-empty lines")
    elif isinstance(bar, str) and block is not None and not any(bar in line for line in block):
        out.append(f"{where}.bar_number: the caption block does not print it")
    if "signs" in a:
        out += _signs_problems(f"{where}.signs", a["signs"], FAMILIES)
    return out


def _entry_problems(where: str, entry: Any, keys: frozenset[str], attorney: bool) -> list[str]:
    required = {"name", "emails"} if attorney else {"name", "signs"}
    if not isinstance(entry, dict) or not required <= set(entry) <= keys:
        return [f"{where}: expected {sorted(required)} and optionally {sorted(keys - required)}"]
    out = [] if _nonempty(entry["name"]) else [f"{where}.name: expected the case system's staff name"]
    if attorney:
        return out + _attorney_problems(where, entry)
    if not isinstance(entry["signs"], dict) or LETTERS not in entry["signs"]:
        return out + [f"{where}.signs: a staff signer signs letters only, and must say how"]
    return out + _signs_problems(f"{where}.signs", entry["signs"], (LETTERS,))


def _unique_problems(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    names: set[str] = set()
    emails: set[str] = set()
    for section in ("attorneys", "staff_signers"):
        for i, entry in enumerate(doc.get(section) or []):
            if not isinstance(entry, dict):
                continue
            key = fold(entry.get("name") if isinstance(entry.get("name"), str) else "")
            if key and key in names:
                out.append(f"{section}[{i}].name: the same person appears twice")
            names.add(key)
            for raw in (entry.get("emails") or []) if section == "attorneys" else []:
                e = raw.strip().casefold() if isinstance(raw, str) else ""
                if e and e in emails:
                    out.append(f"{section}[{i}].emails: an address another entry has")
                emails.add(e)
    return out


def problems(doc: Any) -> list[str]:
    """Every reason ``doc`` is not a roster; empty when it is one."""
    if not isinstance(doc, dict) or not {"schema_version", "attorneys"} <= set(doc) <= _TOP:
        return [f"a roster's keys are schema_version, attorneys and optionally staff_signers ({FILE_NAME})"]
    if doc["schema_version"] != SCHEMA_VERSION:
        return [f"schema_version: expected {SCHEMA_VERSION}"]
    out: list[str] = []
    for section, keys, attorney in (("attorneys", _ATTORNEY, True), ("staff_signers", _STAFF, False)):
        value = doc.get(section, [])
        if not isinstance(value, list) or (attorney and not value):
            out.append(f"{section}: expected a {'non-empty ' if attorney else ''}list")
            continue
        out += [p for i, e in enumerate(value) for p in _entry_problems(f"{section}[{i}]", e, keys, attorney)]
    return out or _unique_problems(doc)


def _person(entry: dict[str, Any], attorney: bool) -> Person:
    return Person(
        name=" ".join(entry["name"].split()),
        attorney=attorney,
        emails=tuple(e.strip().casefold() for e in entry.get("emails") or []),
        bar_number=entry.get("bar_number") or "",
        caption_block=tuple(entry.get("caption_block") or ()),
        signs={k: {f: str(v) for f, v in s.items()} for k, s in (entry.get("signs") or {}).items()},
    )


def parse(text: str) -> Roster:
    """A roster from the file's text: ``ok``, or ``unusable`` with the first problem."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return Roster(UNUSABLE, f"{FILE_NAME} is not JSON ({type(exc).__name__})")
    found = problems(doc)
    if found:
        return Roster(UNUSABLE, f"{FILE_NAME}: {found[0]}" + (f" (and {len(found) - 1} more)" if found[1:] else ""))
    people = [_person(e, True) for e in doc["attorneys"]] + [_person(e, False) for e in doc.get("staff_signers", [])]
    return Roster(OK, people=tuple(people))


def letters_entry(row: dict[str, Any]) -> dict[str, str]:
    """A form-letter signature with blank fields dropped, as the form letters read one."""
    return {k: str(v).strip() for k, v in row.items() if isinstance(v, str) and v.strip()}


def disagreements(
    roster: Roster,
    *,
    drafting: list[dict[str, Any]] | None = None,
    demand: dict[str, dict[str, Any]] | None = None,
    letters: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Each way a lane's own attorney list says something the roster does not:
    the drafting inputs' caption blocks (by email), the demand inputs' signer
    map (by name), the form letters' signers (by name). The roster wins; these
    are what SMD is paged to settle. Positions only, never a person's name."""
    out: list[str] = []
    for i, row in enumerate(drafting or []):
        who = roster.by_email(str(row.get("email") or ""))
        if who is None:
            out.append(f"drafting attorneys[{i}]: its email is no attorney's on the roster")
        elif tuple(row.get("block") or ()) != who.caption_block:
            out.append(f"drafting attorneys[{i}]: its caption block is not the roster's")
    for i, (name, row) in enumerate((demand or {}).items()):
        who = roster.by_name(name)
        sig = who.signs.get(DEMAND) if who is not None and who.attorney else None
        if who is None or not who.attorney:
            out.append(f"demand attorneys entry {i + 1}: not an attorney on the roster")
        elif sig is None or any(str(row.get(k) if row.get(k) is not None else "") != sig[k] for k in _DEMAND_KEYS):
            out.append(f"demand attorneys entry {i + 1}: its signature is not the roster's")
    for i, (key, row) in enumerate((letters or {}).items()):
        who = roster.by_name(key)
        if who is None:
            out.append(f"form letter signers entry {i + 1}: nobody on the roster by that name")
        elif letters_entry(row) != letters_entry(who.signs.get(LETTERS) or {}):
            out.append(f"form letter signers entry {i + 1}: its signature is not the roster's")
    return out


def roster_dir(seat: str | Path | None = None) -> Path:
    """The installed roster's directory: ``seat`` when given, else the env, else the seat default."""
    return Path(seat) if seat is not None else Path(os.environ.get(DIR_ENV) or DEFAULT_DIR)


def load(seat: str | Path | None = None) -> Roster:
    """The seat's roster. ``absent`` when no roster is installed (no directory,
    and no link to one); ``unusable`` when one is installed and cannot be read."""
    folder = roster_dir(seat)
    if not os.path.lexists(folder):
        return Roster(ABSENT)
    try:
        text = (folder / FILE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Roster(UNUSABLE, f"the installed roster could not be read ({type(exc).__name__})")
    return parse(text)


__all__ = [
    "ABSENT",
    "DEFAULT_DIR",
    "DEMAND",
    "DIR_ENV",
    "FAMILIES",
    "FILE_NAME",
    "LETTERS",
    "OK",
    "SCHEMA_VERSION",
    "UNUSABLE",
    "Person",
    "Roster",
    "disagreements",
    "fold",
    "letters_entry",
    "load",
    "parse",
    "problems",
    "roster_dir",
]
