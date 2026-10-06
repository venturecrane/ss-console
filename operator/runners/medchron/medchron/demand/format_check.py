"""Refuse a demand file that is not in the firm's house format, BEFORE filing.

Ported from the engagements laptop tool ``format_check.py`` (2026-09-28), which
``file_to_matter.sh`` ran before writing anything. It checks the shape of the
FILE that would be filed, not the prose:

  1. the name is ``Demand.<Client>.docx``
  2. the RE block carries Our Client / Your Insured / Claim Number / Date of Loss
  3. the first table is the two-row banner: a time-limited demand title, then
     "... Expires at 5:00 P.M. Pacific Time on <Weekday>, <Month D, YYYY>"
  4. a salutation
  5. Summary of Injuries < Liability < Damages < Demand, centered, and an ICD-10 table
  6. the footer carries the firm's settlement-communication markers
  7. "Cordially," then the firm's signature line, a signer, and the title
  8. no drafting machinery left in: skeleton ``[INSERT`` markers, ``PROTO:``
     prototypes, the drafting end-lists, or a curly marker that is not one of
     the open-item markers a requester may ask for
  9. the document properties name only this file

Open items in the firm's bracket dialect (``[TO BE SUPPLIED: ...]``,
``[ATTORNEY: ...]``, ``[CONFIRM ... BEFORE TRANSMITTAL]``) and the three curly
markers a requester can name in her brief (``{{NOT IN RECORD: ...}}``,
``{{FILL: ...}}``, ``{{ATTORNEY: ...}}``, first asked for on 2026-10-06) are
allowed and COUNTED, not refused: they are the draft telling the attorney what
the file does not establish. Any other ``{{`` is a leftover template slot.

What changed from the laptop tool: the firm's signature, title and footer
markers come from the firm config, and the curly open-item markers are allowed.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HEADINGS = ("summary of injuries", "liability", "damages", "demand")
OPEN_ITEM = re.compile(r"\[(TO BE SUPPLIED|ATTORNEY|CONFIRM)[^\]]*\]|\{\{(NOT IN RECORD|FILL|ATTORNEY)\b[^}]*\}\}")
CURLY_ALLOWED = re.compile(r"\{\{(NOT IN RECORD|FILL|ATTORNEY)\b[^}]*\}\}")


@dataclass
class FormatResult:
    fails: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails


def _qn(tag: str) -> str:
    from docx.oxml.ns import qn

    return qn(tag)


def _lines(doc: Any) -> list[tuple[str, str | None, Any]]:
    out = []
    for el in doc.element.body.iterchildren():
        if el.tag == _qn("w:p"):
            out.append(("p", "".join(t.text or "" for t in el.iter(_qn("w:t"))).strip(), el))
        elif el.tag == _qn("w:tbl"):
            out.append(("t", None, el))
    return out


def _centered(el: Any) -> bool:
    jc = el.find(_qn("w:pPr") + "/" + _qn("w:jc"))
    return jc is not None and jc.get(_qn("w:val")) == "center"


def _banner(tables: list[Any], fails: list[str]) -> None:
    if not tables:
        fails.append("no tables: the firm's banner, ICD-10 and Demand tables are missing")
        return
    b = tables[0]
    if len(b.rows) != 2 or len(b.columns) != 1:
        fails.append("first table is not the two-row banner")
    else:
        cells = [r.cells[0].text.strip() for r in b.rows]
        if not re.search(r"time[- ]limited.*demand", cells[0], re.I):
            fails.append(f"banner line 1 is not a time-limited demand title: {cells[0][:60]!r}")
        # The expiry date is the line's point, but on a draft for review it may
        # still be an open item the attorney sets (the 2026-10-06 smoke: a
        # requester's brief asked for {{FILL}} on firm-supplied values).
        date_or_open = r"((\w+day, )?\w+ \d{1,2}, \d{4}|" + OPEN_ITEM.pattern + ")"
        if not re.search(r"expires at 5:00 p\.m\. pacific time on " + date_or_open, cells[1], re.I):
            fails.append(f"banner line 2 is not the expiry line: {cells[1][:70]!r}")
    if not any(t.cell(0, 0).text.strip().lower().startswith("icd") for t in tables):
        fails.append("no ICD-10 Code table under Summary of Injuries")


def _headings(lines: list[tuple[str, str | None, Any]], fails: list[str]) -> None:
    heads = [str(t).lower() for k, t, el in lines if k == "p" and t and _centered(el)]
    pos = []
    for h in HEADINGS:
        idx = [i for i, x in enumerate(heads) if x == h]
        if not idx:
            fails.append(f"section heading {h.title()!r} missing (or not a centered heading)")
        else:
            pos.append(idx[0])
    if pos != sorted(pos):
        fails.append("section headings are out of the firm's order (Summary of Injuries, Liability, Damages, Demand)")


def _machinery(alltext: str, fails: list[str], notes: list[str]) -> None:
    for pat, why in (
        (r"\[INSERT", "an unfilled skeleton [INSERT ...] marker"),
        (r"PROTO:", "a template prototype"),
        (r"HELD OUT PENDING", "the drafting HELD OUT list"),
        (r"^TO BE SUPPLIED$", "the drafting TO BE SUPPLIED end-list"),
        (r"ATTORNEY decisions reserved", "the drafting ATTORNEY end-list"),
    ):
        if re.search(pat, alltext, re.M):
            fails.append(f"letter contains {why}")
    if "{{" in CURLY_ALLOWED.sub("", alltext):
        fails.append("letter contains a curly-brace marker that is not an open-item marker")
    opens = [m.group(1) or m.group(2) for m in OPEN_ITEM.finditer(alltext)]
    if opens:
        notes.append(f"{len(opens)} open item(s) for the attorney: " + ", ".join(sorted(set(opens))))


def check(path: Path, *, signature: str, signer_title: str, footer_markers: list[str]) -> FormatResult:
    import docx

    res = FormatResult()
    name = Path(path).name
    if not re.fullmatch(r"Demand\.[A-Za-z][^/\\]*\.docx", name):
        res.fails.append(f"file name {name!r} is not Demand.<Client>.docx")
    try:
        doc = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - not a docx at all is a refusal, named
        res.fails.append(f"cannot open as .docx: {exc}")
        return res
    lines = _lines(doc)
    texts = [str(t) for k, t, _ in lines if k == "p" and t]
    tables = doc.tables
    for label in ("Our Client:", "Your Insured:", "Claim Number:", "Date of Loss:"):
        if not any(label in t for t in texts[:20]):
            res.fails.append(f"RE block lacks {label!r}")
    _banner(tables, res.fails)
    if not any(t.startswith("Dear ") for t in texts):
        res.fails.append("no salutation")
    _headings(lines, res.fails)
    foot = " ".join(p.text for s in doc.sections for f in (s.footer, s.first_page_footer) for p in f.paragraphs)
    if not all(m in foot for m in footer_markers):
        res.fails.append("footer lacks the firm's settlement-communication line")
    if "Cordially," not in texts:
        res.fails.append("no 'Cordially,' sign-off")
    else:
        blk = texts[texts.index("Cordially,") : texts.index("Cordially,") + 5]
        if signature not in blk or signer_title not in blk:
            res.fails.append(f"signature block is not the firm's: {blk}")
    table_text = " ".join(c.text for t in tables for r in t.rows for c in r.cells)
    _machinery("\n".join(texts) + "\n" + table_text, res.fails, res.notes)
    stem = name[:-5] if name.endswith(".docx") else name
    title = (doc.core_properties.title or "").strip()
    if title and title != stem:
        res.fails.append(f"document title property {title!r} is not this file ({stem!r})")
    with zipfile.ZipFile(str(path)) as z:
        if "docProps/custom.xml" in z.namelist():
            res.fails.append("custom document properties present (they carry a source file's matter ids)")
    return res
