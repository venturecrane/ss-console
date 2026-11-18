"""An added entry cites the page that was READ, never a copy of it. $0.

WHY (2026-11-18, a real update). 21 added entries cited a COPY of a page:
the same page filed in a second new document, excluded from reading there
as a repeat (``select.OVERLAP``, a picture twin, ``update/twins``) while its
twin was read in the first. 9 more cited pages a delivered exhibit already
holds (``select.ALREADY``).

HOW A CITATION NAMES A PAGE THE WRITER WAS NOT SENT. Read in the code, no
step renumbers a page: ``collect.renumber`` maps exhibit numbers only, and
``collect.compact`` remaps them one for one. Two steps put an excluded page
number in front of a model or into a citation:

1. The writer is SHOWN excluded pages by number. ``select.selected_text``
   (``select.py:540-541``) puts the foot of an excluded page before a read
   page, the head of the next one after it, and the heading of the note an
   excluded page opened (``noteheading.heading_block``), each labelled with
   the excluded page's number ("p.N"). The prompt says never to cite them
   (``prompts/map-system.md`` PAGE CONTEXT, NOTE HEADING); a citation of one
   is the writer naming the page a fact is printed on, and that page's text
   is the read twin's. This module handles that case, here, before the
   exhibits are compacted.
2. The AUDIT widens a citation it cannot support on the cited pages to the
   pages either side (``audit/run.py:131``, image path; ``audit/verify.py``
   ``text_verdict``), and repair rewrites the citation to that window
   (``audit/repair.py:538-551``). In an update a read page's neighbours are
   often excluded copies or delivered pages, so a widened citation names
   them. That runs after this stage, in ``medchron/audit/``, and this module
   does not reach it.

The rule: a citation of a new exhibit EVERY page of which is an excluded
page whose recorded cover (``runs/<unit>/pages.jsonl``, ``evidence.cover``,
followed through a cover that is itself excluded) is a READ page of ONE new
exhibit is rewritten to name those read pages, and counted. A citation that
names a page a delivered exhibit holds is counted only. A citation that mixes
read pages with moved ones is left as written and counted: splitting it
would make a second citation the audit grades nothing against
(``audit/claims._walk`` grades the text a citation closes). Nothing else of
any entry changes, and the delivered chronology is never an input.
"""

from __future__ import annotations

import re
from typing import Any

from .. import pages_record as PR
from ..audit import claims as CL
from ..audit.repair import compress
from . import body as B

MOVED, DELIVERED, MIXED = "citations_moved_to_read_page", "citations_to_delivered_pages", "citations_mixed_left"
_READ = (PR.READ, PR.CITED, PR.READ_UNCITED)
#: Covers followed before a page counts as reaching nothing read.
MAX_HOPS = 4


class _Covers:
    """Where each excluded page of a new document leads: a read page of a new exhibit, or a delivered exhibit."""

    def __init__(self, rows: list[dict[str, Any]], exhibit_of: dict[str, int]) -> None:
        self.exhibit_of = exhibit_of
        self.doc_of = {n: d for d, n in exhibit_of.items()}
        self.rows = {(str(r.get("doc")), int(r.get("page") or 0)): r for r in rows}

    def lead(self, ex: int, page: int) -> tuple[str, int, int] | None:
        """("read", exhibit, page) or ("delivered", exhibit, page) for an
        excluded page of new exhibit `ex`; None for a page read here, or one
        whose cover reaches neither."""
        doc = self.doc_of.get(ex)
        for hop in range(MAX_HOPS + 1):
            r = self.rows.get((doc, page)) if doc is not None else None
            if r is None:
                return None
            if r.get("status") in _READ:  # the cited page itself (hop 0) is read here: nothing moves
                return ("read", self.exhibit_of[str(doc)], page) if hop else None
            cov = (r.get("evidence") or {}).get("cover") or {}
            if r.get("status") != PR.EXCLUDED or not isinstance(cov.get("page"), int):
                return None
            if isinstance(cov.get("exhibit"), int):
                return ("delivered", int(cov["exhibit"]), int(cov["page"]))
            nxt = str(cov.get("doc") or "")
            doc, page = (nxt, int(cov["page"])) if nxt in self.exhibit_of else (None, 0)
        return None


def to_read_pages(
    entries: list[str], rows: list[dict[str, Any]], exhibit_of: dict[str, int]
) -> tuple[list[str], dict[str, int]]:
    """(entries with each citation of copies rewritten to the read pages,
    {MOVED, DELIVERED, MIXED: count of citations}). `rows`: the update's page
    record; `exhibit_of`: {document id: its new exhibit number as composed}."""
    covers = _Covers(rows, exhibit_of)
    counts = {MOVED: 0, DELIVERED: 0, MIXED: 0}

    def one(m: re.Match[str]) -> str:
        ex = int(m.group(1))
        pages = CL.parse_pages(m.group(2))
        leads = [covers.lead(ex, p) for p in pages]
        if any(x is not None and x[0] == "delivered" for x in leads):
            counts[DELIVERED] += 1
            return m.group(0)
        moved = [x for x in leads if x is not None]
        if not moved:
            return m.group(0)
        targets = {x[1] for x in moved}
        if len(moved) < len(leads) or len(targets) != 1:
            counts[MIXED] += 1
            return m.group(0)
        counts[MOVED] += 1
        return f"(Exhibit {targets.pop()} - p. {compress(sorted({x[2] for x in moved}))})"

    out = [B.CITE.sub(one, e) for e in entries]
    return out, counts
