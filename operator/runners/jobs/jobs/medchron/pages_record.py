"""Every page that leaves a run carries its evidence: ``runs/<unit>/pages.jsonl``. $0.

WHY (2026-11-17). On a real 4,997-page update, 349 pages were cited, 4,411
excluded by a rule, 34 withheld as another person's and 203 read but never
explained, and both known misses carried a recorded REASON nobody tested: a
file excluded by its name whose pages 2 to 4 were a signed clinical report,
and a file withheld as another person's when 24 of its 34 pages were
picture-identical to the same client's delivered exhibit. A reason is a
word; this record holds the evidence behind it, page by page.

ONE ROW PER PAGE of every document the update pulled:

    {doc, sha256, page, status, class, evidence: {kind, cover, frac, headings}, by}

* ``status``: ``read`` (sent to a model to be read) | ``excluded`` |
  ``unread`` (not read: not a page source, or its text could not be had). ``update_pages`` writes the first rows
  and ``update_select`` replaces them; ``update/charged`` charges the
  ``read`` rows.
* ``class``: why, as a short code (``stages/page_rows.CLASS``).
* ``evidence.kind``: what proves it (``KINDS``). ``cover`` names the page
  that covers an excluded page: ``{exhibit|doc, page, exact}`` (or
  ``{sha256}`` for a byte-identical file). An identity finding stays in
  ``update/identity_holds.json``; the row holds only a pointer to it.
* ``by``: the stage that wrote the row.

The file lives in the job directory and is wiped with it (72 h); nothing
here outlives the job.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .update import pageid_io as PG

NAME = "pages.jsonl"
EXCLUDED, UNREAD, READ = "excluded", "unread", "read"
STATUSES = (EXCLUDED, UNREAD, READ)
#: What proves a row. ``file``: a byte-identical or re-filed document;
#: ``rule``: a document-level rule (not a page source, password-protected,
#: an expert report, read by an earlier delivery, the firm's direction).
PICTURE, TEXT, CONTAINED, IDENTITY, BILLING_WORDS, NONRECORD, BLANK, FILE, RULE, NONE = (
    "picture",
    "text",
    "contained",
    "identity",
    "billing_words",
    "nonrecord",
    "blank",
    "file",
    "rule",
    "none",
)
#: A picture match that is EXACTLY a page of a delivered exhibit
#: (``own_delivered``): the other-person check reads such a page as the
#: client's own. KNOWN EDGE: a delivered exhibit is the client's by
#: definition, so an exact copy of another person's page that was already
#: wrongly delivered passes that check too. The row names the covering
#: exhibit and page, so that case is visible here; nothing decides it.
OWN_DELIVERED = "own_delivered"
KINDS = (PICTURE, OWN_DELIVERED, TEXT, CONTAINED, IDENTITY, BILLING_WORDS, NONRECORD, BLANK, FILE, RULE, NONE)
#: The identity record a withheld row points to (it never copies a finding).
IDENTITY_HOLDS = "update/identity_holds.json"


def path(run_dir: Path) -> Path:
    return run_dir / NAME


def row(
    doc: str,
    sha256: str,
    page: int,
    status: str,
    klass: str,
    kind: str,
    by: str,
    cover: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One page's row. `extra` lands in ``evidence`` (frac, headings, pointer)."""
    if status not in STATUSES or kind not in KINDS:
        raise ValueError(f"a page row needs a known status and evidence kind, not {status!r} / {kind!r}")
    ev: dict[str, Any] = {"kind": kind, "cover": cover, "frac": None, "headings": []}
    ev.update(extra)
    return {"doc": doc, "sha256": sha256, "page": page, "status": status, "class": klass, "evidence": ev, "by": by}


def read(run_dir: Path) -> list[dict[str, Any]]:
    """Every row; a malformed line is skipped (the record is an addition, never a precondition)."""
    p = path(run_dir)
    out: list[dict[str, Any]] = []
    if not p.is_file():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and "doc" in r and "page" in r:
            out.append(r)
    return out


def write(run_dir: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Replace the record with `rows`, sorted by document and page; returns the count."""
    rows = sorted(rows, key=lambda r: (str(r["doc"]), int(r.get("page") or 0)))
    run_dir.mkdir(parents=True, exist_ok=True)
    path(run_dir).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


#: The statuses of a page sent to be read (``update/charged`` charges exactly these).
READ_STATUSES = frozenset({READ})


def own_delivered(ident: PG.Loaded, rid: str, page: int) -> bool:
    """Whether page `page` of new document `rid` is, by its picture, EXACTLY
    a page of a DELIVERED exhibit of this client's own (``pageid_io``). Only
    that: a near match, or a match to another new document's page, proves
    nothing about whose record it is. The one rule the other-person check
    and this record both read (``update_select._delivered_pages``)."""
    h = (ident.pages.get(rid) or {}).get(page)
    return h is not None and h.exact and PG.delivered_exhibit(h.other) is not None


def picture_kind(h: PG.Page) -> str:
    """``OWN_DELIVERED`` for an exact match to a delivered exhibit's page, else ``PICTURE``."""
    return OWN_DELIVERED if h.exact and PG.delivered_exhibit(h.other) is not None else PICTURE


def picture_cover(h: PG.Page) -> dict[str, Any]:
    n = PG.delivered_exhibit(h.other)
    where: dict[str, Any] = {"exhibit": n} if n is not None else {"doc": h.other}
    return {**where, "page": h.page, "exact": h.exact}
