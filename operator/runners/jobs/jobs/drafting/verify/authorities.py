"""The firm's authority store: primary legal text, one authority per file.

    authorities/<slug>.md

    ---
    citation: <the citation as the brief prints it>
    source: <the URL or path the text was read from>
    fetched_at: <ISO time>
    edition | printed_pages | pagination: <optional, where the source has them>
    ---
    <the opinion, statute or instruction text>

The store is a firm input, pinned through an engagements PR and filled by
``templates/drafting/authority_fetch.py`` outside any matter turn (the seat
cannot fetch legal text in a matter turn, ADR 0099 section 3). A brief cites an
authority with ``{{L:<slug>}}``, which renders the entry's ``citation``; an
authority with no entry is ``{{L:?|citation}}`` and is never confirmed from
what a model remembers. After each brief the lane writes
``authorities-wanted.json`` (``write_wanted``) beside the job record: citation
strings only, never a sentence of the brief, so SMD can fetch and pin them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

DIRECTORY = "authorities"
WANTED_FILE = "authorities-wanted.json"
#: An authority's text is windowed around its hits beyond this size.
PASSAGE_CHARS = 30_000
#: Characters kept on each side of a hit.
HIT_PAD = 2_500
REQUIRED = ("citation", "source", "fetched_at")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


class AuthorityError(ValueError):
    """A store file that does not carry its frontmatter or its text."""


def slug(citation: str) -> str:
    """The file name a citation is stored under: lower case, every run of
    other characters one hyphen, at most 80 characters. The same function is
    in ``authority_fetch.py`` (a laptop tool that cannot import the runner);
    a test pins the two equal."""
    s = re.sub(r"[^a-z0-9]+", "-", citation.casefold()).strip("-")
    return s[:80].rstrip("-")


@dataclass(frozen=True)
class Entry:
    slug: str
    citation: str
    source: str
    fetched_at: str
    text: str


def parse_entry(text: str, name: str) -> Entry:
    body = text.replace("\r\n", "\n")
    m = _FRONT.match(body)
    if not m:
        raise AuthorityError(f"{name}: no frontmatter (expected --- citation/source/fetched_at ---)")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            meta[k.strip()] = v.strip()
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise AuthorityError(f"{name}: frontmatter is missing {', '.join(missing)}")
    primary = body[m.end() :].strip()
    if not primary:
        raise AuthorityError(f"{name}: no primary text under the frontmatter")
    return Entry(name, meta["citation"], meta["source"], meta["fetched_at"], primary)


def render_entry(citation: str, source: str, fetched_at: str, text: str, extra: Mapping[str, str] | None = None) -> str:
    """A store file's text (``authority_fetch.render_entry`` writes the same).
    ``extra`` adds optional frontmatter (``edition``, ``printed_pages``,
    ``pagination``) that ``parse_entry`` reads past."""
    one = [" ".join(str(v).split()) for v in (citation, source, fetched_at)]
    more = "".join(f"{k}: {' '.join(str(v).split())}\n" for k, v in (extra or {}).items() if str(v).strip())
    return f"---\ncitation: {one[0]}\nsource: {one[1]}\nfetched_at: {one[2]}\n{more}---\n{text.strip()}\n"


class Store:
    """Every pinned authority, by slug."""

    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self.entries: dict[str, Entry] = {}
        for e in entries:
            if e.slug in self.entries:
                raise AuthorityError(f"{e.slug}: two store files carry this slug")
            self.entries[e.slug] = e

    @classmethod
    def from_dir(cls, directory: Path) -> "Store":
        if not directory.is_dir():
            return cls()
        return cls(
            parse_entry(p.read_text(encoding="utf-8", errors="replace"), p.stem) for p in sorted(directory.glob("*.md"))
        )

    @classmethod
    def from_inputs(cls, root: Path, paths: Iterable[str]) -> "Store":
        """The pinned firm inputs under the store directory (``config.authority_paths``)."""
        out = []
        for rel in paths:
            p = root / rel
            if p.is_file() and p.suffix == ".md":
                out.append(parse_entry(p.read_text(encoding="utf-8", errors="replace"), p.stem))
        return cls(out)

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, s: object) -> bool:
        return s in self.entries

    def get(self, s: str) -> Entry | None:
        return self.entries.get(s)

    def citation(self, s: str) -> str | None:
        """What ``{{L:slug}}`` renders (``evidence.markup.citation_of``)."""
        e = self.entries.get(s)
        return e.citation if e else None

    def lookup(self, citation_or_slug: str) -> Entry | None:
        """The entry for a slug, or for a citation string (by its slug, then by
        the citation printed in a file's frontmatter, spacing ignored)."""
        key = (citation_or_slug or "").strip()
        if key in self.entries:
            return self.entries[key]
        e = self.entries.get(slug(key))
        if e:
            return e
        want = " ".join(key.casefold().split())
        hits = [x for x in self.entries.values() if " ".join(x.citation.casefold().split()) == want]
        return hits[0] if len(hits) == 1 else None

    def index_text(self) -> str:
        return "\n".join(f"- {e.slug}: {e.citation}" for e in self.entries.values()) or (
            "(none: no primary legal text is on file)"
        )

    def passage(self, s: str, terms: Iterable[str], limit: int = PASSAGE_CHARS) -> str:
        """The entry's text, cut to windows around ``terms`` when it is long.
        Empty when the store does not hold ``s``: never a remembered text."""
        e = self.entries.get(s)
        return cut(e.text, terms, limit) if e else ""


def cut(text: str, terms: Iterable[str], limit: int = PASSAGE_CHARS) -> str:
    """``text`` when it fits, else the windows around each term's hits,
    merged, in order, up to ``limit`` characters; the head of the text when no
    term is in it."""
    if len(text) <= limit:
        return text
    low = text.lower()
    spans: list[tuple[int, int]] = []
    for t in sorted({t.lower().strip() for t in terms if len(t.strip()) >= 3}):
        for m in re.finditer(re.escape(t), low):
            spans.append((max(0, m.start() - HIT_PAD), min(len(text), m.end() + HIT_PAD)))
    if not spans:
        return text[:limit]
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    out: list[str] = []
    size = 0
    for a, b in merged:
        if size + (b - a) > limit:
            break
        out.append(text[a:b])
        size += b - a
    return "\n[...]\n".join(out) if out else text[:limit]


# ---- what the brief wanted ---------------------------------------------------------------


@dataclass(frozen=True)
class Want:
    """An authority the brief relied on with no primary text on file."""

    citation: str
    slug: str
    reason: str  # "not_on_file" ({{L:?|...}}) or "unknown_slug" ({{L:slug}} absent from the store)
    uses: int = 1


def wanted(refs: Iterable[tuple[str, str]], store: Store | None) -> list[Want]:
    """``refs`` are (slug, citation) pairs from the brief's L tokens (slug ""
    for ``{{L:?|citation}}``). Every one the store does not hold, merged."""
    by: dict[str, Want] = {}
    for s, citation in refs:
        if s and store is not None and s in store:
            continue
        cite = " ".join((citation or s).split())
        key = s or slug(cite)
        if not key:
            continue
        reason = "unknown_slug" if s else "not_on_file"
        w = by.get(key)
        by[key] = Want(cite, key, reason, (w.uses + 1) if w else 1)
    return list(by.values())


def write_wanted(path: Path, wants: Iterable[Want], job: Mapping[str, Any] | None = None) -> Path:
    """``authorities-wanted.json``: citation strings only (``authority_fetch
    --wanted`` reads it). ``job`` adds the record's own identifiers (job id,
    class), never brief text."""
    body = {
        "job": dict(job or {}),
        "wanted": [{"citation": w.citation, "slug": w.slug, "reason": w.reason, "uses": w.uses} for w in wants],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
