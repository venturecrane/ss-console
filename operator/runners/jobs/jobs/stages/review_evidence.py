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
  shared form held the job. The cited page decides check 1: since
  2026-11-22 by a model looking at its picture (``stages/whose_page``; the
  text rules that decided it before caught none of nine delivered
  other-person entries). The client's page, or a page naming no one, makes
  the finding a NOTE; a page whose patient is another person has the entry
  LEFT OUT BY RULE (``leave_out``) and disclosed without a word about that
  person, as the identity rule merged 2026-10-24 does for a whole record.

Nothing here writes client text: it reads pages and deletes one entry block.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..medchron.audit import claims as CL
from ..medchron.audit.page_text import PageIndex, exhibit_paths
from ..medchron.gates import foreign_identity as FI
from ..proof import review as proof_review
from .base import StageRun
from ..text_rules import EXHIBIT_PAGE_MENTION as _EXHIBIT_PAGE, US_DATE as _DATE

#: Disclosures of entries left out by rule, beside the package (internal,
#: never uploaded): {"unit", "entries": [{"date", "provider", "exhibit",
#: "pages", "reason"}]}. The Review Notes file carries the same sentence.
LEFT_OUT = "review-left-out.json"
LEFT_OUT_REASON = FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]
_DATED = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*$")


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
                    from ..matter import pdf as matter_pdf

                    got = matter_pdf.page_text(pdf, page)
                    if got is not None:
                        parts.append(got)
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


def _named_pages(text: str) -> list[tuple[int, int]]:
    """Every (exhibit, page) the finding's text names with a page number."""
    return [(int(m.group(1)), int(m.group(2))) for m in _EXHIBIT_PAGE.finditer(text) if m.group(2)]


def _cites(block: str, exhibit: int, page: int) -> bool:
    return any(
        int(c.group(1)) == exhibit and page in (CL.parse_pages(c.group(2)) or [1]) for c in CL.CITE.finditer(block)
    )


def entry_for(md: str, text: str) -> tuple[int, int, str] | None:
    """The one entry a finding's text names by its date, then its provider
    when the date has several entries, then the exhibit page the finding
    cites when the same provider has several entries that day (an ED visit
    and its admission); None when it names no single one."""
    low = text.lower()
    blocks = entry_blocks(md)
    named = _named_pages(text)
    for d in dict.fromkeys(_DATE.findall(text)):
        same = [b for b in blocks if b[2].startswith(d)]
        if len(same) > 1:
            same = [b for b in same if _provider(b[2]) and _provider(b[2]) in low]
        if len(same) > 1 and named:
            same = [b for b in same if any(_cites(b[2], ex, pg) for ex, pg in named)]
        if len(same) == 1:
            return same[0]
    return None


def cited_pages(block: str, text: str) -> list[tuple[int, int]]:
    """The page the finding names, else every page the entry cites. A
    finding that mentions an exhibit in passing and then cites a page
    ("the Exhibit 3 copy omits it ... (Exhibit 1 - p. 8)") is decided on
    the page it cites, never on the passing mention."""
    m = next((x for x in _EXHIBIT_PAGE.finditer(text) if x.group(2)), None) or _EXHIBIT_PAGE.search(text)
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


def leave_out(
    sr: StageRun,
    doc: Path,
    block: tuple[int, int, str],
    pages: list[tuple[int, int]],
    reason: str = LEFT_OUT_REASON,
    row: dict[str, Any] | None = None,
) -> None:
    """The entry block (its first line, end line and text: exactly the lines
    removed) out of the chronology (and out of ``entries_scoped_final.md``
    when it is there verbatim, so a rebuild does not bring it back), and its
    disclosure recorded in ``LEFT_OUT``: the date and provider lines, or
    `row` (a prior-history paragraph has neither)."""
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
            **(row or {}),
            "reason": reason,
        }
    )
    out.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")


