"""What the source check reads: the matter's documents page by page, and the
firm's primary legal texts. Never the digest.

Every document is the pulled file's own text under its ``[p.N]`` markers
(``preflight``; a machine transcription is marked as one), so a page cite
resolves to the page it names and a pin can be tested in code. A page whose
text is thin (``THIN_CHARS``), or a form page behind a "verified" or "signed"
claim, is also read as an image (``check.py``): text extraction prints a blank
signature line's words as if the form were complete (2026-11-04).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ...audit.page_text import parse_segments
from .. import render

#: Below this many characters a page's text is too thin to judge from.
THIN_CHARS = 200
#: The evidence one grading call carries, at most (``check.batches``).
EVIDENCE_CHARS = 240_000
#: The pages a contradiction search adds per item, at most.
SEARCH_PAGES = 2
#: An authority's text is windowed around its hits beyond this size.
AUTHORITY_CHARS = 30_000
_WORD = re.compile(r"[a-z0-9]+")
_FORM = re.compile(r"(?i)\b(verification|declare under penalty|signature|signed|executed on|notary)\b")
_SIGNED_CLAIM = re.compile(r"(?i)\b(verif\w*|sign\w*|execut\w*|attest\w*|sworn|swore|notariz\w*)\b")


def norm(s: str) -> str:
    """Words only, decimal tokens dropped: a transcript's line numbers between
    the words of a quote never decide whether the quote is on the page."""
    return " ".join(t for t in _WORD.findall(s.lower()) if not t.isdecimal())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Doc:
    name: str
    pages: dict[int, str]
    machine: set[int] = field(default_factory=set)
    source_path: str | None = None
    lowered: dict[int, str] = field(default_factory=dict)
    normed: dict[int, str] = field(default_factory=dict)


class Sources:
    def __init__(self, rows: Iterable[dict[str, Any]], authorities: dict[str, str] | None = None) -> None:
        self.docs: dict[str, Doc] = {}
        for r in rows:
            text = Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
            segs = parse_segments(text) if "[p." in text else {1: (text, False)}
            d = Doc(
                str(r["name"]),
                {p: t for p, (t, _m) in segs.items()},
                {p for p, (_t, m) in segs.items() if m},
                r.get("source_path"),
            )
            d.lowered = {p: t.lower() for p, t in d.pages.items()}
            d.normed = {p: norm(t) for p, t in d.pages.items()}
            self.docs[d.name] = d
        self.authorities = dict(authorities or {})

    # ---- names ----------------------------------------------------------------------
    def index_text(self) -> str:
        lines = [f"- {d.name} ({len(d.pages)} page{'s' if len(d.pages) != 1 else ''})" for d in self.docs.values()]
        return "\n".join(lines) or "(no documents)"

    def authority_index(self) -> str:
        return "\n".join(f"- {n}" for n in self.authorities) or "(none: no primary legal text is on file)"

    def resolve(self, name: str) -> str | None:
        """The document a checker's ``file`` names: exact, else by stem."""
        name = (name or "").strip().removeprefix("FILE:").strip()
        if name in self.docs:
            return name
        want = render._stem(name)
        hits = [n for n in self.docs if render._stem(n) == want]
        return hits[0] if len(hits) == 1 else None

    def resolve_authority(self, name: str) -> str | None:
        name = (name or "").strip()
        if name in self.authorities:
            return name
        hits = [n for n in self.authorities if render._stem(n) == render._stem(name)]
        return hits[0] if len(hits) == 1 else None

    # ---- pages ----------------------------------------------------------------------
    def page(self, name: str, p: int) -> str | None:
        d = self.docs.get(name)
        return d.pages.get(p) if d else None

    def has_page(self, name: str, p: int) -> bool:
        return self.page(name, p) is not None

    def window(self, name: str, pages: list[int], pad: int = 1) -> list[int]:
        """The cited pages and the page after each (a quote that stops before
        the next line changes its meaning), and the page before, in range."""
        d = self.docs.get(name)
        if d is None:
            return []
        out: set[int] = set()
        for p in pages:
            out.update(q for q in range(p - pad, p + pad + 1) if q in d.pages)
        return sorted(out)

    def transcript_pages(self, name: str, tps: list[int]) -> list[int]:
        """The file pages carrying transcript pages ``tps``: a condensed
        transcript prints four to a page, so the page number is searched for
        ("Page 45", or a line that is just "45"); unfound, the file page of
        the same number is assumed (and the window around it is read)."""
        d = self.docs.get(name)
        if d is None:
            return []
        out: list[int] = []
        for tp in tps:
            rx = re.compile(rf"(?im)^\s*(?:page\s+)?{tp}\s*$|\bpage\s+{tp}\b")
            hits = [p for p, t in d.pages.items() if rx.search(t)]
            out += hits[:2] if hits else ([tp] if tp in d.pages else [])
        return sorted(set(out))

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
        terms (at least two, or the one term given): where a contradiction of
        the claim would sit."""
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

    # ---- authorities ------------------------------------------------------------------
    def authority_text(self, name: str, terms: list[str]) -> str:
        """The authority's text, windowed around its terms when it is long."""
        text = self.authorities.get(name, "")
        if len(text) <= AUTHORITY_CHARS:
            return text
        low = text.lower()
        spans: list[tuple[int, int]] = []
        for t in [t.lower() for t in terms if len(t) >= 3]:
            for m in re.finditer(re.escape(t), low):
                spans.append((max(0, m.start() - 2_500), min(len(text), m.end() + 2_500)))
        if not spans:
            return text[:AUTHORITY_CHARS]
        spans.sort()
        merged = [spans[0]]
        for a, b in spans[1:]:
            if a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        out, size = [], 0
        for a, b in merged:
            if size + (b - a) > AUTHORITY_CHARS:
                break
            out.append(text[a:b])
            size += b - a
        return "\n[...]\n".join(out)


def load_authorities(root: Path, paths: list[str]) -> dict[str, str]:
    """The authority store: each pinned file, named by its stem."""
    out = {}
    for rel in paths:
        p = root / rel
        if p.is_file():
            out[p.stem] = p.read_text(encoding="utf-8", errors="replace")
    return out
