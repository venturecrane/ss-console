"""A new document that is a re-filed copy or extract of another document. $0.

The firm can file a record and, beside it, extracts of it: two new documents
were page-for-page copies of a third, their printed footers numbering page N
where the whole one numbered N+280 (2026-10-13 review, fix 17). Read as three
exhibits, every visit was composed two or three times. An extract is not an
exhibit: the whole document is, and every visit the extract holds is cited
there. ``update_select`` records each extract (``selection.json``
"subsets") so Records Reviewed names it as a re-filed extract.

An extract is known only by its PICTURES (``update/pageid``: every inked page
pixel-equal to a page that was read). The words-side rule that once found an
extract by its text was removed on 2026-11-22: a text test set aside pages
that differ from their match, so a document that is not pixel-identical page
by page is read, each page with its suspected match recorded
(``update/select``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Subset:
    doc: str
    parent: str
    pages: int
    matched: int
