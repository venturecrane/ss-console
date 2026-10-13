"""The attorney's own sets, placed in the caption on the firm's paper.

An attorney often drafts his own discovery (on 2026-10-12, Chris drafted three
sets in his own Claude project) and then pastes each set by hand into a
caption on pleading paper. This is that step, done for him: a drafting
request (``discovery_set``) whose email ATTACHES Word documents that are sets
of discovery places each one, as its own paper, under:

* the caption read from the court's own filings (``caption_stage``, the same
  read every court-class job makes; never Smokeball's fields, house style U2);
* the firm's authored attorney block (``drafting-firm.yaml`` ``attorneys``);
* the set's title (his own title line when his document carries one; else
  built from his identification block and the caption's parties);
* his text, VERBATIM: every paragraph of his set from the identification block
  (or the first request) to its end, in order, words unchanged. The job never
  rewrites, audits, repairs or composes an attorney's own requests;
* his signature block when he wrote one, else the firm's signature lines from
  the authored attorney block; his proof of service when he wrote one, else
  the firm's (house style D8);
* the firm's pleading paper when the request asks for it (``pleading.py``).

Nothing here is a paid compose. What is checked before filing: the verbatim
rule (every paragraph of his set appears, in order, in the rendered file), one
proof of service per paper, one kind of set per paper, the paper asked for,
and the independent review scoped to what the job added (``review.py``).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

from . import sets

_IDENT = re.compile(r"(?i)^\W*propounding\s+party\b")
_TITLE = re.compile(
    r"(?i)\b(special(?:ly prepared)?\s+interrogatories|requests?\s+for\s+(?:admissions?|production(?:\s+of\s+documents)?))\b"
)
_SIGNATURE = re.compile(r"(?i)^\W*(dated|date)\s*:|^\W*by\s*:")
_POS = re.compile(r"(?i)^\W*proof\s+of\s+service\W*$")
_SET_NO = re.compile(r"(?i)^\W*set\s+(?:no\.?|number)\s*:?\s*(.+)$")
_HEADING_MAX = 80
VARIANT_TITLES = {
    "special_interrogatories": "SPECIAL INTERROGATORIES",
    "requests_for_admission": "REQUESTS FOR ADMISSION",
    "requests_for_production": "REQUESTS FOR PRODUCTION OF DOCUMENTS",
}


@dataclass
class Theirs:
    """One attached document, read."""

    name: str
    paragraphs: list[str]  # markdown, one per paragraph
    plain: list[str]  # the same paragraphs as plain text
    kinds: list[str]


def _md(p: Any) -> str:
    """A paragraph as markdown: bold runs kept, a centered all-bold short line a title."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    runs = [r for r in p.runs if r.text]
    text = "".join(r.text for r in runs)
    if not text.strip():
        return ""
    bold = all(r.bold for r in runs if r.text.strip())
    if bold and p.alignment == WD_ALIGN_PARAGRAPH.CENTER and len(text.strip()) <= _HEADING_MAX:
        return "# " + " ".join(text.split())
    out = []
    for r in runs:
        t = r.text.replace("\n", " ")
        out.append(f"**{t.strip()}**" if r.bold and t.strip() else t)
    return " ".join("".join(out).split())


def read(name: str, blob: bytes) -> Theirs:
    import docx

    d = docx.Document(io.BytesIO(blob))
    paras: list[str] = []
    plain: list[str] = []
    for block in d.element.body.iterchildren():
        if block.tag.endswith("}p"):
            from docx.text.paragraph import Paragraph

            p = Paragraph(block, d)
            m = _md(p)
            if m:
                paras.append(m)
                plain.append(" ".join(p.text.split()))
        elif block.tag.endswith("}tbl"):
            from docx.table import Table

            for row in Table(block, d).rows:
                cells = []
                for c in row.cells:
                    t = " ".join(c.text.split())
                    if t and t not in cells:
                        cells.append(t)
                if cells:
                    line = " ".join(cells)
                    paras.append(line)
                    plain.append(line)
    text = "\n".join(plain)
    return Theirs(name, paras, plain, sets.kinds_in(text))


def _start(t: Theirs) -> int:
    """Where his set begins: the identification block, else the first request
    label, else the first heading after a title line, else the top."""
    for i, p in enumerate(t.plain):
        if _IDENT.search(p):
            return i
    for i, p in enumerate(t.plain):
        if any(pat.search(p) for pat in sets.LABELS.values()):
            j = i
            while j > 0 and t.paragraphs[j - 1].startswith("# "):
                j -= 1
            # the Definitions, if they come before the first request
            for k in range(i - 1, -1, -1):
                if t.plain[k].strip().strip(":").upper() == "DEFINITIONS":
                    j = min(j, k)
                    break
            return j
    return 0


def _title(t: Theirs, start: int) -> str | None:
    for p in t.plain[:start]:
        if _TITLE.search(p) and len(p) <= 300 and not _IDENT.search(p):
            return p
    return None


def _names(fields: dict[str, Any], side: str) -> str:
    vals = [x["value"] for x in fields.get(side) or []]
    return "; ".join(vals)


