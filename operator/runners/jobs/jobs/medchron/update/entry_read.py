"""The full chronology's entry-level read: one unit per dated entry, read
against the pages it cites (and one page either side), and the reply parsed
into findings a script can act on. $0 here; the paid call is the stage's.

WHY (2026-11-18). The per-paragraph "is it supported" audit passed every
false statement on a real run: a claim was the paragraph a citation closes,
so an entry's date and provider lines were never read; omissions were not
defects; and the checker was handed the claim's own values. This reads the
ENTRY, header lines included, as the update lane's review does
(``review_repair``, ``review_parts``, ``review_pages``), and nothing the
reply says is applied here: ``entry_confirm`` verifies and decides that.

* A unit carries the entry's full text, its anchor (``review_repair``'s
  ``N<k>`` scheme; ``PRE`` for the prior-history block, read but
  ``record_only``), the pages it cites with one page either side inside the
  same exhibit, and is split so no unit shows more than MAX_SHOWN pages.
* The entry is shown as numbered lines (``entry_lines``): ``[D]``, ``[P]``,
  ``[S1]``... The reply gives EVERY label a verdict (``ok``, ``problem`` or
  ``unreadable``), or it is unparsed (retried, then split, then held:
  round 3, 2026-11-18).
* A finding names a class from CLASSES, a page the read was SHOWN, a line
  label of the unit and a quote found verbatim in THAT line; any other
  finding, and one on an ``unreadable`` line or page, is dropped and
  counted. At most MAX_FINDINGS per unit are kept, MAX_OMITTED of them
  ``omitted``; the rest are counted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from ..audit import claims as CL
from ..prompts import load
from . import body as B, entry_lines as EL, review_pages as RV, review_parts as RP, review_repair as RR
from .entry_lines import count_sentences as count_sentences  # the tripwire's count, the labels' splitter

PROMPT = "entry-read-system"
CLASSES = ("value", "wrong_field", "unsupported", "pointer", "omitted", "date", "provider", "another_person")
#: The pages one request shows at most (the update finisher's figure,
#: ``review_parts.FINISH_MAX_IMAGES``); an entry showing more is split. Before
#: 2026-11-18 (fix round) a split came at 40 CITED pages, which let one
#: request show up to 120.
MAX_SHOWN = RP.FINISH_MAX_IMAGES
MAX_FINDINGS = 12
MAX_OMITTED = 4
VERDICTS = ("ok", "problem", "unreadable")
#: Applied edits per sentence of the document above which the run is held.
EDIT_SHARE = 0.08
PRE = "PRE"
Images = RV.Images


@dataclass
class Unit:
    anchor: str  # N<k>, or PRE for the prior-history block
    text: str  # the entry's full text, its date and provider lines included
    pages: list[tuple[int, int]]  # every page the read is shown
    entry: RR.Entry | None = None
    record_only: bool = False
    part: int = 1
    parts: int = 1
    cited: list[tuple[int, int]] = field(default_factory=list)  # the cited pages this unit shows (never sent)
    lines: dict[str, str] = field(default_factory=dict)  # label -> the line's exact text (``entry_lines``)
    sub: bool = False  # a page-by-page read of one cited page (``stages/entry_audit``)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.lines)

    @property
    def key(self) -> str:
        return self.anchor if self.parts == 1 else f"{self.anchor}-{self.part}"

    def cites(self) -> set[tuple[int, int]]:
        """The pages the entry itself cites (all of them, not only this split's)."""
        return self.entry.cites() if self.entry is not None else _cites_of(self.text)


@dataclass
class ReadFinding:
    klass: str
    unit: str  # the unit's key
    anchor: str  # the entry anchor
    exhibit: int
    page: int
    quote: str
    action: str
    replacement: str
    problem: str = ""
    record_only: bool = False
    line: str = ""  # the label of the line it is about


@dataclass
class Parsed:
    findings: list[ReadFinding]
    dropped: Counter = field(default_factory=Counter)
    over_cap: int = 0
    lines: dict[str, str] = field(default_factory=dict)  # label -> ok | problem | unreadable
    omitted_over_cap: int = 0
    problem_without_finding: int = 0


def _cites_of(text: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), p) for m in CL.CITE.finditer(text) for p in CL.parse_pages(m.group(2)) or [1]}


