"""The pages an update's writer held and cited nothing from, found and settled
by code (``stages/update_uncited`` is the stage). $0, no model.

For each writer chunk of the update (its whole ``chunk-XX.txt``, so a chunk
that was retried, split or repaired counts each page once), the pages it held
are its ``[p.N]`` marks, context blocks excluded (``chunking.pieces_pages``).
A page is CITED when an entry of any writer map cites it (``(FILE: <name>,
p. N)``, resolved to its file the way ``assemble`` resolves it). Only the
base chunks count, never the extra chunks this stage wrote: their entries
must not change which pages are uncited, or a resume would route a
different set.

Code settles, with its reason, what code can know (``SETTLED``). Before any
request, ``prefilter`` settles two more ($0): a page whose trusted text layer
(``page_mode``: native, not a form, a grid or a result) prints no date
outside a print, fax or as-of header line (``no_date``), and a page that is an
exact copy, by its words or its picture, of an uncited page already kept for
the reader (``duplicate_uncited``: it is read once). Every other uncited page
goes to the reader, in runs of at most ``RUN_PAGES`` adjacent pages of one
document.

LIMIT: a page some entry cites is never examined here, though it may hold
dated rows the entry does not chart (a real update cited 2 of 20
immunization rows on one page).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..medchron import page_mode as PM, pages_record as PR
from ..medchron.audit.page_text import PageIndex
from ..medchron.update import pageid_io as PG, select as S
from ..medchron.update.billing_page import is_billing
from .assemble import CITE, CITE_NOPAGE, FILE_HEADER, MAP_FILE, Resolver, parse_maps, section
from .base import read_json
from .chunking import pieces_pages, sha
from .compose import UNREAD
from .coverage import file_key
from .repair import truncated_targets

#: Adjacent pages of one document one reader request shows at most.
RUN_PAGES = 6
#: A citation span wider than this is read as its two ends (a typo, never 400 pages).
MAX_SPAN = 60
#: The date and provider lines of entries shown with a run, at most.
MAX_CONTEXT = 40
TRUNCATED, NAMED_UNREAD, BILLING, BLANK, DELIVERED_COPY, CITED_COPY = (
    "truncated",
    "named_unread",
    "billing",
    "blank",
    "delivered_copy",
    "cited_copy",
)
NO_DATE, DUP_UNCITED = "no_date", "duplicate_uncited"
#: Reasons code settles a page by, without a model. ``truncated``: the
#: chunk's map ran out and ``repair_truncated`` reads it again.
SETTLED = (TRUNCATED, NAMED_UNREAD, BILLING, BLANK, DELIVERED_COPY, CITED_COPY, NO_DATE, DUP_UNCITED)
#: Any written date (wider than MM/DD/YYYY on purpose: a page settles only when it has none).
_ANY_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
#: A line whose date is the printout's, never care's.
_HEADER = re.compile(
    r"(?i)\b(printed|print date|printed on|fax(ed)?|as of|generated|run date|report date|page \d+ of)\b"
)
#: A page's words are compared as a copy only when it carries at least this many characters of them.
DUP_MIN_CHARS = 40
_EXHIBIT = re.compile(r"^Exhibit (\d+) - ")
_ALNUM = re.compile(r"[A-Za-z0-9]")
Page = tuple[int, int]  # (exhibit, page)


@dataclass(frozen=True)
class Doc:
    """One new exhibit as the writer read it (its units row)."""

    exhibit: int
    doc_id: str
    row: dict[str, Any]

    @property
    def name(self) -> str:
        return file_key(self.row)


@dataclass
class Sweep:
    docs: dict[int, Doc]
    held: set[Page] = field(default_factory=set)
    cited: set[Page] = field(default_factory=set)
    settled: dict[Page, str] = field(default_factory=dict)
    open: list[Page] = field(default_factory=list)
    context: dict[int, list[str]] = field(default_factory=dict)  # exhibit -> date | provider lines
    settler: Any = None

    @property
    def uncited(self) -> int:
        return len(self.settled) + len(self.open)

    def counts(self) -> dict[str, int]:
        return dict(Counter(self.settled.values()))


def docs_of(slug_dir: Path, unit: str) -> dict[int, Doc]:
    out: dict[int, Doc] = {}
    for r in read_json(slug_dir / "units" / f"{unit}.json", []) or []:
        m = _EXHIBIT.match(str(r.get("name") or ""))
        if m and r.get("compose", True):
            out[int(m.group(1))] = Doc(int(m.group(1)), str(r["id"]), r)
    return out


def whole_chunks(d: Path) -> dict[int, str]:
    """{chunk number: its text's sha} for every whole chunk on disk."""
    out: dict[int, str] = {}
    for p in d.glob("chunk-*.txt"):
        m = re.fullmatch(r"chunk-(\d+)\.txt", p.name)
        if m:
            out[int(m.group(1))] = sha(p.read_text(encoding="utf-8"))
    return out


def base_chunks(d: Path, extras: dict[int, str]) -> list[int]:
    """The writer's own chunks: every whole chunk this stage did not write
    (a number it recorded whose text compose has since replaced is compose's)."""
    return sorted(i for i, h in whole_chunks(d).items() if extras.get(i) != h)


def maps_of(d: Path, chunks: list[int]) -> list[str]:
    """The live map files of these chunks (a set-aside ``.truncated`` is not live)."""
    want = set(chunks)
    out = []
    for p in d.iterdir():
        m = MAP_FILE.fullmatch(p.name)
        if m and int(m.group(1)) in want:
            out.append(p.name)
    return sorted(out)


def page_spec(spec: str) -> set[int]:
    """The pages of a citation's page groups (``, p. 3-5, p. 7`` or ``, p. 3, 5``)."""
    out: set[int] = set()
    for part in re.split(r",|\bp\.", spec):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out |= set(range(a, b + 1)) if a <= b <= a + MAX_SPAN else {a, b}
        elif part.isdigit():
            out.add(int(part))
    return out


class _Cites:
    def __init__(self, docs: dict[int, Doc]) -> None:
        self.by_name = {doc.name: doc.exhibit for doc in docs.values()}
        names = list(self.by_name)
        self.resolve = Resolver(names, {doc.doc_id: doc.name for doc in docs.values()})
        self.npages: dict[int, set[int]] = {}

    def exhibit(self, cited: str) -> int | None:
        got = self.resolve(cited.strip())
        return self.by_name.get(got) if got else None

    def pages(self, text: str) -> set[Page]:
        out: set[Page] = set()
        for name, spec in CITE.findall(text):
            x = self.exhibit(name)
            if x is not None:
                out |= {(x, p) for p in page_spec(spec)}
        # A paged citation is taken out first: a title's own "(Medical Records)"
        # closes the bare pattern early, so it would read as a whole-file citation.
        for name in CITE_NOPAGE.findall(CITE.sub("", text)):
            x = None if ", p." in name else self.exhibit(name)
            if x is not None:  # a citation of the whole file cites every page it holds
                out |= {(x, p) for p in self.npages.get(x, set())}
        return out


def cited(d: Path, maps: list[str], docs: dict[int, Doc], held: set[Page] | None = None) -> set[Page]:
    """Every page an entry of these maps cites."""
    return _scan(d, maps, docs, held or set())[0]


def _scan(d: Path, maps: list[str], docs: dict[int, Doc], held: set[Page]) -> tuple[set[Page], set[Page], dict]:
    """(pages entries cite, pages BILLING-DATES cites, exhibit -> context lines)."""
    c = _Cites(docs)
    for x, p in held:
        c.npages.setdefault(x, set()).add(p)
    entries, _ = parse_maps(d, maps)
    got: set[Page] = set()
    context: dict[int, list[str]] = {}
    for e in entries:
        c.resolve.scope = e.get("seen") or {}
        pages = c.pages(e["text"])
        got |= pages
        line = f"{e['text'].splitlines()[0].strip()} | {e['provider']}"
        for x in sorted({x for x, _ in pages}):
            ctx = context.setdefault(x, [])
            if line not in ctx and len(ctx) < MAX_CONTEXT:
                ctx.append(line)
    billing: set[Page] = set()
    for fn in maps:
        txt = (d / fn).read_text(encoding="utf-8")
        seen: dict[str, set[str]] = {}
        for name, fid in FILE_HEADER.findall(txt):
            seen.setdefault(name.strip(), set()).add(fid)
        c.resolve.scope = seen
        billing |= c.pages(section(txt, "BILLING-DATES"))
    return got, billing, context


def _held(d: Path, chunks: list[int], docs: dict[int, Doc]) -> tuple[set[Page], set[int]]:
    """(pages the chunks held, the chunks whose map ran out)."""
    c = _Cites(docs)
    out: set[Page] = set()
    for i in chunks:
        for piece in pieces_pages((d / f"chunk-{i:02d}.txt").read_text(encoding="utf-8")):
            x = c.by_name.get(str(piece["file"]).strip())
            if x is None and piece.get("file_id"):
                x = next((doc.exhibit for doc in docs.values() if doc.doc_id == piece["file_id"]), None)
            if x is not None:
                out |= {(x, p) for p in piece["pages"] or []}
    return out, {t.c for t in truncated_targets(d)} & set(chunks)


def _chunk_pages(d: Path, chunks: set[int], docs: dict[int, Doc]) -> set[Page]:
    return _held(d, sorted(chunks), docs)[0] if chunks else set()


def _named_unread(d: Path, docs: dict[int, Doc]) -> tuple[set[Page], set[int]]:
    """(pages compose named unread, exhibits it named unread whole)."""
    c = _Cites(docs)
    out: set[Page] = set()
    whole: set[int] = set()
    for r in read_json(d / UNREAD, []) or []:
        x = c.by_name.get(str(r.get("file") or "").strip())
        if x is None:
            continue
        if r.get("pages"):
            out |= {(x, int(p)) for p in r["pages"]}
        else:
            whole.add(x)
    return out, whole


class _Settler:
    """The reason code settles one uncited page by, or None."""

    def __init__(self, slug_dir: Path, d: Path, docs: dict[int, Doc], cited: set[Page], billing: set[Page]) -> None:
        self.slug_dir, self.docs, self.cited, self.billing = slug_dir, docs, cited, billing
        self.texts: dict[int, dict[int, str]] = {}
        self.ink = {(str(r["doc"]), int(r["page"])): (r.get("evidence") or {}).get("inked") for r in PR.read(d)}
        self.ident = PG.load(d / "update")
        self.by_id = {doc.doc_id: doc.exhibit for doc in docs.values()}
        self.unread, self.unread_whole = _named_unread(d, docs)

    def text(self, x: int, p: int) -> str:
        if x not in self.texts:
            tp = self.slug_dir / "text" / f"{self.docs[x].doc_id}.txt"
            self.texts[x] = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
        return self.texts[x].get(p) or ""

    def copy(self, x: int, p: int) -> str | None:
        hit = (self.ident.pages.get(self.docs[x].doc_id) or {}).get(p)
        if hit is None or not hit.exact:
            return None
        if PG.delivered_exhibit(hit.other) is not None:
            return DELIVERED_COPY
        other = self.by_id.get(hit.other)
        return CITED_COPY if other is not None and (other, hit.page) in self.cited else None

    def reason(self, x: int, p: int) -> str | None:
        if (x, p) in self.unread or x in self.unread_whole:
            return NAMED_UNREAD
        text = self.text(x, p)
        if (x, p) in self.billing or (text and is_billing(text)):
            return BILLING
        if not _ALNUM.search(text) and self.ink.get((self.docs[x].doc_id, p)) is False:
            return BLANK
        return self.copy(x, p)


def sweep(slug_dir: Path, unit: str, extras: dict[int, str]) -> Sweep:
    """The base chunks' uncited pages, each settled by code or left open."""
    d = slug_dir / "runs" / unit
    docs = docs_of(slug_dir, unit)
    base = base_chunks(d, extras)
    held, trunc = _held(d, base, docs)
    got, billing, context = _scan(d, maps_of(d, base), docs, held)
    settler = _Settler(slug_dir, d, docs, got, billing)
    sw = Sweep(docs, held, got, context=context, settler=settler)
    truncated = _chunk_pages(d, trunc, docs)
    for x, p in sorted(held - got):
        why = TRUNCATED if (x, p) in truncated else settler.reason(x, p)
        if why is None:
            sw.open.append((x, p))
        else:
            sw.settled[(x, p)] = why
    return sw


def runs(pages: list[Page]) -> list[tuple[int, tuple[int, ...]]]:
    """Adjacent pages of one exhibit, at most RUN_PAGES to a run."""
    out: list[tuple[int, list[int]]] = []
    for x, p in sorted(pages):
        last = out[-1] if out else None
        if last and last[0] == x and last[1][-1] == p - 1 and len(last[1]) < RUN_PAGES:
            last[1].append(p)
        else:
            out.append((x, [p]))
    return [(x, tuple(ps)) for x, ps in out]


def dated_outside_headers(text: str) -> bool:
    """A date is printed on a line that is not a print, fax or as-of header."""
    return any(_ANY_DATE.search(ln) and not _HEADER.search(ln) for ln in text.splitlines())


def modes_for(slug_dir: Path, unit: str, docs: dict[int, Doc]) -> PM.Modes:
    """The page rule over the new exhibits as the writer read them: the page
    index's exhibit-to-file rows built here, since ``page_map.json`` is written
    later, by ``update_collect``. Every page a picture when it cannot open."""
    from .update_review_io import page_counts

    n = page_counts(slug_dir / "out" / unit)
    rows = [
        {
            "exhibit": x,
            "total_pages": n(x),
            "files": [{"file": doc.name, "id": doc.doc_id, "start_page": 1, "pages": n(x)}],
        }
        for x, doc in docs.items()
    ]
    try:
        return PM.Modes(PageIndex(slug_dir, unit, min_chars=PM.TEXT_MIN_CHARS, page_map=rows))
    except Exception:  # noqa: BLE001 - an index that cannot open shows every page as a picture
        return PM.Modes(None)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def prefilter(sw: Sweep, modes: PM.Modes) -> None:
    """Settle, before any request, a trusted text page with no dated line
    (``no_date``) and an exact copy of an uncited page already kept (``duplicate_uncited``)."""
    kept: list[Page] = []
    words: set[str] = set()
    for x, p in sw.open:
        m = modes(x, p)
        if m.kind == PM.TEXT and m.text and not dated_outside_headers(m.text):
            sw.settled[(x, p)] = NO_DATE
            continue
        key = _words(sw.settler.text(x, p)) if sw.settler is not None else ""
        if (len(key) >= DUP_MIN_CHARS and key in words) or _picture_copy(sw, x, p, set(kept)):
            sw.settled[(x, p)] = DUP_UNCITED
            continue
        if len(key) >= DUP_MIN_CHARS:
            words.add(key)
        kept.append((x, p))
    sw.open = kept


def _picture_copy(sw: Sweep, x: int, p: int, kept: set[Page]) -> bool:
    st = sw.settler
    if st is None:
        return False
    hit = (st.ident.pages.get(st.docs[x].doc_id) or {}).get(p)
    other = st.by_id.get(hit.other) if hit is not None and hit.exact else None
    return other is not None and hit is not None and (other, hit.page) in kept


def accounted(d: Path, maps: list[str], docs: dict[int, Doc], held: set[Page]) -> set[Page]:
    """Every page an entry cites or a BILLING-DATES line names, in these maps."""
    got, billing, _ = _scan(d, maps, docs, held)
    return got | billing
