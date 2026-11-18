"""Did a visit keep its sections? The within-page measure. $0, no model.
A RECORDING INSTRUMENT: it never stops, queues or edits anything.

WHY (2026-11-17). One visit of a real update lost ten clinical items on
pages the update read: the drafting step kept half the visit, and the
page-image review that looks for omitted content missed it too. A word share
would not have seen it (the half that was kept was more than 40 percent of
the page's words).

MEASURED (2026-11-17, three real job copies, $0): the first version of this
measure did NOT catch that visit. Two of its four pages were cited by no
entry, so they were never measured; one cited page had no section heading
the measure knows; the two measured pages read 88 and 81 percent present,
because an item counted as present when any entry citing the same document
carried it. Its three flags were all false (two standing boilerplate
sentences, one diagnosis table whose ICD codes broke across lines). So it
records and never acts, and closing that class of miss is not its job.

THE SIGNAL is dropped BLOCKS, not word share. A cited page is split at its
section headings (``HEADINGS``: the clinical-note headings of
``billing_page.NARRATIVE_HEADS`` and the further ones named here). A block's
ITEMS are what code can see without reading prose:

* its clinical values (``values.clinical_values``);
* its diagnosis and impression terms (``values.terms``; under a diagnoses,
  impression or assessment heading every content word is one);
* the dates on its lines;
* its ICD-10 codes, in their dotted form (``ICD``), a code a print layout
  broke across a line joined first (``join_codes``).

PRESENT, two columns. The judged column is the entries citing the SAME PAGE;
the second column is any entry citing the same DOCUMENT, kept for
comparison. For the ICD codes the compose pass's INDEX rows
(``collect.index_rows``) count too. A page that states no date is a
chart-level page (a problem list, a medication list): both columns read the
whole chronology, the delivered text included.

A block of at least ``DROP_MIN_ITEMS`` items of which fewer than
``DROP_PRESENT_FRAC`` are present on the same-page column is DROPPED; one
dropped block flags the page. Text before a page's first heading is no
block, so a cover, an index or a blank page can never flag.

UNCITED SIBLINGS. A page dropped whole from a visit whose other pages are
cited is never measured above, because nothing cites it. So the report also
counts, per document, the pages that were READ, that no entry cites, that
code does not class as blank, billing or non-record, and that lie inside or
next to the page range one entry cites in that document.

KNOWN LIMITS (stated in every report): a prose diagnosis, exam finding or
plan with no value, code or cue word is invisible; there is no sentence-level
boilerplate signal in code, so a standing sentence ("interpreted by me
unless otherwise stated") is measured as written and can flag.

    python -m jobs.medchron.update.page_content measure <copy root or job_dir> [--unit U] [--out report.json]
        [--show-values FILE] [--firm-config firm.yaml]

The path may be the copy root (holding ``data/<slug>/``) or the
``data/<slug>`` folder itself (holding ``delivered/``, ``text/`` and
``runs/<unit>/update/``). The report names the chronology file it compared
against and carries NO record text: per cited page its document (a sha256
prefix), page, blocks, items per block and the two present columns;
totals; and for a dropped block the KINDS of its items and how many of each
are absent. ``--show-values FILE`` writes the absent values to FILE, a local
file for the workspace reader only; nothing prints them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from . import body as B, select as S
from .billing_page import NARRATIVE_HEADS, is_billing
from .contained import dates_of
from .values import _STOP, clinical_values, numbers, terms, words

#: The thresholds, in one place. A block needs this many items to be judged
#: at all (a two-line block cannot be told from a heading's echo) ...
DROP_MIN_ITEMS = 4
#: ... and is DROPPED when fewer than this share of its items are present.
DROP_PRESENT_FRAC = 0.25
#: An uncited read page is a sibling of a cited range when it is inside it
#: or this many pages beyond either end.
SIBLING_REACH = 1
#: The document id a report names: a prefix of the file's sha256, never a name.
SHA_PREFIX = 12

#: The further headings a visit's sections open with (regex alternatives).
EXTRA_HEADS = (
    "diagnos(?:is|es)",
    "visit diagnos(?:is|es)",
    "discharge diagnos(?:is|es)",
    "final diagnos(?:is|es)",
    "assessment\\s*(?:and|&|/)\\s*plan",
    "plan of care",
    "exam by system",
    "physical examination",
    "neuro(?:logical)?(?:\\s+exam(?:ination)?)?",
    "medications?",
    "current medications?",
    "medication list",
    "imaging",
    "radiology",
    "impression",
    "findings",
    "vital signs",
    "vitals",
)
_SHORT = ("s", "o", "a", "p", "cc", "ros", "hpi")
_LONG = sorted({h for h in (*NARRATIVE_HEADS, *EXTRA_HEADS) if h not in _SHORT}, key=len, reverse=True)
#: A heading line: a long heading followed by a colon, a dash or the line's
#: end, or a short one (S:, CC:) followed by a colon or a dash. A leading
#: "1." or "2)" (a numbered section) is allowed.
HEADINGS = re.compile(
    r"(?im)^[ \t]*(?:\d{1,2}[.)][ \t]*)?"
    rf"(?P<h>(?:{'|'.join(_LONG)})(?=[ \t]*(?:[:\-]|$))|(?:{'|'.join(_SHORT)})(?=[ \t]*[:\-]))"
    r"[ \t]*[:\-]?"
)
#: An ICD-10 code in its dotted form. The undotted form ("T12", "C56") is a
#: vertebra or a room as often as a code, so it is not counted.
ICD = re.compile(r"\b([A-TV-Z]\d{2}\.[0-9A-Z]{1,4})\b")
#: A code a table's layout broke across a line: "M54." then "5", "M54" then
#: ".5", or "S13.4XX" then a line that is only its last characters.
_SPLIT_CODE = re.compile(
    r"\b([A-TV-Z]\d{2}\.)[ \t]*\n[ \t]*([0-9A-Z]{1,4})\b"
    r"|\b([A-TV-Z]\d{2})[ \t]*\n[ \t]*(\.[0-9A-Z]{1,4})\b"
    r"|\b([A-TV-Z]\d{2}\.[0-9A-Z]{1,3})[ \t]*\n[ \t]*([0-9A-Z]{1,3})[ \t]*$",
    re.M,
)
#: The item kinds a report names.
VALUE, TERM, DATE, CODE = "value", "term", "date", "icd"
LIMITS = (
    "A prose diagnosis, exam finding or plan with no value, no ICD code and no cue word is invisible to this "
    "test: it measures sections that left with their numbers, codes and named findings.",
    "No sentence-level boilerplate signal exists in code: a standing sentence (an interpretation or a "
    "learning-needs line) is measured as written and can flag.",
    "A page no entry cites is not measured; it is counted under uncited_siblings when it sits beside a cited "
    "range of the same document.",
)


def join_codes(text: str) -> str:
    """`text` with each ICD code a line break split rejoined (``_SPLIT_CODE``)."""
    return _SPLIT_CODE.sub(lambda m: "".join(g for g in m.groups() if g), text or "")


@dataclass(frozen=True)
class Item:
    kind: str
    value: str


@dataclass
class Block:
    heading: str  # the heading's canonical name, from the fixed vocabulary above
    items: frozenset[Item]


def _norm_heading(h: str) -> str:
    return " ".join(h.lower().split())


#: A heading whose every line is a finding: a diagnoses list names its
#: diagnoses with no cue word, so each content word of it is a term.
LISTS_FINDINGS = re.compile(r"diagnos|impression|assessment")


def items_of(text: str, heading: str = "") -> frozenset[Item]:
    """What code can see in a block: values, terms, dates, dotted ICD codes."""
    out = {Item(VALUE, v) for v in clinical_values(text)}
    found = terms(text) | (words(ICD.sub(" ", text)) - _STOP if LISTS_FINDINGS.search(heading) else set())
    out |= {Item(TERM, t) for t in found}
    out |= {Item(DATE, d) for d in dates_of(text)}
    out |= {Item(CODE, c.upper()) for c in ICD.findall(text)}
    return frozenset(out)


def blocks(text: str) -> list[Block]:
    """The page's sections, each from its heading to the next. Text before
    the first heading is no block: a page with no clinical heading has none."""
    text = join_codes(text)
    marks = list(HEADINGS.finditer(text))
    out: list[Block] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        h = _norm_heading(m.group("h"))
        out.append(Block(h, items_of(text[m.end() : end], h)))
    return out


def _parts(nums: Iterable[str]) -> set[str]:
    out = set()
    for n in nums:
        out.add(n)
        out |= set(re.split(r"[/:,]", n))
    return out


@dataclass(frozen=True)
class Hay:
    """The text a page's items should be found in, read once."""

    values: frozenset[str]
    nums: frozenset[str]
    words: frozenset[str]
    dates: frozenset[str]
    codes: frozenset[str]

    @classmethod
    def of(cls, text: str, codes_text: str = "") -> Hay:
        text = join_codes(text)
        return cls(
            frozenset(clinical_values(text)),
            frozenset(_parts(numbers(text))),
            frozenset(words(text)),
            dates_of(text),
            frozenset(c.upper() for c in ICD.findall(f"{text}\n{join_codes(codes_text)}")),
        )

    def has(self, it: Item) -> bool:
        if it.kind == VALUE:
            # A value restated in another spelling ("BP 132/96" for "Blood
            # pressure 132/96 mmHg") is present when its number is.
            return it.value in self.values or it.value.split(" ")[-1 if it.value.startswith("bp ") else 0] in self.nums
        if it.kind == TERM:
            return it.value in self.words
        if it.kind == DATE:
            return it.value in self.dates
        return it.value in self.codes


