"""Page identity by PICTURE, before the vision pass. $0 (rendering only).

The text rules (``select``, ``subsets``) compare words, so they are blind
where there are no words: a delivered page that is a scan has no text layer
and added nothing to the delivery's coverage, and a document with a page the
transcription could not read was never an extract. A real update filed five
scanned copies of pages of other documents and a re-file of a delivered
exhibit as new exhibits, and composed the same visits two and three times
(2026-10-14). Each page is now known by its picture first.

* EXACT: the page rendered at EXACT_DPI in grayscale is pixel-for-pixel the
  same image (sha256 of the pixels). Same pixels, same page, whatever it
  says: such a page is never transcribed.
* NEAR: a re-encoded copy of one scan (a heavier JPEG) is never pixel-equal.
  Its 256-bit difference hash (HASH_DPI, grayscale, 16x16 gradients) is
  within HASH_MAX of ONE page of the other document, every other page there
  (pixel-equal duplicates of that one aside) is at least MARGIN further, and
  a NEIGHBOUR pairs the same way with the neighbouring page. A picture cannot
  tell a re-encoded copy from the same form filled in with one other number,
  so a NEAR page IS transcribed and is a repeat only when its words confirm
  it (``pageid_io.confirmed``: the same dates and the same clinical values).
* A blank page (almost no ink) is never matched: every blank page looks alike.
* Pages of one document are never compared with each other: a run of
  template visits inside one record is read whole (review fix 1).

A new page pixel-equal to a delivered page is ALREADY read; one pixel-equal
to a page of a new document READ EARLIER (``reading_order``: the larger
first) is a repeat. A new document whose EVERY
inked page is pixel-equal to a page of one other document (a delivered
exhibit, or a LARGER new document) or of a delivered exhibit, at least
EXTRACT_FRAC of them to that one document, is a re-filed copy or extract: not
an exhibit, none of its pages transcribed, named in Records Reviewed and cited
to that document.

Calibration, on the real files of that update read only (numbers in the PR):
re-encoded copies of one scan sat 2 to 25 bits apart of 256, the next
different-looking page of the same record 43 or more; pages that looked alike
at 16x16 but said other words sat as close as 0 to 4, which is why a near
match needs uniqueness, a neighbour and its words.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

#: What the vision pass records for a page it skipped because of this module.
#: ``select`` reads it as unread unless this module's record accounts for the
#: page, so a skipped page can never pass as blank.
SKIPPED = "[not transcribed: the same page image is read elsewhere]"
HASH_DPI = 24
EXACT_DPI = 72
HASH_MAX = 28
MARGIN = 16
EXTRACT_FRAC = 0.9
INK_LEVEL = 128
MIN_INK = 0.002  # fraction of dark pixels below which a page is blank
DELIVERED = "exhibit:"
ALREADY, REPEAT = "already", "repeat"


@dataclass(frozen=True)
class Sig:
    """One page's picture: its 256-bit difference hash, the sha256 of its
    pixels, and whether it carries ink."""

    dhash: int
    exact: str
    inked: bool


def _dhash(gray: bytes, w: int, h: int) -> int:
    from PIL import Image

    px = Image.frombytes("L", (w, h), gray).resize((17, 16), Image.Resampling.BOX).tobytes()
    bits = 0
    for y in range(16):
        row = px[y * 17 : y * 17 + 17]
        for x in range(16):
            bits = (bits << 1) | (row[x + 1] > row[x])
    return bits


def signatures(path: Path) -> list[Sig]:
    """Every page's Sig, in page order (a PDF, or an image opened as one)."""
    import pymupdf

    out: list[Sig] = []
    with pymupdf.open(str(path)) as doc:
        for pg in doc:
            fine = pg.get_pixmap(dpi=EXACT_DPI, colorspace=pymupdf.csGRAY, alpha=False)
            coarse = pg.get_pixmap(dpi=HASH_DPI, colorspace=pymupdf.csGRAY, alpha=False)
            samples = bytes(fine.samples)
            step = max(1, len(samples) // 20000)
            sampled = samples[::step]
            inked = sum(1 for b in sampled if b < INK_LEVEL) >= MIN_INK * len(sampled)
            exact = hashlib.sha256(f"{fine.width}x{fine.height}:".encode() + samples).hexdigest()
            out.append(Sig(_dhash(bytes(coarse.samples), coarse.width, coarse.height), exact, inked))
    return out


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _nearest(s: Sig, theirs: list[Sig], exact: dict[str, int]) -> int | None:
    """The one page of `theirs` that `s` is a copy of (index), or None. A
    near match is unique only when every other page within MARGIN of it is a
    pixel-equal duplicate of the matched page itself."""
    if s.exact in exact:
        return exact[s.exact]
    near = sorted((distance(s.dhash, o.dhash), j) for j, o in enumerate(theirs) if o.inked)
    if not near or near[0][0] > HASH_MAX:
        return None
    d1, j1 = near[0]
    for d, j in near[1:]:
        if d - d1 >= MARGIN:
            break
        if theirs[j].exact != theirs[j1].exact:
            return None  # another page is about as close: no unique match
    return j1


def pair_pages(mine: list[Sig], theirs: list[Sig]) -> dict[int, tuple[int, bool]]:
    """{my page: (their page, pixel-equal?)} (1-based) for each inked page of
    `mine` that is a page of `theirs`: pixel-equal, or a unique near match
    whose neighbour pairs with the neighbouring page."""
    exact: dict[str, int] = {}
    for j, o in enumerate(theirs):
        if o.inked:
            exact.setdefault(o.exact, j)
    near = {i: _nearest(s, theirs, exact) if s.inked else None for i, s in enumerate(mine)}
    out: dict[int, tuple[int, bool]] = {}
    for i, j in near.items():
        if j is None:
            continue
        same = mine[i].exact == theirs[j].exact
        if same or any(near.get(i + d) == j + d for d in (-1, 1)):
            out[i + 1] = (j + 1, same)
    return out


@dataclass(frozen=True)
class Extract:
    doc: str
    parent: str  # a new document's id, or "" for a delivered exhibit
    exhibit: int | None  # the delivered exhibit, when the parent is one
    pages: int
    matched: int


@dataclass(frozen=True)
class Hit:
    """A new page's picture found elsewhere: ALREADY (a delivered page) or
    REPEAT (a page of an earlier new document); where ("exhibit:N" or a
    document id), on which page, and whether the pixels are equal."""

    reason: str
    other: str
    page: int
    exact: bool


@dataclass
class Identity:
    """What the pictures settle: per new document, {page: Hit}, and the
    documents that are re-filed copies or extracts."""

    pages: dict[str, dict[int, Hit]] = field(default_factory=dict)
    extracts: dict[str, Extract] = field(default_factory=dict)

    def skipped(self, npages: dict[str, int]) -> dict[str, list[int]]:
        """{document: pages} the vision pass never transcribes: every
        pixel-equal page, and every page of a re-filed copy. A NEAR page is
        transcribed: its words must confirm it."""
        out = {d: sorted(p for p, h in m.items() if h.exact) for d, m in self.pages.items()}
        out |= {d: list(range(1, npages.get(d, 0) + 1)) for d in self.extracts}
        return {d: v for d, v in out.items() if v}


_Pairs = dict[str, dict[int, tuple[int, bool]]]


def reading_order(docs: list[tuple[str, int]]) -> list[str]:
    """The order the new documents are READ in: most pages first, pull order
    among equals. A page two new documents share is read in the one read
    first and is a repeat in the other, so the LARGER copy keeps it: a
    124-page file that was pages 281 to 404 of a 404-page file was pulled
    first, kept every shared page, and the larger file's copies became
    repeats, so one record was filed as two exhibits and a visit was
    itemized twice (2026-10-14). Exhibit numbers stay in pull order."""
    return [d for _, _, d in sorted((-n, i, d) for i, (d, n) in enumerate(docs))]


def identify(
    delivered: list[tuple[int, list[Sig]]], new: list[tuple[str, list[Sig]]], firm: set[str] | None = None
) -> Identity:
    """Page identity across the delivered exhibits (exhibit number, sigs) and
    the new documents (id, sigs) in pull order. A firm-numbered file
    (`firm`) is never an extract: the firm holds it under its own number."""
    pairs = {d: _pairs_of(d, sigs, delivered, new) for d, sigs in new}
    out = Identity()
    sizes = {d: len(s) for d, s in new}
    for d, sigs in new:
        ex = None if d in (firm or set()) else _extract(d, sigs, pairs[d], sizes)
        if ex is not None:
            out.extracts[d] = ex
    _resolve(out.extracts)
    order = reading_order([(d, len(s)) for d, s in new])
    for d, sigs in new:
        if d not in out.extracts:
            out.pages[d] = _page_hits(d, len(sigs), pairs[d], order, out.extracts)
    return out


def _pairs_of(
    d: str, sigs: list[Sig], delivered: list[tuple[int, list[Sig]]], new: list[tuple[str, list[Sig]]]
) -> _Pairs:
    """{other: {my page: (its page, exact)}}; delivered exhibits keyed "exhibit:N"."""
    got = {f"{DELIVERED}{n}": pair_pages(sigs, s) for n, s in delivered}
    got |= {o: pair_pages(sigs, s) for o, s in new if o != d}
    return {k: v for k, v in got.items() if v}


def _extract(d: str, sigs: list[Sig], pairs: _Pairs, sizes: dict[str, int]) -> Extract | None:
    """A re-filed copy or extract, by pixel equality only (a page known by a
    near match is transcribed and confirmed by its words, so its document is
    read)."""
    inked = {i for i, s in enumerate(sigs, 1) if s.inked}
    exact = {o: {p for p, (_, e) in m.items() if e} for o, m in pairs.items()}
    delivered: set[int] = set().union(*(m for o, m in exact.items() if o.startswith(DELIVERED)))
    best: tuple[int, str] | None = None
    for o, m in exact.items():
        bigger = o.startswith(DELIVERED) or sizes.get(o, 0) > len(sigs)
        if not bigger or len(m) < EXTRACT_FRAC * len(inked) or not inked <= m | delivered:
            continue  # a page neither this document nor the delivery holds: the document is read
        if best is None or len(m) > best[0]:
            best = (len(m), o)
    if best is None:
        return None
    n, o = best
    if o.startswith(DELIVERED):
        return Extract(d, "", int(o[len(DELIVERED) :]), len(sigs), n)
    return Extract(d, o, None, len(sigs), n)


def _resolve(found: dict[str, Extract]) -> None:
    """An extract of an extract is cited to the outermost document."""
    for d, ex in list(found.items()):
        cur, seen = ex, {d}
        while cur.parent in found and cur.parent not in seen:  # pages strictly rise along a chain
            seen.add(cur.parent)
            cur = found[cur.parent]
        found[d] = Extract(d, cur.parent, cur.exhibit, ex.pages, ex.matched)


def _page_hits(d: str, npages: int, pairs: _Pairs, order: list[str], extracts: dict[str, Extract]) -> dict[int, Hit]:
    """Per page, the best hit: pixel-equal before near, a delivered page
    before a new one read earlier (``reading_order``). Never a page of this
    document, of one read later, or of a re-filed copy (it is not read)."""
    earlier = [o for o in order[: order.index(d)] if o not in extracts]
    sources = [o for o in pairs if o.startswith(DELIVERED)] + [o for o in earlier if o in pairs]
    out: dict[int, Hit] = {}
    for p in range(1, npages + 1):
        found = [(not pairs[o][p][1], i, o) for i, o in enumerate(sources) if p in pairs[o]]
        if found:
            o = min(found)[2]
            q, exact = pairs[o][p]
            out[p] = Hit(ALREADY if o.startswith(DELIVERED) else REPEAT, o, q, exact)
    return out


def to_json(ident: Identity) -> dict:
    return {
        "pages": {
            d: {str(p): [h.reason, h.other, h.page, "exact" if h.exact else "near"] for p, h in m.items()}
            for d, m in ident.pages.items()
        },
        "extracts": {
            d: {"parent": e.parent, "exhibit": e.exhibit, "pages": e.pages, "matched": e.matched}
            for d, e in ident.extracts.items()
        },
        "hash_max": HASH_MAX,
        "margin": MARGIN,
    }
