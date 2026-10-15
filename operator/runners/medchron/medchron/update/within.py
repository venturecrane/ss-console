"""A sentence an entry states twice is said once. $0, no model.

Merge joins the notes of one visit, and an entry can come out stating its
procedure and its pathology twice, once under each heading it drew from
(2026-10-13 review, fix 22). Here a sentence or a semicolon clause whose
words AND numbers equal an earlier one in the same entry is removed; the first
occurrence stays with its citation. When the repeat cites other pages of the
same exhibit, those pages join the first occurrence's citation, so no page the
statement rests on is lost. A repeat citing another exhibit leaves the first
citation as it is: the statement is identical, so its first source supports
it, and a claim with two citations side by side would audit only the first.

Only exact repeats go (same words, same numbers, in order), and only ones of
at least MIN_WORDS words closed by their own punctuation: a short line ("Normal.") means something different
under each heading it sits under. A heading whose every line went is removed
with them. Nothing that states a value differently is ever touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_WORDS = 6
SAID_TWICE = "removed: a sentence an entry stated twice"
CITE = re.compile(r"\(Exhibit (\d+)\s*(?:-\s*p\.\s*([0-9,\s\-]+?))?\s*(?:,\s*machine transcription)?\)")
_PLAIN = re.compile(r"\(Exhibit \d+(?: - p\. [0-9,\s\-]+)?\)")
_PIECE = re.compile(r"(?<=[.!?;])\s+")


def _key(s: str) -> str:
    return " ".join(re.findall(r"[a-z]+|\d+(?:[.,/:]\d+)*", s.lower()))


def _whole(piece: str, key: str) -> bool:
    """A whole statement: MIN_WORDS words or more, closed by its own
    punctuation. A fragment that runs into its citation ("reported in
    (Exhibit 4)") says nothing without it and is never removed."""
    return piece.rstrip().endswith((".", "!", "?", ";")) and len(re.findall(r"[a-z]+", key)) >= MIN_WORDS


def _pages(spec: str | None) -> list[int]:
    out: list[int] = []
    for part in (spec or "").split(","):
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", part)
        if m and int(m.group(1)) <= int(m.group(2)):
            out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            out.append(int(part.strip()))
    return out


def _spec(pages: list[int]) -> str:
    ps, runs = sorted(set(pages)), []
    for p in ps:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return ", ".join(f"{a}-{b}" if b > a else f"{a}" for a, b in runs)


@dataclass
class _Seg:
    pieces: list[str]
    cite: str | None
    removed: int = 0
    parsed: str | None = None

    def __post_init__(self) -> None:
        self.parsed = self.cite


@dataclass
class _Line:
    segs: list[_Seg] = field(default_factory=list)
    changed: bool = False


def _parse(line: str) -> _Line:
    out, prev = _Line(), 0
    for m in CITE.finditer(line):
        out.segs.append(_Seg([p for p in _PIECE.split(line[prev : m.start()].strip()) if p], m.group(0)))
        prev = m.end()
    rest = line[prev:].strip()
    if rest:
        out.segs.append(_Seg([p for p in _PIECE.split(rest) if p], None))
    return out


def _join_cite(first: str, repeat: str) -> str:
    """`first` with the pages of `repeat` added when both cite one exhibit
    in the plain form; otherwise `first` unchanged."""
    a, b = CITE.fullmatch(first), CITE.fullmatch(repeat)
    if not (a and b and _PLAIN.fullmatch(first) and _PLAIN.fullmatch(repeat)) or a.group(1) != b.group(1):
        return first
    pa, pb = _pages(a.group(2)), _pages(b.group(2))
    if not pa or not pb or set(pb) <= set(pa):
        return first
    return f"(Exhibit {a.group(1)} - p. {_spec(pa + pb)})"


def _render(ln: _Line) -> str:
    parts = []
    for s in ln.segs:
        text = " ".join(s.pieces)
        if s.cite is not None and (text or not s.removed):
            parts.append(f"{text} {s.cite}".strip())
        elif text:
            parts.append(text)
    return " ".join(parts)


def dedupe(entry: str) -> tuple[str, int]:
    """(entry with each repeated sentence said once, how many went)."""
    lines = entry.splitlines()
    if len(lines) < 3:
        return entry, 0
    body = [_parse(ln) for ln in lines[2:]]
    first: dict[str, _Seg] = {}
    gone = 0
    for ln in body:
        for seg in ln.segs:
            kept = []
            for piece in seg.pieces:
                k = _key(piece)
                if not _whole(piece, k):
                    kept.append(piece)
                    continue
                if k not in first:
                    first[k] = seg
                    kept.append(piece)
                    continue
                orig = first[k]
                if orig.cite is not None and seg.cite is not None:
                    orig.cite = _join_cite(orig.cite, seg.cite)
                seg.removed += 1
                ln.changed = True
                gone += 1
            seg.pieces = kept
    if not gone:
        return entry, 0
    rendered = [_render(ln) if ln.changed or any(s.cite != s.parsed for s in ln.segs) else None for ln in body]
    out = [lines[i + 2] if r is None else r for i, r in enumerate(rendered)]
    emptied = {i for i, r in enumerate(rendered) if r is not None and not r.strip() and lines[i + 2].strip()}
    out = _drop_bare_headings(out, emptied)
    return "\n".join(lines[:2] + out), gone


def _drop_bare_headings(out: list[str], emptied: set[int]) -> list[str]:
    """Remove the lines this pass emptied, and a heading (an uncited line)
    whose every following line up to the next blank line was emptied."""
    drop = set(emptied)
    for i, ln in enumerate(out):
        if i in drop or not ln.strip() or CITE.search(ln):
            continue
        j, block = i + 1, []
        while j < len(out) and (out[j].strip() or j in emptied):
            block.append(j)
            j += 1
        if block and all(b in emptied for b in block):
            drop.add(i)
    return [ln for i, ln in enumerate(out) if i not in drop]