@dataclass
class BlockResult:
    heading: str
    items: int
    present: int  # on the judged column: the entries citing the same page
    present_doc: int  # on the second column: any entry citing the same document
    kinds: dict[str, int]
    absent_kinds: dict[str, int]
    absent: list[Item] = field(default_factory=list)

    @property
    def dropped(self) -> bool:
        return self.items >= DROP_MIN_ITEMS and self.present < DROP_PRESENT_FRAC * self.items


def measure_text(text: str, hay: Hay, hay_doc: Hay | None = None) -> list[BlockResult]:
    """Every block of `text`, measured against `hay` (and `hay_doc`, the second column)."""
    out = []
    for b in blocks(text):
        absent = sorted((i for i in b.items if not hay.has(i)), key=lambda i: (i.kind, i.value))
        doc_has = sum(1 for i in b.items if (hay_doc or hay).has(i))
        out.append(
            BlockResult(
                b.heading,
                len(b.items),
                len(b.items) - len(absent),
                doc_has,
                dict(Counter(i.kind for i in b.items)),
                dict(Counter(i.kind for i in absent)),
                absent,
            )
        )
    return out


@dataclass
class PageResult:
    exhibit: int
    page: int
    doc: str  # a sha256 prefix
    dated: bool
    blocks: list[BlockResult]

    @property
    def flagged(self) -> bool:
        return any(b.dropped for b in self.blocks)

    def dropped(self) -> list[BlockResult]:
        return [b for b in self.blocks if b.dropped]