def _shown(cited: list[tuple[int, int]], npages: Callable[[int], int]) -> list[tuple[int, int]]:
    """The cited pages with one page either side, inside each exhibit's length."""
    out: set[tuple[int, int]] = set()
    for ex, p in cited:
        n = max(npages(ex), 1)
        out |= {(ex, q) for q in (p - 1, p, p + 1) if 1 <= q <= n}
    return sorted(out)


def _chunks(ps: list[tuple[int, int]], npages: Callable[[int], int]) -> list[list[tuple[int, int]]]:
    """The cited pages in order, cut so no chunk shows more than MAX_SHOWN pages."""
    out: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    for p in ps:
        if cur and len(_shown(cur + [p], npages)) > MAX_SHOWN:
            out.append(cur)
            cur = []
        cur.append(p)
    return out + [cur] if cur else out or [[]]


def _units(anchor: str, text: str, cited: set[tuple[int, int]], npages: Callable[[int], int], **kw: Any) -> list[Unit]:
    chunks = _chunks(sorted(cited), npages)
    lines = EL.numbered(text, bool(kw.get("record_only")))[1]
    return [
        Unit(anchor, text, _shown(c, npages), part=i, parts=len(chunks), cited=c, lines=lines, **kw)
        for i, c in enumerate(chunks, 1)
    ]


def plan(md: str, keep: set[int], headings: set[str], npages: Callable[[int], int]) -> list[Unit]:
    """One unit per dated entry of the built document (split so no unit
    shows more than MAX_SHOWN pages), and one record-only unit for a cited
    prior-history block."""
    pre, _ = B.entries_of(md)
    out: list[Unit] = []
    pre_cites = {(x, p) for x, p in _cites_of(pre) if x in keep}
    if pre_cites:
        out += _units(PRE, pre.strip("\n"), pre_cites, npages, record_only=True)
    for e in RR.anchor_entries(md, keep, headings):
        out += _units(e.anchor, e.text, e.cites(), npages, entry=e)
    return out


def _page_list(pages: list[tuple[int, int]]) -> str:
    by: dict[int, list[int]] = {}
    for ex, p in pages:
        by.setdefault(ex, []).append(p)
    return "; ".join(f"Exhibit {ex} pp. {', '.join(map(str, ps))}" for ex, ps in sorted(by.items())) or "none"


def request(
    unit: Unit, cfg: Any, client: str, images: Images, modes: RV.Modes | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, user content) for one unit's read."""
    split = f" (part {unit.part} of {unit.parts}: only the pages below are shown)" if unit.parts > 1 else ""
    split = split or (" (only the pages below are shown)" if unit.sub else "")
    kind = "PRIOR HISTORY BLOCK" if unit.record_only else "ENTRY"
    out: list[dict[str, Any]] = [{"type": "text", "text": client}]
    shown = EL.numbered(unit.text, unit.record_only)[0]
    out.append({"type": "text", "text": f"{kind} {unit.anchor}{split}:\n{shown}"})
    out.append({"type": "text", "text": f"PAGES SHOWN: {_page_list(unit.pages)}"})
    cited = unit.cites()
    for ex, pg in unit.pages:
        label = " (cited by this entry)" if (ex, pg) in cited else " (a page next to a cited page)"
        out += RV.page_blocks(ex, pg, label, images, modes)
    return load(PROMPT, cfg), out


_int = RR._int  # the review's own field reader (a bool or a non-digit is None)