def _role(fields: dict[str, Any], side: str, word: str) -> str:
    return word + ("s" if len(fields.get(side) or []) > 1 else "")


def court_attorney_block(att: dict[str, Any]) -> list[str]:
    """The attorney block as the court's paper prints it, when the firm authored
    none: the name with its State Bar number on one line, then the firm, the
    address, telephone, facsimile and email."""
    name, sbn = att.get("name"), att.get("state_bar_number")
    first = f"{name} (SBN {sbn})" if name and sbn else (name or "")
    rest = [att.get(k) for k in ("firm", "street", "city_state_zip")]
    rest += [
        f"{label}: {att[k]}"
        for k, label in (("phone", "Telephone"), ("fax", "Facsimile"), ("email", "Email"))
        if att.get(k)
    ]
    return [x for x in [first, *rest] if x]


def caption_md(fields: dict[str, Any], block: list[str] | None, title: str) -> str:
    """The caption: attorney block, the court in two lines, the parties and the
    case number, the title. Missing court facts are markers, never guesses."""

    def nir(what: str) -> str:
        return f"{{{{NOT IN RECORD: {what}, not in the court's filings read for the caption}}}}"

    plaintiffs = _names(fields, "plaintiffs") or nir("plaintiff as captioned")
    defendants = _names(fields, "defendants") or nir("defendant as captioned")
    court = (fields.get("court_name") or {}).get("value") or nir("court")
    county = (fields.get("county") or {}).get("value")
    case = (fields.get("case_number") or {}).get("value") or nir("case number")
    lines = list(block or [])
    if not lines:
        att = fields.get("attorney") or {}
        lines = court_attorney_block(att) or [nir("attorney block")]
    paras = [*lines, f"Attorneys for Plaintiff {plaintiffs}", f"**{court.upper()}**"]
    if county and county.upper() not in court.upper():
        paras.append(f"**FOR THE COUNTY OF {county.upper()}**")
    table = "\n".join(
        [
            "| | |",
            "| --- | --- |",
            f"| {plaintiffs}, {_role(fields, 'plaintiffs', 'Plaintiff')}, v. {defendants}, "
            f"{_role(fields, 'defendants', 'Defendant')}. | Case No. {case} |",
            f"| | **{title}** |",
        ]
    )
    return "\n\n".join([*paras, table])


def signature_md(block: list[str] | None, plaintiffs: str) -> str:
    if not block:
        return "Dated: {{ATTORNEY: date}}\n\nBy: ______________________________\n\n{{ATTORNEY: signature block}}"
    name = re.split(r"\s*\(SBN", block[0])[0].strip()
    firm = block[1] if len(block) > 1 else ""
    return (
        "Dated: {{ATTORNEY: date}}\n\n"
        + (f"**{firm}**\n\n" if firm else "")
        + f"By: ______________________________\n\n{name}\n\nAttorneys for Plaintiff {plaintiffs}"
    )


@dataclass
class Placed:
    markdown: str
    body: list[str]  # his paragraphs, plain, as placed (the verbatim check's source)
    added: list[str]  # what the job added, for the notes


def place(t: Theirs, fields: dict[str, Any], block: list[str] | None, pos_md: str) -> Placed:
    start = _start(t)
    body_md, body_plain = t.paragraphs[start:], t.plain[start:]
    added = ["the caption from the court's filings"]
    title = _title(t, start)
    if title is None:
        kind = t.kinds[0] if t.kinds else None
        set_no = next((m.group(1).strip() for p in body_plain if (m := _SET_NO.match(p))), None)
        plaintiffs = _names(fields, "plaintiffs") or "PLAINTIFF"
        defendants = _names(fields, "defendants") or "DEFENDANT"
        title = f"PLAINTIFF {plaintiffs.upper()}'S {VARIANT_TITLES.get(kind or '', 'DISCOVERY')} TO DEFENDANT {defendants.upper()}"
        title += f", SET {set_no.upper()}" if set_no else ", SET {{ATTORNEY: set number}}"
        added.append("the set's title, from its identification block and the caption's parties")
    else:
        added.append("the set's title, as his document wrote it")
    cap = caption_md(fields, block, title)
    if block:
        added.append("the firm's attorney block")
    parts = [cap, *body_md]
    if not any(_SIGNATURE.search(p) for p in body_plain):
        parts.append(signature_md(block, _names(fields, "plaintiffs") or "{{NOT IN RECORD: plaintiff}}"))
        added.append("the signature lines")
    if not any(_POS.search(p) for p in body_plain):
        parts.append(pos_md)
        added.append("the firm's proof of service")
    return Placed("\n\n".join(parts) + "\n", body_plain, added)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[*_`#|]", " ", s).split()).casefold()


def missing(body: list[str], rendered: str) -> list[str]:
    """His paragraphs that do not appear, in order and word for word, in the
    rendered text: the verbatim rule. Empty is a pass."""
    hay, at, out = _norm(rendered), 0, []
    for p in body:
        n = _norm(p)
        if not n:
            continue
        i = hay.find(n, at)
        if i < 0:
            out.append(p[:120])
        else:
            at = i + len(n)
    return out