# ---- check 1 and the entry reader, decided by page ----------------------------------
#: The text of every finding this settles, written by code and never the
#: model's: the model's own wording named the other person (2026-11-23).
CHECK1 = "1 another person's records"
KEPT_NOTE = "checked against the cited page: kept"
NOT_SHOWN_NOTE = "the cited page could not be shown, so it was kept"
#: A ``LEFT_OUT`` row for a prior-history paragraph (``limitations_rules``).
PRIOR = "Prior Medical History"


def left_out_note(date_line: str) -> str:
    return f"left out before delivery by rule: the entry dated {date_line} {LEFT_OUT_REASON}"


def prior_note(pages: list[tuple[int, int]]) -> str:
    return f"a prior-history paragraph citing {_cite(pages)} {LEFT_OUT_REASON}"


def _cite(pages: list[tuple[int, int]]) -> str:
    return "; ".join(f"Exhibit {ex} p. {pg}" for ex, pg in pages)


def _date(block: str) -> str:
    return block.split("\n", 1)[0].strip()


def _all_cites(text: str) -> set[tuple[int, int]]:
    return {(int(c.group(1)), p) for c in CL.CITE.finditer(text) for p in CL.parse_pages(c.group(2)) or [1]}


def entries(md: str) -> tuple[tuple[int, int, str] | None, list[tuple[int, int, str]]]:
    """(the preamble, every dated entry) of the chronology body as (first
    line, end line, text), bounded exactly as the entry reader's list bounds
    them (``body.entries_of``, whose order numbers its ``N<k>``): a date line
    with a note after it ("04/02/2026 (records date, ...)", "07/18 (year not
    stated)") starts its own entry. (None, []) when there is no body."""
    from ..medchron.update import body as B

    head = re.search(r"(?m)^## Medical Chronology\s*$", md)
    if head is None:
        return None, []
    start = head.end()
    ends = [m.start() for h in B.SECTIONS[3:] for m in [re.search(rf"(?m)^{re.escape(h)}\s*$", md[start:])] if m]
    end = start + min(ends) if ends else len(md)
    cuts = [m.start() for m in B.ENTRY.finditer(md, start, end)] + [end]
    lines = md.splitlines(keepends=True)

    def span(a: int, b: int) -> tuple[int, int, str]:
        la = md[:a].count("\n") + (0 if a == 0 or md[a - 1] == "\n" else 1)
        lb = len(lines) if b >= len(md) else md[:b].count("\n")
        return la, lb, "".join(lines[la:lb])

    return span(start, cuts[0]), [span(a, b) for a, b in zip(cuts, cuts[1:])]


def finding_pages(md: str, text: str) -> list[tuple[int, int]]:
    """The pages a check 1 finding is about: those it names, and, when no
    entry cites any of them, also those its entry (``entry_for``) cites;
    with no page named, those its entry cites."""
    named = list(dict.fromkeys(_named_pages(text)))
    cited = set().union(*(_all_cites(e[2]) for e in entries(md)[1]))
    if named and set(named) & cited:
        return named
    block = entry_for(md, text)
    return list(dict.fromkeys([*named, *(sorted(_all_cites(block[2])) if block is not None else [])]))


def _citing(md: str, other: set[tuple[int, int]]) -> tuple[tuple[int, int, str], list[tuple[int, int]]] | None:
    """The first entry citing a page in `other`, and which of them it cites."""
    for e in entries(md)[1]:
        hit = sorted(other & _all_cites(e[2]))
        if hit:
            return e, hit
    return None


