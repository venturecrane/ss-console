"""Which pages of the delivered exhibits are no keeper. $0.

A new page pixel-equal to a delivered page is set aside only when that
delivered page's facts are in the chronology the firm holds (``update/pageid``).
The one durable record of that is the delivered chronology itself
(``runs/<unit>/update/base.md``, pulled by ``update_base``): a delivered page
it CITES was read and written down. A page it never cites may have been set
aside by a past update (a suspected or pixel-equal copy, a blank or billing
page), or read and found to say nothing citable; either way a copy of it is
no evidence its facts reached the firm, so the copy is read, as a suspected
copy, and not charged (2026-11-22: on the largest saved update, 75 pages'
facts never reached a delivered update).

Why not the past update's own record of what it read: that record lives in
the delivered job's directory, which is wiped 72 hours after delivery, so
in practice it was never there to read (bug review, 2026-11-22).
"""

from __future__ import annotations

from .encounter import cited_pages


def cited(delivered_md: str) -> dict[int, set[int]]:
    """{delivered exhibit: the pages the delivered chronology cites}."""
    out: dict[int, set[int]] = {}
    for ex, p in cited_pages(delivered_md or ""):
        out.setdefault(ex, set()).add(p)
    return out


def uncited_pages(delivered_md: str, pages: dict[int, int]) -> dict[int, set[int]]:
    """{delivered exhibit: its pages the delivered chronology never cites}, for
    `pages` {delivered exhibit: its page count}."""
    have = cited(delivered_md)
    out = {n: set(range(1, k + 1)) - have.get(n, set()) for n, k in pages.items()}
    return {n: v for n, v in out.items() if v}