def _reply(reply: str, unit: Unit, lenient: bool) -> dict[str, Any]:
    """The reply object. Raises ``review_repair.ReplyUnparsed`` as the update
    review's parse does (not JSON, no ``complete``), and when ``lines`` does
    not give every label of the unit one of VERDICTS: the stage retries or
    splits the unit on it, and a line no verdict covers is never passed."""
    RR.parse_reply(reply, lenient=lenient)
    obj = json.loads(reply[reply.find("{") : reply.rfind("}") + 1])
    lines = obj.get("lines")
    if not isinstance(lines, dict):
        raise RR.ReplyUnparsed("the reply gives no verdict per line")
    lines = obj["lines"] = {str(k).strip().strip("[]").strip(): v for k, v in lines.items()}  # "[S1]" is "S1"
    missing = [lb for lb in unit.labels if lb not in lines]
    if missing:
        raise RR.ReplyUnparsed(f"the reply gives no verdict for line(s) {', '.join(missing[:6])}")
    if any(v not in VERDICTS for v in lines.values()):
        raise RR.ReplyUnparsed("a line's verdict is not ok, problem or unreadable")
    return obj


def _one(item: object, unit: Unit, verdicts: dict[str, str], dark: set[tuple[int, int]]) -> ReadFinding | str:
    """One item as a finding, or the reason it is dropped."""
    if not isinstance(item, dict):
        return "not_object"
    klass = str(item.get("class") or "").strip().lower()
    if klass not in CLASSES:
        return "class"
    ex, pg = _int(item.get("exhibit")), _int(item.get("page"))
    if ex is None and len({x for x, _ in unit.pages}) == 1 and unit.pages:
        ex = unit.pages[0][0]
    if ex is None or pg is None or (ex, pg) not in unit.pages:
        return "page"
    line = str(item.get("line") or "").strip().strip("[]")
    if line not in unit.lines:
        return "line"
    if verdicts.get(line) == "unreadable" or (ex, pg) in dark:
        return "unreadable"
    raw_quote, raw_new = item.get("quote"), item.get("replacement")
    quote: str = raw_quote if isinstance(raw_quote, str) else ""
    if not quote.strip() or quote not in unit.lines[line]:
        return "quote"
    action = str(item.get("action") or "").strip().lower()
    return ReadFinding(
        klass,
        unit.key,
        unit.anchor,
        ex,
        pg,
        quote,
        action if action in RR.ACTIONS else "none",
        raw_new if isinstance(raw_new, str) else "",
        str(item.get("problem") or "")[:400],
        unit.record_only,
        line,
    )


def _dark(obj: dict[str, Any]) -> set[tuple[int, int]]:
    """The pages the reply says it could not read."""
    out = set()
    for d in obj.get("unreadable_pages") or []:  # the prompt names no shape: each one a reader might write
        if isinstance(d, dict):
            ex, pg = _int(d.get("exhibit")), _int(d.get("page"))
        elif isinstance(d, list) and len(d) == 2:
            ex, pg = _int(d[0]), _int(d[1])
        else:
            m = _PAGE_NAME.search(str(d))
            ex, pg = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        if ex is not None and pg is not None:
            out.add((ex, pg))
    return out


_PAGE_NAME = re.compile(r"Exhibit\s*(\d+)\D+?(\d+)", re.I)


def parse(reply: str, unit: Unit, *, lenient: bool = False) -> Parsed:
    """The read's verdicts and findings, each finding checked against the
    unit. Raises ``review_repair.ReplyUnparsed`` when the reply is not one
    complete JSON object or skips a line (``_reply``)."""
    obj = _reply(reply, unit, lenient)
    verdicts = {lb: str(obj["lines"][lb]) for lb in unit.labels}
    out = Parsed([], lines=verdicts)
    dark = _dark(obj)
    for item in list(obj.get("findings") or []):
        got = _one(item, unit, verdicts, dark)
        if isinstance(got, str):
            out.dropped[got] += 1
        elif got.klass == "omitted" and sum(f.klass == "omitted" for f in out.findings) >= MAX_OMITTED:
            out.omitted_over_cap += 1
        elif len(out.findings) >= MAX_FINDINGS:
            out.over_cap += 1
        else:
            out.findings.append(got)
    on = {f.line for f in out.findings}
    out.problem_without_finding = sum(1 for lb, v in verdicts.items() if v == "problem" and lb not in on)
    return out


# ---- the tripwire -----------------------------------------------------------------
def over_share(applied_count: int, sentence_count: int) -> bool:
    """True when applied edits exceed EDIT_SHARE of the document's sentences."""
    return applied_count > EDIT_SHARE * max(sentence_count, 0)