def _prior_out(sr: StageRun, doc: Path, other: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Each paragraph of the prior-history block (blank-line separated) that
    cites a page in `other`, out of the chronology and disclosed; never the
    block's other paragraphs. Returns the pages of each paragraph removed."""
    from ..medchron.update import body as B

    out: list[list[tuple[int, int]]] = []
    while True:
        md = doc.read_text(encoding="utf-8")
        pre = entries(md)[0]
        if pre is None:
            return out
        lines = md.splitlines(keepends=True)
        para: list[int] = []
        for i in range(pre[0], pre[1] + 1):
            if i < pre[1] and lines[i].strip():
                if para or not B.PMH_HEAD.match(lines[i]):  # the block's heading is never part of a paragraph
                    para.append(i)
                continue
            hit = sorted(other & _all_cites("".join(lines[j] for j in para)))
            if hit:
                row = {"date": PRIOR, "provider": "", "exhibit": hit[0][0], "pages": [p for _x, p in hit]}
                leave_out(sr, doc, (para[0], para[-1] + 1, "".join(lines[j] for j in para)), hit, row=row)
                out.append(hit)
                break
            para = []
        else:
            return out


def settle_identity(
    sr: StageRun, doc: Path, out: Any, subject: Any, stats: dict[str, int], check1: list[int], reader: set
) -> None:
    """One decision by page for both sources (2026-11-23). The pages the
    entry reader recorded as another person's (`reader`) and the pages each
    model check 1 MUST-FIX (`check1`, indices into ``out.findings``) is about
    are all looked at first (`subject`: (exhibit, page) -> subject; a look
    that fails raises ``whose_page.LookFailed`` before anything is edited).
    Then EVERY entry citing a page judged "other" is left out, each once, as
    the entry reader's list bounds it, and so is each prior-history paragraph
    citing one; each is disclosed. A check 1 whose pages or entry were left
    out becomes the neutral left-out NOTE; one whose pages were looked at and
    not judged so, the neutral kept NOTE; one whose pages could not be shown,
    the not-shown NOTE; one with no page found stays a MUST-FIX."""
    from . import whose_page

    md = doc.read_text(encoding="utf-8")
    about = {k: finding_pages(md, f"{out.findings[k].finding} {out.findings[k].cite}") for k in check1}
    seen = {p: subject(*p) for p in dict.fromkeys([*sorted(reader), *(p for ps in about.values() for p in ps)])}
    for v in seen.values():
        if v != "other":
            stats[f"identity_page_{v}"] += 1
    other = {p for p, v in seen.items() if v == "other"}
    gone: list[tuple[str, list[tuple[int, int]]]] = []  # each entry left out, and its pages judged other
    while (hit := _citing(doc.read_text(encoding="utf-8"), other)) is not None:
        leave_out(sr, doc, hit[0], hit[1], LEFT_OUT_REASON)
        gone.append((hit[0][2], hit[1]))
        out.changed, out.left_out = True, out.left_out + 1
        stats["identity_left_out"] += 1
        sr.log(f"review: the {_date(hit[0][2])} entry left out by rule (it cites a page judged another person's)")
    prior = _prior_out(sr, doc, other)
    if prior:
        out.changed = True
        stats["identity_prior_left_out"] += len(prior)
        sr.log(f"review: {len(prior)} prior-history paragraph(s) left out by rule")
    noted: set[str] = set()
    for k in check1:
        f, ps = out.findings[k], about[k]
        mine = [t for t, _hp in gone if _all_cites(t) & set(ps)]
        if mine:
            noted.update(mine)
            text = left_out_note(_date(mine[0]))
        elif any(p in other for p in ps):
            text = prior_note(sorted(p for p in ps if p in other))
        elif any(seen.get(p) not in (None, whose_page.NOT_SHOWN) for p in ps):
            text = KEPT_NOTE
        elif ps:
            text = NOT_SHOWN_NOTE
        else:
            continue
        out.findings[k] = proof_review.Finding(proof_review.NOTE, CHECK1, text, _cite(ps), f.by)
    for t, hp in gone:
        if t not in noted:
            out.findings.append(proof_review.code(proof_review.NOTE, CHECK1, left_out_note(_date(t)), _cite(hp)))
    for hp in prior:
        out.findings.append(proof_review.code(proof_review.NOTE, CHECK1, prior_note(hp), _cite(hp)))