@dataclass
class Sibling:
    exhibit: int
    page: int
    doc: str
    blocks: int
    items: int


@dataclass
class Measured:
    pages: list[PageResult]
    siblings: list[Sibling] = field(default_factory=list)


def cites(entry: str) -> set[tuple[int, int]]:
    """(exhibit, page) of every citation in an entry."""
    return {(int(m.group(1)), p) for m in B.CITE.finditer(entry) for p in CL.parse_pages(m.group(2))}


def _joined(es: Iterable[str]) -> str:
    return "\n\n".join(dict.fromkeys(es))


def _code_class(text: str, nonrecord: Callable[[str], str | None] | None) -> str | None:
    """Why code sets a page aside without reading it: blank, billing or non-record."""
    t = (text or "").strip()
    if len(t) < S.MIN_CHARS or t in S.FAILED_MARKS:
        return "blank"
    if is_billing(t):
        return "billing"
    return "nonrecord" if nonrecord is not None and nonrecord(t) else None


def siblings(
    entries: list[str],
    read: Mapping[int, Iterable[int]],
    page_text: Callable[[int, int], str | None],
    docs: dict[int, str],
    nonrecord: Callable[[str], str | None] | None = None,
) -> list[Sibling]:
    """Read, uncited pages inside or beside a page range one entry cites in
    the same document, that code does not class blank, billing or non-record."""
    cited = {c for e in entries for c in cites(e)}
    near: set[tuple[int, int]] = set()
    for e in entries:
        by_ex: dict[int, list[int]] = {}
        for ex, p in cites(e):
            by_ex.setdefault(ex, []).append(p)
        for ex, ps in by_ex.items():
            near |= {(ex, q) for q in range(min(ps) - SIBLING_REACH, max(ps) + SIBLING_REACH + 1)}
    out = []
    for ex, pages in sorted(read.items()):
        for p in sorted(set(pages)):
            if (ex, p) in cited or (ex, p) not in near:
                continue
            text = page_text(ex, p) or ""
            if _code_class(text, nonrecord) is not None:
                continue
            bs = blocks(text)
            out.append(Sibling(ex, p, str(docs.get(ex) or "")[:SHA_PREFIX], len(bs), sum(len(b.items) for b in bs)))
    return out


