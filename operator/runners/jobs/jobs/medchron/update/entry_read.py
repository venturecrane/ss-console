"""The full chronology's entry-level read: one unit per dated entry, read
against the pages it cites (and one page either side), and the reply parsed
into findings a script can act on. $0 here; the paid call is the stage's.

WHY (2026-11-18). The per-paragraph "is it supported" audit passed every
false statement on a real run: a claim was the paragraph a citation closes,
so an entry's date and provider lines were never read; omissions were not
defects; and the checker was handed the claim's own values. This reads the
ENTRY, header lines included, as the update lane's review does
(``review_repair``, ``review_parts``, ``review_pages``), and nothing the
reply says is applied here: ``entry_confirm.decide`` decides that.

* A unit carries the entry's full text, its anchor (``review_repair``'s
  ``N<k>`` scheme; ``PRE`` for the prior-history block, read but
  ``record_only``), the pages it cites with one page either side inside the
  same exhibit, and is split when the entry cites more than MAX_PAGES pages.
* A finding names a class from CLASSES, a page the read was SHOWN, and a
  quote found verbatim in the entry; any other finding is dropped and
  counted. At most MAX_FINDINGS per unit are kept; the rest are counted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from ..audit import claims as CL
from ..prompts import load
from . import body as B, review_pages as RV, review_repair as RR

PROMPT = "entry-read-system"
CLASSES = ("value", "wrong_field", "unsupported", "pointer", "omitted", "date", "provider", "another_person")
#: The pages one unit cites before the entry is split into several units.
MAX_PAGES = 40
MAX_FINDINGS = 12
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
    gaps: list[str] = field(default_factory=list)  # index-gap lines (``index_gaps.attach``)

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


@dataclass
class Parsed:
    findings: list[ReadFinding]
    dropped: Counter = field(default_factory=Counter)
    over_cap: int = 0


def _cites_of(text: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), p) for m in CL.CITE.finditer(text) for p in CL.parse_pages(m.group(2)) or [1]}


def _shown(cited: list[tuple[int, int]], npages: Callable[[int], int]) -> list[tuple[int, int]]:
    """The cited pages with one page either side, inside each exhibit's length."""
    out: set[tuple[int, int]] = set()
    for ex, p in cited:
        n = max(npages(ex), 1)
        out |= {(ex, q) for q in (p - 1, p, p + 1) if 1 <= q <= n}
    return sorted(out)


def _units(anchor: str, text: str, cited: set[tuple[int, int]], npages: Callable[[int], int], **kw: Any) -> list[Unit]:
    ps = sorted(cited)
    chunks = [ps[i : i + MAX_PAGES] for i in range(0, len(ps), MAX_PAGES)] or [[]]
    return [Unit(anchor, text, _shown(c, npages), part=i, parts=len(chunks), **kw) for i, c in enumerate(chunks, 1)]


def plan(md: str, keep: set[int], headings: set[str], npages: Callable[[int], int]) -> list[Unit]:
    """One unit per dated entry of the built document (split past MAX_PAGES
    cited pages), and one record-only unit for a cited prior-history block."""
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
    kind = "PRIOR HISTORY BLOCK" if unit.record_only else "ENTRY"
    out: list[dict[str, Any]] = [{"type": "text", "text": client}]
    out.append({"type": "text", "text": f"{kind} {unit.anchor}{split}:\n{unit.text}"})
    if unit.gaps:
        out.append({"type": "text", "text": "WRITER'S INDEX:\n" + "\n".join(unit.gaps)})
    out.append({"type": "text", "text": f"PAGES SHOWN: {_page_list(unit.pages)}"})
    cited = unit.cites()
    for ex, pg in unit.pages:
        label = " (cited by this entry)" if (ex, pg) in cited else " (a page next to a cited page)"
        out += RV.page_blocks(ex, pg, label, images, modes)
    return load(PROMPT, cfg), out


_int = RR._int  # the review's own field reader (a bool or a non-digit is None)


def _items(reply: str, lenient: bool) -> list[Any]:
    """The reply's findings list. Raises ``review_repair.ReplyUnparsed`` exactly
    as the update review's parse does (not JSON, no ``complete``): the stage
    retries or splits the unit on it."""
    RR.parse_reply(reply, lenient=lenient)
    obj = json.loads(reply[reply.find("{") : reply.rfind("}") + 1])
    return list(obj.get("findings") or [])


def _one(item: object, unit: Unit) -> ReadFinding | str:
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
    quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
    if not quote.strip() or quote not in unit.text:
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
        item.get("replacement") if isinstance(item.get("replacement"), str) else "",
        str(item.get("problem") or "")[:400],
        unit.record_only,
    )


def parse(reply: str, unit: Unit, *, lenient: bool = False) -> Parsed:
    """The read's findings, each checked against the unit. Raises
    ``review_repair.ReplyUnparsed`` when the reply is not one complete JSON
    object (the same signal ``review_repair.parse_reply`` gives)."""
    out = Parsed([])
    for item in _items(reply, lenient):
        got = _one(item, unit)
        if isinstance(got, str):
            out.dropped[got] += 1
        elif len(out.findings) >= MAX_FINDINGS:
            out.over_cap += 1
        else:
            out.findings.append(got)
    return out


# ---- the tripwire -----------------------------------------------------------------
_END = re.compile(r"(?<=[.!?])\s+")
_WORDY = re.compile(r"[A-Za-z]{2,}")


def _line_sentences(line: str) -> int:
    text = CL.CITE.sub("", line).strip()
    if not _WORDY.search(text) or (len(text) <= 60 and not re.search(r"[.!?:;]$", text)):
        return 0  # a bare citation, or a heading
    return sum(1 for s in _END.split(text) if _WORDY.search(s))


def count_sentences(body: str) -> int:
    """Sentences of the chronology body: entry text past its date and
    provider lines, the preamble, never a heading or a bare citation."""
    pre, ents = B.split_body(body)
    lines = pre.splitlines() + [ln for e in ents for ln in e.splitlines()[2:]]
    return sum(_line_sentences(ln) for ln in lines)


def over_share(applied_count: int, sentence_count: int) -> bool:
    """True when applied edits exceed EDIT_SHARE of the document's sentences."""
    return applied_count > EDIT_SHARE * max(sentence_count, 0)
