"""What the check reads: the matter's documents page by page. Never the
digest, never the fact index, never a working paper.

Every document is the pulled file's own text under its ``[p.N]`` markers
(``preflight``; a machine transcription is marked as one), so a page cite
resolves to the page it names. A document named like one of the lane's own
working papers (``digest.md``, the fact index, an earlier draft) is refused at
load: those are what the model wrote, and the check exists because the old
checks compared the brief to them (2026-11-04: 23 wrong items, 0 caught).

Primary legal text is the authority store's (``authorities.Store``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ...audit.page_text import parse_segments
from .. import render
from ..evidence.shapes import DocPages

#: Below this many characters a page's text is too thin to judge from.
THIN_CHARS = 200
#: One page's text shown to a checker, at most.
PAGE_CHARS = 6_000
#: The pages a contradiction search adds per item, at most.
SEARCH_PAGES = 2
_WORD = re.compile(r"[a-z0-9]+")
_FORM = re.compile(r"(?i)\b(verification|declare under penalty|signature|signed|executed on|notary)\b")
_SIGNED_CLAIM = re.compile(r"(?i)\b(verif\w*|sign\w*|execut\w*|attest\w*|sworn|swore|notariz\w*)\b")
#: The lane's own papers: never a source (module docstring).
NOT_SOURCES = re.compile(
    r"(?i)(^|[/_\- .])(digest|fact[-_ ]?index|working[-_ ]?papers?|draft(?:-v\d+)?|notes?|review|ledger)\.(md|txt|json|jsonl)$"
)


def norm(s: str) -> str:
    """Words only, decimal tokens dropped: a transcript's line numbers between
    the words of a quote never decide whether the quote is on the page."""
    return " ".join(t for t in _WORD.findall(s.lower()) if not t.isdecimal())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_working_paper(name: str) -> bool:
    return bool(NOT_SOURCES.search(name.strip()))


@dataclass
class Doc:
    name: str
    pages: dict[int, str]
    machine: set[int] = field(default_factory=set)
    source_path: str | None = None
    lowered: dict[int, str] = field(default_factory=dict)
    normed: dict[int, str] = field(default_factory=dict)


class Sources:
    """The matter's documents, page by page. ``refused`` names every document
    left out as a working paper."""

    def __init__(self, docs: Iterable[Doc] = (), log: Callable[[str], None] | None = None) -> None:
        self.docs: dict[str, Doc] = {}
        self.refused: list[str] = []
        for d in docs:
            if is_working_paper(d.name):
                self.refused.append(d.name)
                if log:
                    log(f"SOURCES: {d.name} refused as a source (the lane's own paper, not the record)")
                continue
            d.lowered = {p: t.lower() for p, t in d.pages.items()}
            d.normed = {p: norm(t) for p, t in d.pages.items()}
            self.docs[d.name] = d

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, Any]], log: Callable[[str], None] | None = None) -> "Sources":
        """From ``extracted.jsonl`` rows (``name``, ``text_path``)."""
        out = []
        for r in rows:
            text = Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            out.append(doc_from_text(str(r["name"]), text, r.get("source_path")))
        return cls(out, log)

    @classmethod
    def from_docs(cls, docs: Iterable[DocPages], log: Callable[[str], None] | None = None) -> "Sources":
        """From the evidence stage's corpus (``shapes.DocPages``)."""
        return cls(
            (Doc(d.name, dict(d.pages), set(d.machine), d.source_path) for d in docs),
            log,
        )

    # ---- names ----------------------------------------------------------------------
    def index_text(self) -> str:
        lines = [f"- {d.name} ({len(d.pages)} page{'s' if len(d.pages) != 1 else ''})" for d in self.docs.values()]
        return "\n".join(lines) or "(no documents)"

    def resolve(self, name: str) -> str | None:
        """The document a ``file`` names: exact, else by stem."""
        name = (name or "").strip().removeprefix("FILE:").strip()
        if name in self.docs:
            return name
        want = render._stem(name)
        hits = [n for n in self.docs if render._stem(n) == want]
        return hits[0] if len(hits) == 1 else None

    # ---- pages ----------------------------------------------------------------------
    def page(self, name: str, p: int) -> str | None:
        d = self.docs.get(name)
        return d.pages.get(p) if d else None

    def has_page(self, name: str, p: int) -> bool:
        return self.page(name, p) is not None

    def window(self, name: str, pages: Iterable[int], pad: int = 1) -> list[int]:
        """The cited pages and the pages either side, in range."""
        d = self.docs.get(name)
        if d is None:
            return []
        out: set[int] = set()
        for p in pages:
            out.update(q for q in range(p - pad, p + pad + 1) if q in d.pages)
        return sorted(out)

    def thin(self, name: str, p: int) -> bool:
        t = self.page(name, p)
        return t is None or len(t.strip()) < THIN_CHARS

    def form_behind(self, name: str, p: int, statement: str) -> bool:
        """A form or signature page behind a "verified" or "signed" claim."""
        t = self.page(name, p) or ""
        return bool(_SIGNED_CLAIM.search(statement) and _FORM.search(t))

    def quote_pages(self, name: str, quote: str) -> list[int]:
        """The pages of ``name`` that carry ``quote`` (normalized)."""
        q = norm(quote)
        d = self.docs.get(name)
        if not q or d is None:
            return []
        return [p for p, t in d.normed.items() if q in t]

    def quote_anywhere(self, quote: str) -> list[tuple[str, int]]:
        q = norm(quote)
        if not q:
            return []
        return [(d.name, p) for d in self.docs.values() for p, t in d.normed.items() if q in t]

    def search(self, terms: list[str], skip: set[tuple[str, int]], limit: int = SEARCH_PAGES) -> list[tuple[str, int]]:
        """Pages, not already read for the item, carrying the most of its
        terms (at least two, or the one term given)."""
        ts = [t.lower().strip() for t in terms if len(t.strip()) >= 3][:6]
        if not ts:
            return []
        need = min(2, len(ts))
        scored = []
        for d in self.docs.values():
            for p, low in d.lowered.items():
                if (d.name, p) in skip:
                    continue
                n = sum(1 for t in ts if t in low)
                if n >= need:
                    scored.append((-n, d.name, p))
        return [(n, p) for _s, n, p in sorted(scored)[:limit]]

    def pages_sha(self, refs: Iterable[tuple[str, int]]) -> str:
        return sha("\n".join(f"{n}|{p}|{sha(self.page(n, p) or '')}" for n, p in sorted(set(refs))))

    def page_block(self, name: str, p: int, limit: int = PAGE_CHARS) -> str:
        """One page's text as a checker sees it, cut to ``limit``."""
        t = (self.page(name, p) or "").strip()
        d = self.docs.get(name)
        mark = " (machine transcription)" if d is not None and p in d.machine else ""
        if len(t) > limit:
            t = t[:limit] + " [...]"
        return f"{name} p.{p}{mark}:\n{t}"


def doc_from_text(name: str, text: str, source_path: str | None = None) -> Doc:
    segs = parse_segments(text) if "[p." in text else {1: (text, False)}
    return Doc(name, {p: t for p, (t, _m) in segs.items()}, {p for p, (_t, m) in segs.items() if m}, source_path)
