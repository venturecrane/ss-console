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
#: An aside: a set named in it is an example, never the document to change.
_PAREN = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_EXAMPLE = re.compile(r"\b(?:such\s+as|e\.\s?g\.|for\s+(?:example|instance))[^.;:\n]*", re.I)
#: A set named as where something already is ("identified in the special
#: interrogatories") is a reference, not the document to change.
_REFERENCE = re.compile(r"\b(?:identified|listed|disclosed|referenced|elsewhere)\b[^.;:\n]{0,40}$", re.I)


def _without_asides(text: str) -> str:
    prev = None
    while prev != text:  # nested parentheses, innermost first
        prev, text = text, _PAREN.sub(" ", text)
    return _EXAMPLE.sub(" ", text)


def _targets(pat: re.Pattern[str], text: str) -> bool:
    return any(not _REFERENCE.search(text[max(0, m.start() - 60) : m.start()]) for m in pat.finditer(text))


def named_set_types(request_text: str) -> set[str]:
    """The set types the request names as the documents to change; none named
    is every set. "the interrogatories" with neither special nor form names
    both. A set named in an aside (parentheses, "such as", "e.g.", "for
    example") or as where something already is, is not named: on 2026-11-21 a
    rule about document requests said not to list documents already identified
    elsewhere "(such as in form or special interrogatories)", and reading that
    as the target dropped the production responses the email asked to fix."""
    text = _without_asides(request_text or "")
    got = {kind for kind, pat in NAMED.items() if _targets(pat, text)}
    if not got & {"special_interrogatories", "form_interrogatories"} and _targets(_INTERROGATORIES, text):
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
    kind of format finding the filed file did not already have (``finding_kind``;
    ``fmt`` None: a
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
        before = {finding_kind(f) for f in format_check.check(filed, cls, fmt, paper=paper).fails}
        new_fails = [f for f in format_check.check(out, cls, fmt, paper=paper).fails if finding_kind(f) not in before]
        if new_fails:
            raise Refused("format_check", "the revision brings in: " + "; ".join(new_fails)[:300])
    rec.update(changed=numbers, out=str(out), touched=len(ed.touched))
    return rec


_COUNT = re.compile(r"\d+(?:\.\d+)?")


def finding_kind(finding: str) -> str:
    """A format finding with its counts removed. A filed file that already
    fails the firm's format check keeps failing it after an edit with other
    counts ("font: 182 run(s)" becomes "font: 178 run(s)"): the same kind, not
    a new one. Only a kind the filed file did not have is brought in."""
    return _COUNT.sub("#", finding)


class Refused(ValueError):
    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}")
        self.code, self.why = code, why
