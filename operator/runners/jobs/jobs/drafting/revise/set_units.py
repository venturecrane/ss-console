"""One filed discovery response of a set revision, without the seat: read it,
prove it, choose it, change it. Pure over files on disk, so the job
(``set_run.py``) and the $0 rehearsal (``set_rehearse.py``) run the same code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import format_check
from . import inplace, layout as lay, prior, responses as rs

#: What a request may call each set type; a set type it names is in scope.
NAMED: dict[str, re.Pattern[str]] = {
    "special_interrogatories": re.compile(r"\bspecial\s+(?:rog|interrogator)|\bSROGs?\b", re.I),
    "form_interrogatories": re.compile(r"\bform\s+(?:rog|interrogator)|\bFROGs?\b", re.I),
    "requests_for_production": re.compile(r"\bproduction\b|\bRFPs?\b|\bdocument\s+requests?\b", re.I),
    "requests_for_admission": re.compile(r"\badmissions?\b|\bRFAs?\b", re.I),
}
_INTERROGATORIES = re.compile(r"\binterrogator(?:y|ies)\b|\brogs\b", re.I)


def named_set_types(request_text: str) -> set[str]:
    """The set types the request names; none named is every set. "the
    interrogatories" with neither special nor form names both."""
    got = {kind for kind, pat in NAMED.items() if pat.search(request_text or "")}
    if not got & {"special_interrogatories", "form_interrogatories"} and _INTERROGATORIES.search(request_text or ""):
        got |= {"special_interrogatories", "form_interrogatories"}
    return got


def in_scope(request_text: str, set_types: list[str]) -> list[bool]:
    named = named_set_types(request_text)
    return [not named or t in named for t in set_types]


def edit_file(filed: Path, md: str, out: Path) -> tuple[inplace.Edit, list[str]]:
    """``md`` made in the filed file, saved to ``out``: (the edit, where the
    result differs from the filed XML beyond the paragraphs it rewrote)."""
    ed = inplace.open_filed(filed)
    before = inplace.snapshot(ed)
    inplace.edit_content(ed, md)
    blob = inplace.save(ed, out)
    return ed, inplace.prove(ed, before, filed.read_bytes(), blob, md)


def read_base(filed: Path, work: Path) -> tuple[str, rs.Document]:
    """The filed file as markdown and responses, proven to write back unchanged."""
    md = prior.docx_to_md(filed)
    ed, diffs = edit_file(filed, md, work / "base-roundtrip.docx")
    if diffs or ed.touched or ed.removed:
        raise BaseUnproven(
            "the filed document does not read back element for element ("
            + "; ".join(diffs or [f"{len(ed.touched)} element(s) moved on a round trip"])[:300]
            + ")"
        )
    try:
        return md, rs.read(md)
    except rs.ResponsesUnread as exc:
        raise BaseUnproven(f"its responses cannot be pinned to request numbers: {exc}") from None


class BaseUnproven(ValueError):
    pass


def served_numbers(records: list[dict[str, Any]], name: str, set_type: str) -> list[int] | None:
    """The request numbers served for the set this file answers, from the prior
    jobs' kept served sets (``forms/saved.SERVED_FILES``): the set the file was
    filed for by name, else the one set of its type. None when none is known."""
    from ..served import ServedSet, numbering

    stem = name[:-5] if name.lower().endswith(".docx") else name
    for rec in records:
        sets = [ServedSet(**s) for s in rec.get("served") or []]
        by_label = {s.label: s for s in sets}
        for row in rec.get("served_outcomes") or []:
            doc = str(row.get("document") or "")
            if doc in (name, stem) and row.get("set") in by_label:
                return numbering(by_label[row["set"]].request_text)
    of_type = [ServedSet(**s) for rec in records for s in rec.get("served") or [] if s.get("set_type") == set_type]
    return numbering(of_type[0].request_text) if len(of_type) == 1 else None


def prior_records(data: Path) -> list[dict[str, Any]]:
    from ..forms.saved import PRIOR_RECORDS

    d = Path(data) / PRIOR_RECORDS
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def revise_one(
    base_md: str, filed: Path, values: list[rs.Value], out: Path, cls: str, fmt: dict[str, Any] | None
) -> dict[str, Any]:
    """The values made in the filed file at ``out``, proven: only the responses
    they name changed, the XML unchanged beyond the paragraphs rewritten, and no
    format finding the filed file did not already have (``fmt`` None: a
    rehearsal without the firm's inputs, no format check). Raises ``Refused``."""
    doc = rs.read(base_md)
    new, did, skipped = rs.apply(doc, values)
    rec: dict[str, Any] = {"did": did, "skipped": skipped, "changed": [], "out": None}
    if new.md == doc.md:
        return rec
    ed, diffs = edit_file(filed, new.md, out)
    if diffs:
        raise Refused("revise_layout_lost", "the revision changes what no rule names: " + "; ".join(diffs)[:300])
    numbers, outside = rs.changed(doc.md, new.md)
    allowed = {v.request for v in values}
    if outside or not set(numbers) <= allowed:
        raise Refused("revise_layout_lost", f"words outside the responses the rules name changed ({numbers})")
    if fmt is not None:
        paper = str(lay.read(filed)["paper"])
        before = format_check.check(filed, cls, fmt, paper=paper).fails
        new_fails = [f for f in format_check.check(out, cls, fmt, paper=paper).fails if f not in before]
        if new_fails:
            raise Refused("format_check", "the revision brings in: " + "; ".join(new_fails)[:300])
    rec.update(changed=numbers, out=str(out), touched=len(ed.touched))
    return rec


class Refused(ValueError):
    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}")
        self.code, self.why = code, why
