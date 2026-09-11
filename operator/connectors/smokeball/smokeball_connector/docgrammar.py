"""The drafting content grammar: markdown -> structural blocks, nothing more.

This module is the model-facing half of "code owns typography, the model fills
content" (ADR 0083). It parses the small markdown subset the drafting lane
writes into a flat list of blocks with inline runs. It decides NOTHING about
fonts, spacing, or styles; ``docx_format`` does that per document class.

The subset (documented once, in ``operator/templates/drafting/
drafting-discipline.md`` Part IV):

* ``#`` / ``##`` / ``###`` headings (the heading TEXT is content: a model writes
  ``## I. Introduction`` and the numeral stays, because the body cross-references
  it; the renderer styles the level and never renumbers),
* paragraphs (blank-line separated) with ``**bold**`` / ``*italic*`` runs,
* ``-`` / ``*`` bullets,
* literal ``1.`` numbered items (the number is content; discovery item numbers
  come from the propounded set, never from a counter),
* pipe tables (``| a | b |``; an optional ``| --- | --- |`` separator after the
  first row marks it as a header row),
* a line that is exactly ``---`` outside a table is a horizontal rule,
* ``{{...}}`` markers, preserved VERBATIM as their own runs. Markers contain
  pipes (``{{FILL: date | proof of service}}``) and every shipped skeleton puts
  them inside table cells, so markers are tokenized BEFORE any cell or emphasis
  split and restored afterwards. Shredding a marker inside a table is a
  Pattern-B failure (a cell that reads as a fact), which is why the order of
  operations here is the whole point of the module.

Anything else renders as plain paragraph text with its characters intact: the
reader sees it, which remains the only acceptable failure mode in a document an
attorney reviews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Well-formed markers only; the content gate runs before the renderer so every
# ``{{`` has its ``}}`` by the time text reaches this module.
MARKER_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\d+[.)])\s+(.*)$")
_HRULE_RE = re.compile(r"^-{3,}$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
# Emphasis and code spans. Backticks are markdown SYNTAX, like ``**``: the shipped
# skeletons wrap markers in them (`` `{{FILL: ...}}` ``) for human readers, and a
# Word document must not carry literal backticks. A code span renders as its
# plain text; nothing inside it is styled.
_EMPHASIS_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")

# Private-use placeholders stand in for markers while cells/emphasis are split.
_PLACEHOLDER = "{}"
_PLACEHOLDER_RE = re.compile("(\\d+)")


@dataclass(frozen=True)
class Run:
    """One inline run. ``marker`` runs are emitted literally and unstyled so a
    marker is never restyled, hidden, or split by the emphasis pass."""

    text: str
    bold: bool = False
    italic: bool = False
    marker: bool = False


@dataclass(frozen=True)
class Heading:
    level: int
    runs: tuple[Run, ...]

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass(frozen=True)
class Paragraph:
    runs: tuple[Run, ...]

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass(frozen=True)
class Bullet:
    runs: tuple[Run, ...]


@dataclass(frozen=True)
class Numbered:
    label: str  # the literal "1." / "2)" the author wrote
    runs: tuple[Run, ...]


@dataclass(frozen=True)
class Table:
    rows: tuple[tuple[tuple[Run, ...], ...], ...]
    header: bool = False


@dataclass(frozen=True)
class HRule:
    pass


Block = Heading | Paragraph | Bullet | Numbered | Table | HRule


@dataclass
class _MarkerStash:
    """Marker text tokenized out of a line, restorable by index."""

    markers: list[str] = field(default_factory=list)

    def stash(self, text: str) -> str:
        def _sub(m: re.Match[str]) -> str:
            self.markers.append(m.group(0))
            return _PLACEHOLDER.format(len(self.markers) - 1)

        return MARKER_RE.sub(_sub, text)

    def runs(self, text: str) -> tuple[Run, ...]:
        """Emphasis-split ``text`` (which may hold placeholders) into runs,
        then restore each placeholder as a literal, unstyled marker run. The
        emphasis pass sees the WHOLE line, so ``**LABEL {{marker}}:**`` (the
        shipped skeletons' item-label shape) bolds the label text around the
        marker; the marker itself stays verbatim and unstyled."""
        out: list[Run] = []
        for run in _emphasis_runs(text):
            pos = 0
            for m in _PLACEHOLDER_RE.finditer(run.text):
                if m.start() > pos:
                    out.append(Run(run.text[pos : m.start()], run.bold, run.italic))
                out.append(Run(self.markers[int(m.group(1))], marker=True))
                pos = m.end()
            if pos < len(run.text):
                out.append(Run(run.text[pos:], run.bold, run.italic))
        return tuple(out)


def _emphasis_runs(text: str) -> list[Run]:
    runs: list[Run] = []
    for part in _EMPHASIS_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append(Run(part[2:-2], bold=True))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            runs.append(Run(part[1:-1], italic=True))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            runs.append(Run(part[1:-1]))
        else:
            runs.append(Run(part))
    return runs


def inline_runs(text: str) -> tuple[Run, ...]:
    """Runs for one line of text: markers verbatim, emphasis applied outside
    them. The unit every block kind shares."""
    stash = _MarkerStash()
    return stash.runs(stash.stash(text))


def _is_table_line(line: str) -> bool:
    # Markers are stashed before this test so a pipe INSIDE a marker cannot
    # make a prose line look like a table row.
    stashed = _MarkerStash().stash(line)
    return stashed.startswith("|") and stashed.count("|") >= 2


def _split_cells(line: str) -> tuple[tuple[Run, ...], ...]:
    stash = _MarkerStash()
    stashed = stash.stash(line).strip()
    if stashed.startswith("|"):
        stashed = stashed[1:]
    if stashed.endswith("|"):
        stashed = stashed[:-1]
    return tuple(stash.runs(cell.strip()) for cell in stashed.split("|"))


def _table_sep(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def parse_document(markdown: str) -> list[Block]:
    """Parse ``markdown`` into blocks. Pure; never raises on unknown syntax
    (it degrades to a paragraph, visibly)."""
    blocks: list[Block] = []
    lines = markdown.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if _is_table_line(line):
            rows: list[tuple[tuple[Run, ...], ...]] = []
            header = False
            while i < n and _is_table_line(lines[i].strip()):
                cur = lines[i].strip()
                if _table_sep(cur):
                    header = len(rows) == 1
                else:
                    rows.append(_split_cells(cur))
                i += 1
            if rows:
                blocks.append(Table(tuple(rows), header=header))
            continue
        if _HRULE_RE.match(line):
            blocks.append(HRule())
            i += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(Heading(len(heading.group(1)), inline_runs(heading.group(2).strip())))
            i += 1
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            blocks.append(Bullet(inline_runs(bullet.group(1).strip())))
            i += 1
            continue
        numbered = _NUMBERED_RE.match(line)
        if numbered:
            blocks.append(Numbered(numbered.group(1), inline_runs(numbered.group(2).strip())))
            i += 1
            continue
        blocks.append(Paragraph(inline_runs(line)))
        i += 1
    return blocks


__all__ = [
    "MARKER_RE",
    "Block",
    "Bullet",
    "HRule",
    "Heading",
    "Numbered",
    "Paragraph",
    "Run",
    "Table",
    "inline_runs",
    "parse_document",
]