def measure(
    entries: list[str],
    page_text: Callable[[int, int], str | None],
    docs: dict[int, str],
    context: str = "",
    index_rows: Iterable[str] = (),
    read: Mapping[int, Iterable[int]] | None = None,
    nonrecord: Callable[[str], str | None] | None = None,
) -> Measured:
    """Every page an added entry cites, of an exhibit in `docs` ({exhibit:
    sha256}), measured, and the uncited siblings of the pages in `read`
    ({exhibit: the pages the update read}). `context`: the rest of the
    chronology (the delivered text) a chart-level page's items may sit in;
    `index_rows`: the compose pass's INDEX rows, where an ICD code it read is
    recorded; `nonrecord`: the firm's non-record page classifier."""
    codes = "\n".join(index_rows)
    by_ex: dict[int, list[str]] = {}
    by_page: dict[tuple[int, int], list[str]] = {}
    for e in entries:
        for ex, p in cites(e):
            if ex in docs:
                by_page.setdefault((ex, p), []).append(e)
                by_ex.setdefault(ex, []).append(e)
    doc_hays = {ex: Hay.of(_joined(es), codes) for ex, es in by_ex.items()}
    wide = Hay.of(context + "\n\n" + "\n\n".join(entries), codes)
    out = []
    for (ex, p), es in sorted(by_page.items()):
        text = page_text(ex, p) or ""
        dated = bool(dates_of(S.without_furniture(text)))
        hay, hay_doc = (Hay.of(_joined(es), codes), doc_hays[ex]) if dated else (wide, wide)
        out.append(PageResult(ex, p, str(docs.get(ex) or "")[:SHA_PREFIX], dated, measure_text(text, hay, hay_doc)))
    sib = siblings(entries, read or {}, page_text, docs, nonrecord) if read else []
    return Measured(out, sib)


def totals(m: Measured) -> dict[str, int]:
    """The counts ``page_account.json`` keeps under ``within_page``."""
    r = m.pages
    return {
        "cited_pages": len(r),
        "pages_with_blocks": sum(1 for x in r if x.blocks),
        "blocks": sum(len(x.blocks) for x in r),
        "dropped_blocks": sum(len(x.dropped()) for x in r),
        "flagged_pages": sum(1 for x in r if x.flagged),
        "uncited_siblings": len(m.siblings),
        "uncited_siblings_with_blocks": sum(1 for s in m.siblings if s.blocks),
    }


def report(m: Measured, compared: str = "") -> dict[str, Any]:
    """The report: counts and kinds only, never a value."""
    pages = [
        {
            "doc": r.doc,
            "exhibit": r.exhibit,
            "page": r.page,
            "blocks": len(r.blocks),
            "items": [b.items for b in r.blocks],
            "present_same_page": [b.present for b in r.blocks],
            "present_same_document": [b.present_doc for b in r.blocks],
            "flagged": r.flagged,
        }
        for r in m.pages
    ]
    flagged = [
        {
            "doc": r.doc,
            "exhibit": r.exhibit,
            "page": r.page,
            "block": r.blocks.index(b),
            "heading": b.heading,
            "kinds": b.kinds,
            "absent_kinds": b.absent_kinds,
        }
        for r in m.pages
        for b in r.dropped()
    ]
    sib = [
        {"doc": s.doc, "exhibit": s.exhibit, "page": s.page, "blocks": s.blocks, "items": s.items} for s in m.siblings
    ]
    return {
        "compared_against": compared,
        "limits": list(LIMITS),
        "thresholds": {
            "drop_min_items": DROP_MIN_ITEMS,
            "drop_present_frac": DROP_PRESENT_FRAC,
            "judged_column": "present_same_page",
            "sibling_reach": SIBLING_REACH,
        },
        "totals": totals(m),
        "pages": pages,
        "flagged": flagged,
        "uncited_siblings": sib,
    }


def values_of(m: Measured) -> list[dict[str, Any]]:
    """The absent values of every dropped block: for the workspace reader's local file only."""
    return [
        {"exhibit": r.exhibit, "page": r.page, "heading": b.heading, "absent": [[i.kind, i.value] for i in b.absent]}
        for r in m.pages
        for b in r.dropped()
    ]


