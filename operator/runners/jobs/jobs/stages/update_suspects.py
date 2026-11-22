"""What an update does with the pages a rule only SUSPECTS are copies. $0.

Split out of ``update_select`` (2026-11-22), which decides every page by the
rules of ``update/select`` and then calls ``read_suspects``: only a pixel-equal
page stays set aside, every other page a rule calls a copy is READ, recorded
with the page it may copy and not charged (``update/charged``). ``fill`` and
``_read_for_itself`` answer the one pixel-equal page that has no reading
elsewhere: a copy of a page of a document that is never read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..medchron.update import medlegal as ML, pageid as PI, pageid_io as PG, select as S, twins as TW
from ..medchron.update.billing_page import BILLING, is_billing
from ..medchron.update.locate import vendor_name
from .base import StageRun


def texts_of(sr: StageRun, rid: str) -> dict[int, str]:
    tp = sr.slug_dir / "text" / f"{rid}.txt"
    return S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")


def settle_twins(got: dict[str, Any], ident: PG.Loaded) -> None:
    """Of a page two new documents both still read, picture-paired (or at
    its neighbours' offset), the reading that adds nothing is not read
    (``update/twins``). A suspicion only: the rule answers on copies of the
    readings, and no transcript changes."""
    texts = {rid: dict(c.pages) for rid, c in got.items()}
    for rid, c in got.items():
        by_other: dict[str, dict[int, int]] = {}
        for pg, h in (ident.pages.get(rid) or {}).items():
            if h.other in got and not got[h.other].medlegal and not c.medlegal:
                by_other.setdefault(h.other, {})[pg] = h.page
        for other, pairs in by_other.items():
            o = got[other]
            mine, theirs = {}, {}  # each page a twin excluded, and the page it repeats
            TW.settle((texts[rid], c.decisions), (texts[other], o.decisions), pairs, mine, theirs)
            c.covers.update({q: S.Cover(S.EARLIER_SRC, other, p, S.BY_TWIN) for q, p in mine.items()})
            o.covers.update({p: S.Cover(S.EARLIER_SRC, rid, q, S.BY_TWIN) for p, q in theirs.items()})


def read_suspects(rid: str, got: dict[str, Any], ident: PG.Loaded) -> None:
    """The rules' answers become the selection: every page they call a copy
    is READ as a suspected one (``select.SUSPECTED``), except a page
    pixel-equal to a page that is itself read (or set aside the same way).
    Called in reading order, so the page it equals is settled first."""
    c, found = got[rid], ident.pages.get(rid) or {}
    if c.medlegal:
        return
    for pg, (d, why) in list(c.decisions.items()):
        h = found.get(pg)
        if d == S.EXCLUDED and why in (S.ALREADY, S.OVERLAP):
            if h is not None and h.aside:
                if _never_read(h, got):
                    _read_for_itself(c, pg, h, ident.tested)
                continue
        elif d != S.READ or h is None or h.aside:
            continue
        c.decisions[pg] = (S.READ, S.SUSPECTED)
        if h is not None:  # the picture names the page itself, whatever else matched
            c.covers[pg] = picture(h)


def picture(h: PG.Page) -> S.Cover:
    src = S.DELIVERED_SRC if PG.delivered_exhibit(h.other) is not None else S.EARLIER_SRC
    return S.Cover(src, h.other, h.page, S.BY_PICTURE)


def _never_read(h: PG.Page, got: dict[str, Any]) -> bool:
    """The new document's page `h` names is read nowhere: an expert's report,
    or a page whose transcription failed."""
    o = got.get(h.other)
    return o is not None and (o.medlegal or o.decisions.get(h.page, ("",))[0] == S.UNREAD)


def _read_for_itself(c: Any, pg: int, h: PG.Page, tested: bool) -> None:
    """A page pixel-equal to one that is never read, decided on its own text
    (``fill`` gave a skipped page the text of the page it equals). With no
    text it is unread and the stage holds; a record from before this question
    was asked (`tested` False) skipped it on the old rule, which then stands."""
    t = (c.pages.get(pg) or "").strip()
    if t in S.FAILED_MARKS:
        if tested:
            c.decisions[pg] = (S.UNREAD, "the page it equals is never read, and it was not transcribed")
        return
    if len(t) < S.MIN_CHARS or is_billing(t):
        c.decisions[pg] = (S.EXCLUDED, S.BLANK if len(t) < S.MIN_CHARS else BILLING)
        return
    c.decisions[pg] = (S.READ, S.SUSPECTED)
    c.covers[pg] = replace(picture(h), exact=True)


def fill(
    sr: StageRun, rows: list[dict[str, Any]], ident: PG.Loaded, firm_of: dict[str, str]
) -> tuple[PG.Loaded, list[dict[str, Any]]]:
    """An expert's report known only by its transcribed first page stood for
    the pixel-equal pages of other documents, so the vision pass skipped
    those. The report is never composed, so each such page is given the
    report's reading of the same pixels, in its own transcript (compose, the
    audit and every later check read it there), and an extract of the report
    is decided page by page. In reading order, so a copy whose first page is
    the report's own is an expert's report too. Returns the picture record as
    it now stands and one row per page filled."""
    out: list[dict[str, Any]] = []
    by_id, never = {str(r["id"]): r for r in rows}, set()
    for rid in PI.reading_order([(d, len(texts_of(sr, d))) for d in by_id]):
        pages = texts_of(sr, rid)
        ident = PG.reopened(ident, never)
        at = {
            p: h
            for p, h in (ident.pages.get(rid) or {}).items()
            if h.exact and h.other in never and (pages.get(p) or "").strip() in S.FAILED_MARKS
        }
        text = {p: texts_of(sr, h.other).get(h.page) or "" for p, h in at.items()}
        text = {p: t for p, t in text.items() if t.strip() and t.strip() not in S.FAILED_MARKS}
        if text:
            tp = sr.slug_dir / "text" / f"{rid}.txt"
            body = _with_pages(tp.read_text(encoding="utf-8", errors="replace"), text, set(text))
            tp.write_text(body, encoding="utf-8")
            out += [{"doc": rid, "page": p, "from_doc": at[p].other, "from_page": at[p].page} for p in sorted(text)]
        if ML.is_medlegal([vendor_name(by_id[rid]), firm_of.get(rid, "")], texts_of(sr, rid).get(1) or ""):
            never.add(rid)
    return ident, out


def _with_pages(text: str, pages: dict[int, str], which: set[int]) -> str:
    """`text` (a page-marked transcript) with the bodies of the pages in
    `which` replaced by `pages`."""
    marks = list(S.PAGE_MARK.finditer(text))
    out, at = [], 0
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        n = int(m.group(1))
        if n in which:
            out += [text[at : m.end()], "\n" + pages[n].strip("\n") + "\n"]
            at = end
    return "".join([*out, text[at:]])
