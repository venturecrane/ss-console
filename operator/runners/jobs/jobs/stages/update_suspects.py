"""What an update does with the pages a rule only SUSPECTS are copies. $0.

Split out of ``update_select`` (2026-11-22), which decides every page by the
rules of ``update/select`` and then calls ``read_suspects``: a pixel-equal
page stays set aside exactly as the rules left it, and every other page they
set aside as a copy is READ, recorded with the page it may copy and not
charged (``update/charged``). A page the rules read is untouched: it is read
and charged as before.
"""

from __future__ import annotations

from typing import Any

from ..medchron.update import pageid_io as PG, select as S, twins as TW


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


def read_suspects(c: Any, found: dict[int, PG.Page]) -> None:
    """The rules' answers for one document (`c`, its ``update_select._Read``)
    become the selection: every page they set aside as a copy is READ as a
    suspected one, except a pixel-equal page (`found`: its picture matches)."""
    for pg, (d, why) in list(c.decisions.items()):
        h = found.get(pg)
        if d == S.EXCLUDED and why in S.SUSPECTED and not (h is not None and h.exact):
            c.decisions[pg] = (S.READ, S.SUSPECTED[why])


def picture(h: PG.Page) -> S.Cover:
    src = S.DELIVERED_SRC if PG.delivered_exhibit(h.other) is not None else S.EARLIER_SRC
    return S.Cover(src, h.other, h.page, S.BY_PICTURE)