# ---- reading a job directory ------------------------------------------------


def _read(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def slug_dir(path: Path) -> Path:
    """The job folder holding ``runs/<unit>/update/``: `path` itself, or the
    one ``data/<slug>`` folder under a copy root."""
    if any((path / "runs").glob("*/update")):
        return path
    found = sorted({p.parents[2] for p in path.glob("data/*/runs/*/update")})
    if len(found) != 1:
        raise SystemExit(f"no single data/<slug>/runs/<unit>/update under {path} (found {[str(f) for f in found]})")
    return found[0]


def _unit_dir(job: Path, unit: str | None) -> Path:
    if unit:
        return job / "runs" / unit
    runs = sorted(p for p in (job / "runs").glob("*") if (p / "update").is_dir())
    if len(runs) != 1:
        raise SystemExit(f"name the unit with --unit (found {[p.name for p in runs]})")
    return runs[0]


@dataclass
class JobView:
    """What the measure reads off one update job directory."""

    entries: list[str]
    context: str
    docs: dict[int, str]
    texts: dict[int, dict[int, str]]
    index_rows: list[str]
    read: dict[int, list[int]]
    compared: str

    def page_text(self, ex: int, page: int) -> str | None:
        return self.texts.get(ex, {}).get(page)


def _body(md: str) -> list[str]:
    try:
        return B.entries_of(md)[1]
    except ValueError:
        return B.split_body(md)[1]


def job_view(job: Path, rd: Path) -> JobView:
    """The added entries (the audited document ``final-chronology.md``, or
    ``update/new_entries.md``), the new exhibits ({exhibit: sha256}, each
    one's page texts and the pages the update read), the delivered text,
    and the compose pass's INDEX rows."""
    from .collect import index_rows

    ud = rd / "update"
    doc = rd / "final-chronology.md"
    if doc.is_file():
        entries, compared = _body(doc.read_text(encoding="utf-8")), doc
    else:
        compared = ud / "new_entries.md"
        entries = B.split_body("\n\n" + compared.read_text(encoding="utf-8"))[1] if compared.is_file() else []
    rows = _read(ud / "new_exhibits.final.json", None)
    if not isinstance(rows, list):
        rows = _read(ud / "new_exhibits.json", [])
    docs: dict[int, str] = {}
    texts: dict[int, dict[int, str]] = {}
    read: dict[int, list[int]] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("exhibit"), int) or not r.get("id"):
            continue
        docs[r["exhibit"]] = str(r.get("sha256") or "")
        read[r["exhibit"]] = [p for p in r.get("read") or [] if isinstance(p, int)]
        tp = job / "text" / f"{r['id']}.txt"
        texts[r["exhibit"]] = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
    base = ud / "base.md"
    context = base.read_text(encoding="utf-8") if base.is_file() else ""
    rel = str(compared.relative_to(job)) if compared.is_relative_to(job) else str(compared)
    return JobView(entries, context, docs, texts, index_rows(rd) if rd.is_dir() else [], read, rel)


def _nonrecord(config: Path | None) -> Callable[[str], str | None] | None:
    if config is None:
        return None
    from ..config import load
    from ...stages.classify import classify_text, page_classes

    classes = page_classes(load(str(config)))
    return lambda t: classify_text(t, classes)


def measure_job(
    job: Path, unit: str | None = None, nonrecord: Callable[[str], str | None] | None = None
) -> tuple[Measured, str]:
    v = job_view(job, _unit_dir(job, unit))
    return measure(v.entries, v.page_text, v.docs, v.context, v.index_rows, v.read, nonrecord), v.compared


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="measure every cited page of one update job copy")
    m.add_argument("job_dir", type=Path, help="the copy root, or its data/<slug> folder")
    m.add_argument("--unit")
    m.add_argument("--out", type=Path, help="write the report here as well as to stdout")
    m.add_argument("--show-values", type=Path, help="write the absent VALUES to this local file (never to stdout)")
    m.add_argument("--firm-config", type=Path, help="the firm config, for its non-record page classes")
    a = ap.parse_args(argv)
    measured, compared = measure_job(slug_dir(a.job_dir), a.unit, _nonrecord(a.firm_config))
    rep = report(measured, compared)
    text = json.dumps(rep, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    if a.show_values:
        a.show_values.write_text(json.dumps(values_of(measured), indent=1) + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
