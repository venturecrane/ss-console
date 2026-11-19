"""The writer's own INDEX rows against the entries it wrote. $0, no model.

Each window's writer output (``prompts/map-system.md``) carries an ENTRIES
block and an INDEX block, one row per entry: ``YYYY-MM-DD | provider | codes
| file``. ``stages/assemble.py`` reads the entries and ignores the INDEX;
``stages/build_doc.py`` reads only its codes. A row whose visit has an entry
but whose codes the entry never states is a gap the entry read is told about
(one line); a row that matches no entry is recorded only. A row that does not
parse is counted, never raised on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...text_rules import DATE_HEAD, ENTRY_START
from . import body as B
from .icd import ICD_SHAPE

_ROW_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINE = "The writer's own index for this visit lists codes {codes} that the entry does not state."


@dataclass
class Gap:
    kind: str  # "codes" | "no_entry"
    date: str  # YYYY-MM-DD
    provider: str
    file: str
    codes: list[str] = field(default_factory=list)
    anchor: str = ""  # the entry the gap rides into (``attach``)


def section(txt: str, name: str) -> str:
    """One ``## NAME`` block of a writer output (the shape ``stages/assemble.section`` reads)."""
    m = re.search(rf"##\s*{name}\s*\n(.*?)(?=\n##\s|\Z)", txt, re.S)
    return m.group(1).strip() if m else ""


def map_entries(map_text: str) -> list[str]:
    """The ENTRIES block's dated entries, split as assemble splits them."""
    body = section(map_text, "ENTRIES")
    return [c.strip() for c in ENTRY_START.split(body) if DATE_HEAD.match(c.strip())]


def _key(provider: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (provider or "").lower())[:28]


def _same_provider(a: str, b: str) -> bool:
    ka, kb = _key(a), _key(b)
    return bool(ka and kb) and (ka.startswith(kb) or kb.startswith(ka))


def _codes(cell: str) -> list[str]:
    out = []
    for code in re.split(r"[,/;]\s*", cell):
        code = re.sub(r"\s*\(.*?\)", "", code).strip()
        if code and ICD_SHAPE.fullmatch(code):
            out.append(code)
    return out


def _states(text: str, code: str) -> bool:
    low = text.lower()
    return code.lower() in low or code.replace(".", "").lower() in low.replace(".", "")


def _row(line: str) -> tuple[str, str, list[str], str] | None:
    cols = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cols) < 4 or not _ROW_DATE.match(cols[0]):
        return None
    return cols[0], cols[1], _codes(cols[2]), cols[3]


def scan(map_text: str, entries: list[str] | None = None) -> tuple[list[Gap], int]:
    """(gaps, malformed row count) for one window's writer output; `entries`
    defaults to the ones its ENTRIES block holds."""
    ents = map_entries(map_text) if entries is None else entries
    out: list[Gap] = []
    bad = 0
    for line in section(map_text, "INDEX").splitlines():
        if not line.strip() or line.strip().lower().startswith("none"):
            continue
        row = _row(line)
        if row is None:
            bad += 1
            continue
        day, prov, codes, fname = row
        mine = [e for e in ents if B.iso(e) == day and _same_provider(B.provider_of(e), prov)]
        if not mine:
            out.append(Gap("no_entry", day, prov, fname))
            continue
        missing = [c for c in codes if not any(_states(e, c) for e in mine)]
        if missing:
            out.append(Gap("codes", day, prov, fname, missing))
    return out, bad


def gaps(map_text: str, entries: list[str] | None = None) -> list[Gap]:
    return scan(map_text, entries)[0]


def attach(units: list[Any], found: list[Gap]) -> list[Gap]:
    """Put each codes gap's line on the units of the entry it names (same
    date, same provider); returns the codes gaps no unit took."""
    left = []
    for g in (x for x in found if x.kind == "codes"):
        hits = [u for u in units if not u.record_only and B.iso(u.text) == g.date
                and _same_provider(B.provider_of(u.text), g.provider)]  # fmt: skip
        for u in hits:
            u.gaps.append(LINE.format(codes=", ".join(g.codes)))
            g.anchor = u.anchor
        if not hits:
            left.append(g)
    return left
