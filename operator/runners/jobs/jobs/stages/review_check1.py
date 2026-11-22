"""The pre-delivery review's check 1 ("another person's records"), decided on
the cited pages. $0: it reads pages and never writes client text.

WHY (2026-11-22, three holds on one update and one on a full chronology,
each released by hand):

* An update never reached this decision: ``review_policy.settle`` had no
  document on an update and went straight to its last step, so every model
  check 1 MUST-FIX on an update held. An update is now decided on the
  document it will deliver (``update/updated-chronology.md``), read only:
  the client's page, or a page naming no one, makes the finding a NOTE as on
  a full chronology. A page naming another person keeps it MUST-FIX on an
  update, so the update holds: an update's entries leave only through the
  update's own assemble, render and proofs, which run before this review,
  and the review never edits a document those proofs already read.
* A family member's health fact written in the client's own record (a
  parent's results the client forwards, a child's diagnosis the client
  reports) is the client's family history, not another person's record. A
  page whose identifiers name the client before any other patient label,
  where the finding or the entry attributes the fact to a family member, is
  the client's own record: a NOTE, kept and disclosed. A page HEADED with
  another patient's name and date of birth is still another person's.
* A finding whose date has many entries and that names no provider or page
  is narrowed by a phrase it quotes (``review_evidence.candidates``). When
  several entries remain, every candidate is decided: none on another
  person's page, a NOTE disclosed in the Review Notes; any on another
  person's page, it stays MUST-FIX.

Who is named is read only through ``review_evidence.whose`` (the identity
rule's labels), so a change to that rule changes this decision with it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..proof import review
from .base import StageRun

#: Words that attribute a health fact to a member of the client's family.
FAMILY = re.compile(
    r"(?i)\b(?:family\s+history|mother|father|mom|dad|parents?|sons?|daughters?|siblings?|brothers?|sisters?"
    r"|spouse|husband|wife|child|children|grandmother|grandfather|grandparents?)(?:'s)?\b"
)
#: Where an update keeps the chronology it will deliver.
UPDATE_DOC = Path("update") / "updated-chronology.md"


def own_record(sr: StageRun, text: str) -> bool:
    """True when the page names the client before any line that labels
    another patient: the page is the client's, whatever it later quotes. A
    page headed with another patient's identifiers names no client first."""
    from . import review_evidence as EV

    lines = (text or "").splitlines()
    for i in range(len(lines)):
        if EV.whose(sr, ["\n".join(lines[: i + 1])]) == "other":
            return EV.whose(sr, ["\n".join(lines[:i])]) == "client"
    return EV.whose(sr, [text]) == "client"


def kind_of(sr: StageRun, texts: list[str], about: str) -> str:
    """'client', 'unnamed', 'family' or 'other' for the cited page texts;
    `about` is the finding and its entry, read for a family attribution."""
    from . import review_evidence as EV

    kind = EV.whose(sr, texts)
    if kind != "other" or not FAMILY.search(about):
        return kind
    other = [t for t in texts if t and t.strip() and EV.whose(sr, [t]) == "other"]
    return "family" if other and all(own_record(sr, t) for t in other) else "other"


_WHOM = {
    "client": "which is the client's",
    "unnamed": "which names no other person; the entry is kept",
    "family": "which is the client's own record; the family member's health fact it reports is the client's "
    "family history, and the entry is kept",
}
_STAT = {"client": "identity_client", "unnamed": "identity_unnamed", "family": "identity_family"}


def decide(sr: StageRun, md: str, f: review.Finding, pages: Any) -> tuple[str, Any, list[tuple[int, int]], int]:
    """(kind, the one entry or None, its cited pages, how many entries it may
    be about) for a check 1 finding.
    'none' when no entry carries the finding's date. Several entries left:
    'other' when any candidate's cited pages are another person's, else
    'several' (every candidate is the client's, or names no one)."""
    from . import review_evidence as EV

    text = f"{f.finding} {f.cite}"
    blocks = EV.candidates(md, text)
    if not blocks:
        return "none", None, [], 0
    kinds = []
    for b in blocks:
        cited = EV.cited_pages(b[2], text)
        kinds.append((kind_of(sr, [pages.text(ex, pg) for ex, pg in cited], f"{text}\n{b[2]}"), b, cited))
    if len(kinds) == 1:
        return (*kinds[0], 1)
    if any(k == "other" for k, _b, _c in kinds):
        return "other", None, [], len(kinds)
    return "several", None, [], len(kinds)


def note(f: review.Finding, kind: str, n: int = 0) -> review.Finding:
    """The finding as a NOTE for the attorney, saying what the pages showed."""
    if kind == "several":
        return replace(
            f,
            grade=review.NOTE,
            finding=f"checked against the cited pages of all {n} entries this finding may be about, none of which "
            f"names another person; the entries are kept: {f.finding}",
        )
    return replace(f, grade=review.NOTE, finding=f"checked against the cited page, {_WHOM[kind]}: {f.finding}")


def on_update(sr: StageRun, rd: Path, out: Any) -> None:
    """An update's model check 1 MUST-FIX, decided on the document it will
    deliver; read only. Another person's page keeps it MUST-FIX (it holds)."""
    from . import review_evidence as EV
    from .review_policy import check_no

    doc = rd / UPDATE_DOC
    todo = [k for k, f in enumerate(out.findings) if f.grade == review.MUST and f.by == "model" and check_no(f) == 1]
    if not todo or not doc.is_file():
        return
    md = doc.read_text(encoding="utf-8")
    pages = EV.Pages(sr)
    try:
        for k in todo:
            f = out.findings[k]
            kind, _block, _cited, n = decide(sr, md, f, pages)
            if kind in ("none", "other"):
                sr.log(f"review: on the update, a check 1 finding stays must-fix (its cited page: {kind})")
                continue
            out.findings[k] = note(f, kind, n)
            sr.log(f"review: on the update, a check 1 finding checked against its cited page ({kind}), a note")
    finally:
        pages.close()


def stat_key(kind: str) -> str:
    return _STAT.get(kind, "identity_several")
