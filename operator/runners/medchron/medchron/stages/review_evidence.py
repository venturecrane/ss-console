"""The cited page as evidence for the pre-delivery review's own policy. $0.

WHY (2026-10-24, the stop audit rows 11 and 12). Two review findings held
chronologies built from legitimate records, each a stop SMD could only clear
by reading the page:

* the system-vocabulary check fired on words the RECORD itself uses (a
  hospital's "pipeline" of referrals, a billing "auditor"). A phrase that
  appears verbatim on the page every sentence carrying it cites is the
  record's wording: the finding becomes a NOTE (``verbatim_on_record``).
* the model's check 1 ("another person's records") and check 3 ("the
  system's language") were never repaired, so a family-history line or a
  shared form held the job. The cited page decides check 1 by its patient
  identifiers (``whose``), the same labels the identity rule reads
  (``gates/foreign_identity``): the client's page, or a page naming no one
  else, makes the finding a NOTE; a page naming another person has the entry
  LEFT OUT BY RULE (``leave_out``) and disclosed without a word about that
  person, as the identity rule merged 2026-10-24 does for a whole record.

Nothing here writes client text: it reads pages and deletes one entry block.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from ..audit.page_text import PageIndex, exhibit_paths
from ..gates import foreign_identity as FI
from .base import StageRun

#: Disclosures of entries left out by rule, beside the package (internal,
#: never uploaded): {"unit", "entries": [{"date", "provider", "exhibit",
#: "pages", "reason"}]}. The Review Notes file carries the same sentence.
LEFT_OUT = "review-left-out.json"
LEFT_OUT_REASON = FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]
_DATED = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*$")
_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_EXHIBIT_PAGE = re.compile(r"(?i)\bExhibit\s+(\d+)(?:\s*[,-]?\s*p(?:age|p)?\.?\s*(\d+))?")


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


class Pages:
    """Every text the run holds for a current exhibit page: its transcript
    (native or vision, through the audit's page index) and the exhibit PDF's
    own text layer. Cached; '' for a page nothing can read."""

    def __init__(self, sr: StageRun) -> None:
        self.sr = sr
        self.out = sr.slug_dir / "out" / sr.unit.unit
        self._cache: dict[tuple[int, int], str] = {}
        self._index: PageIndex | None = None
        try:
            self._index = PageIndex(sr.slug_dir, sr.unit.unit)
        except Exception:  # noqa: BLE001 - no page map or unit file: the PDF text layer is read alone
            self._index = None

    def text(self, exhibit: int, page: int) -> str:
        key = (exhibit, page)
        if key not in self._cache:
            parts: list[str] = []
            if self._index is not None:
                try:
                    at = self._index.resolve(exhibit, page)
                    seg = self._index.segments(at[0]).get(at[1]) if at else None
                    if seg:
                        parts.append(seg[0])
                except Exception:  # noqa: BLE001 - an unreadable transcript is no text; the PDF layer is still read
                    pass
            pdf = exhibit_paths(self.out).get(exhibit)
            if pdf is not None:
                try:
                    import pymupdf

                    with pymupdf.open(str(pdf)) as doc:
                        if 1 <= page <= len(doc):
                            parts.append(doc[page - 1].get_text())
                except Exception:  # noqa: BLE001 - an unreadable PDF page is no text
                    pass
            self._cache[key] = "\n".join(p for p in parts if p)
        return self._cache[key]

    def close(self) -> None:
        if self._index is not None:
            self._index.close()


def _count(phrase: str, text: str) -> int:
    words = phrase.lower().split()
    if not words:
        return 0
    rx = r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in words) + r"(?![a-z0-9])"
    return len(re.findall(rx, (text or "").lower()))


def verbatim_on_record(md: str, phrase: str, pages: Pages) -> bool:
    """True when every occurrence of `phrase` in the chronology is inside a
    cited sentence whose cited page prints it verbatim (case and spacing
    aside). An occurrence in a heading, a table or an uncited line is the
    system's, and so is a phrase no page prints."""
    total = _count(phrase, md)
    if not total:
        return False
    keep = set(exhibit_paths(pages.out))
    backed = 0
    for c in CL.extract_claims(CL.body_of(md), keep):
        n = _count(phrase, c["claim"])
        if not n:
            continue
        cited = CL.parse_pages(c["page_spec"]) or [1]
        if any(_count(phrase, pages.text(int(c["exhibit"]), p)) for p in cited):
            backed += n
    return backed >= total


# ---- check 1: whose page is it -------------------------------------------------------
def entry_blocks(md: str) -> list[tuple[int, int, str]]:
    """(first line, end line, text) of every dated entry in the chronology
    body; an entry runs to the next dated line or the end of the body."""
    lines = md.splitlines(keepends=True)
    out: list[tuple[int, int, str]] = []
    body, start = False, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("## "):
            if start is not None:
                out.append((start, i, "".join(lines[start:i])))
                start = None
            body = s == CL.BODY_START
            continue
        if body and _DATED.match(s):
            if start is not None:
                out.append((start, i, "".join(lines[start:i])))
            start = i
    if start is not None:
        out.append((start, len(lines), "".join(lines[start:])))
    return out


def _provider(block: str) -> str:
    rows = block.split("\n")
    return rows[1].split("|")[0].strip().lower() if len(rows) > 1 else ""


def entry_for(md: str, text: str) -> tuple[int, int, str] | None:
    """The one entry a finding's text names by its date (and its provider,
    when the date has several entries); None when it names no single one."""
    low = text.lower()
    blocks = entry_blocks(md)
    for d in dict.fromkeys(_DATE.findall(text)):
        same = [b for b in blocks if b[2].startswith(d)]
        if len(same) > 1:
            same = [b for b in same if _provider(b[2]) and _provider(b[2]) in low]
        if len(same) == 1:
            return same[0]
    return None


def cited_pages(block: str, text: str) -> list[tuple[int, int]]:
    """The page the finding names, else every page the entry cites."""
    m = _EXHIBIT_PAGE.search(text)
    if m and m.group(2):
        return [(int(m.group(1)), int(m.group(2)))]
    out: list[tuple[int, int]] = []
    for c in CL.CITE.finditer(block):
        ex = int(c.group(1))
        if m and int(m.group(1)) != ex:
            continue
        for p in CL.parse_pages(c.group(2)) or [1]:
            if (ex, p) not in out:
                out.append((ex, p))
    return out


def whose(sr: StageRun, texts: list[str]) -> str:
    """'other' when a cited page labels a patient who is not the client (a
    date of birth or a patient name, read as the identity rule reads them,
    or another client on the matter named where the client is not);
    'client' when the pages name the client (surname or date of birth) and
    no one else; 'unnamed' otherwise (no text, or no one named)."""
    u = sr.unit
    text = "\n".join(t for t in texts if t and t.strip())
    if not text.strip():
        return "unnamed"
    sur = {u.surname} if u.surname else set()
    dobs = {u.dob} if u.dob else set()
    first = set(str(u.client_name).split()[:1])
    if FI.findings(text, sur, dobs, first):
        return "other"
    mine = _count(str(u.client_name), text)
    for o in sr.job.units:
        if o.unit != u.unit and _count(str(o.client_name), text) and not mine:
            return "other"
    named = any(_count(x, text) for x in sur) or bool(u.dob and u.dob in text)
    return "client" if named else "unnamed"


def leave_out(
    sr: StageRun, doc: Path, block: tuple[int, int, str], pages: list[tuple[int, int]], reason: str = LEFT_OUT_REASON
) -> None:
    """The entry block out of the chronology (and out of
    ``entries_scoped_final.md`` when it is there verbatim, so a rebuild does
    not bring it back), and its disclosure recorded in ``LEFT_OUT``."""
    md = doc.read_text(encoding="utf-8")
    lines = md.splitlines(keepends=True)
    start, end, text = block
    doc.write_text(re.sub(r"\n{3,}", "\n\n", "".join(lines[:start] + lines[end:])), encoding="utf-8")
    ents = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if ents.is_file():
        body = ents.read_text(encoding="utf-8")
        stripped = text.strip("\n")
        if body.count(stripped) == 1:
            ents.write_text(re.sub(r"\n{3,}", "\n\n", body.replace(stripped, "", 1)), encoding="utf-8")
    out = sr.slug_dir / "out" / sr.unit.unit
    p = out / LEFT_OUT
    rec: dict[str, Any] = (
        json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"unit": sr.unit.unit, "entries": []}
    )
    rows = text.split("\n")
    rec["entries"].append(
        {
            "date": rows[0].strip(),
            "provider": rows[1].split("|")[0].strip() if len(rows) > 1 else "",
            "exhibit": pages[0][0] if pages else None,
            "pages": [pg for _ex, pg in pages],
            "reason": reason,
        }
    )
    out.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
