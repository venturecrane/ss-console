"""medchron_foreign_identity: does a document's text name a DIFFERENT person
than the client it is about to be read for?

Found live 2026-10-13: an update was about to fold another person's emailed
records into a client's file. An email attachment arrives with no folder to
say whose it is, and a document an agent names by id carries no folder rule
either, so the only evidence left is the document's own text. This reads the
two things a medical record labels its patient with:

* a labeled date of birth ("DOB:", "Date of Birth:", "Birth Date:") that is
  not the client's date of birth; a line that labels a relative or a payer
  ("guarantor", "insured", "subscriber", "parent", "mother", "father",
  "spouse", "emergency contact") is that person's, not the patient's, and is
  not read;
* a labeled patient name ("Patient:", "Patient Name:") that does not carry
  the client's surname.

A hit is a HOLD at the caller, never a silent skip and never a fold: a
person reads the document and releases it (``identity_release.json`` in the
job's matter directory, ``{"release": [<id or sha12>, ...]}``), because a
co-plaintiff's records inside a client's chronology is the defect class
(ss#2893) and a record wrongly withheld is recoverable. Text that names no
one is not evidence of anyone: it passes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DOB_LABEL = re.compile(
    r"(?i)\b(?:D\.?O\.?B\.?|Date\s+of\s+Birth|Birth\s*Date)\s*[:#]?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"
)
# The label is case-blind, the name is not: two capitalised words ("Jane Doe",
# "DOE, JANE"), so "Patient: denies pain" is not read as a name.
PATIENT_LABEL = re.compile(
    r"(?m)\b(?i:patient(?:\s+name)?)\s*[:#]\s*([A-Z][A-Za-z'\-]+(?:,\s*|\s+)[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-.]*){0,2})"
)
#: Capitalised words a form puts after "Patient:" that are not a name.
NOT_NAME = frozenset(
    "denies reports states presents complains seen here was is has had states noted tolerated returns arrived "
    "advised instructed declined education name information history status signature consent".split()
)
RELATIVE = re.compile(r"(?i)\b(?:guarantor|insured|subscriber|policy ?holder|parent|mother|father|spouse|emergency)\b")
RELEASE_FILE = "identity_release.json"


def _norm_dob(mo: str, dy: str, yr: str) -> str | None:
    y = int(yr)
    y = y + (1900 if y > 30 else 2000) if y < 100 else y
    try:
        return f"{int(mo):02d}/{int(dy):02d}/{y:04d}"
    except ValueError:
        return None


def full_name(name: str, after: str) -> bool:
    """True when the capitalised words after "Patient:" are a person's name:
    none is a form word ("Denies", "Reports"), and the name ends where a name
    ends (the line, a delimiter, another label, or running lowercase text),
    so "Patient: Denies Neck Pain" is not read as someone else."""
    words = re.findall(r"[A-Za-z'\-]+", name)
    if any(w.lower() in NOT_NAME for w in words):
        return False
    rest = after.lstrip()
    if not rest or rest[:1].islower():
        return True
    return bool(re.match(r"(?:[|,;:(]|(?i:dob|d\.o\.b|date|mrn|acct|account|sex|age|id)\b)", rest))


def _has(word: str, text: str) -> bool:
    return bool(re.search(rf"(?i)(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text))


def _conflicts(name: str, sur: set[str], firsts: set[str], text: str) -> bool:
    """True when a "Patient:" name is someone else's. A name without the
    client's surname that carries the client's FIRST name, in a document that
    names the client's surname elsewhere, is the client written short (a
    billing summary's "Patient: Alpha" held a real update, 2026-10-13); it
    does not conflict. Any other name without the surname does."""
    if not sur or any(_has(s, name) for s in sur):
        return False
    first_only = any(_has(f, name) for f in firsts)
    return not (first_only and any(_has(s, text) for s in sur))


def first_names(units: list[Any]) -> set[str]:
    """The first word of each unit's client name."""
    return {str(u.client_name).split()[0] for u in units if str(getattr(u, "client_name", "") or "").split()}


def findings(
    text: str, surnames: set[str], dobs: set[str], firsts: set[str] | None = None, context: str | None = None
) -> list[str]:
    """What in `text` names someone other than the client (surnames, first
    names and MM/DD/YYYY dobs of every unit on the job); [] when nothing
    does. `context`: the whole document `text` is a page of, where the
    client's surname is looked for (default `text`)."""
    out: list[str] = []
    sur = {s.lower() for s in surnames if s}
    for line in (text or "").splitlines():
        if RELATIVE.search(line):
            continue
        for m in DOB_LABEL.finditer(line):
            d = _norm_dob(*m.groups())
            if d and dobs and d not in dobs:
                out.append(f"a date of birth {d} that is not the client's")
        for m in PATIENT_LABEL.finditer(line):
            if full_name(m.group(1), line[m.end() :]) and _conflicts(
                m.group(1), sur, firsts or set(), text if context is None else context
            ):
                out.append("a patient name without the client's surname")
    return sorted(set(out))


def released(slug_dir: Path) -> set[str]:
    p = slug_dir / RELEASE_FILE
    if not p.is_file():
        return set()
    try:
        return {str(x) for x in (json.loads(p.read_text(encoding="utf-8")) or {}).get("release") or []}
    except (OSError, ValueError, AttributeError):
        return set()


def identities(units: list[Any]) -> tuple[set[str], set[str]]:
    """(surnames, dobs) of the job's units."""
    return {u.surname for u in units if u.surname}, {u.dob for u in units if u.dob}


def pdf_text(path: Path) -> str:
    """The text layer of a PDF (or a Word file), '' for anything without one."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            import pymupdf

            with pymupdf.open(str(path)) as doc:
                return "\n".join(pg.get_text() for pg in doc)
        if suffix == ".docx":
            import docx

            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    except Exception:  # noqa: BLE001 - an unreadable attachment has no text to name anyone; vision reads it later and the identity stage checks again
        return ""
    return ""
