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

* ``status``: ``cited`` | ``cited_via`` (cited, resolved through its cover) |
  ``excluded`` | ``read_uncited`` | ``unread``; ``read`` is the interim
  status ``update_select`` writes for a page sent to compose, which
  ``stages/page_account`` settles to cited or read_uncited.
* ``class``: why, as a short code (``CLASSES``).
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
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .update import pageid_io as PG

NAME = "pages.jsonl"
CITED, CITED_VIA, EXCLUDED, READ_UNCITED, UNREAD, READ = (
    "cited",
    "cited_via",
    "excluded",
    "read_uncited",
    "unread",
    "read",
)
STATUSES = (CITED, CITED_VIA, EXCLUDED, READ_UNCITED, UNREAD, READ)
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


def replace_by(run_dir: Path, by: str, rows: Iterable[dict[str, Any]]) -> int:
    """Replace the rows `by` wrote earlier with `rows`, keeping every other stage's."""
    keep = [r for r in read(run_dir) if r.get("by") != by]
    return write(run_dir, [*keep, *rows])


def counts(run_dir: Path) -> dict[str, dict[str, int]]:
    """{"status": {...}, "class": {...}, "kind": {...}}: what the run-record
    builder attaches to its audit block."""
    rows = read(run_dir)
    return {
        "status": dict(Counter(str(r.get("status")) for r in rows)),
        "class": dict(Counter(str(r.get("class")) for r in rows)),
        "kind": dict(Counter(str((r.get("evidence") or {}).get("kind")) for r in rows)),
    }


def missing(rows: Iterable[dict[str, Any]], pages: dict[str, int]) -> list[tuple[str, int]]:
    """(document, page) of every page of `pages` ({document: page count})
    that has no row: the record's own completeness check."""
    have = {(str(r.get("doc")), int(r.get("page") or 0)) for r in rows}
    return [(d, p) for d, n in sorted(pages.items()) for p in range(1, n + 1) if (d, p) not in have]


#: The statuses of a page sent to be read (``update/charged`` charges exactly these).
READ_STATUSES = frozenset({READ, CITED, READ_UNCITED})


#: The exclusions that name the page covering them (``stages/page_rows.CLASS``).
COVERED_CLASSES = frozenset({"already_delivered", "repeat", "refiled_extract"})
#: A withheld document's row class starts with this (``page_rows.CLASS``).
WITHHELD_PREFIX = "withheld"


def _excluded_covered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("status") in (EXCLUDED, CITED_VIA) and r.get("class") in COVERED_CLASSES]


def uncovered_exclusions(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """RECORD ONLY: pages excluded as a copy that name no covering page:
    {count, docs}. Nothing in the run reads this."""
    bad = [r for r in _excluded_covered(list(rows)) if not (r.get("evidence") or {}).get("cover")]
    return {"count": len(bad), "docs": sorted({str(r["doc"]) for r in bad})}


def invalid_covers(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """RECORD ONLY: pages excluded as a copy whose cover is a document this run
    withheld, could not read, or never pulled: {count, docs}. A cover page code
    itself set aside (billing, blank, an expert report, a re-filed extract, a
    copy of another page) is a VALID cover: the copy is rightly excluded for the
    same reason. Nothing in the run reads this."""
    rows = list(rows)
    by_key = {(str(r.get("doc")), int(r.get("page") or 0)): r for r in rows}
    first: dict[str, dict[str, Any]] = {}
    for r in rows:
        first.setdefault(str(r.get("doc")), r)
    bad: list[dict[str, Any]] = []
    for r in _excluded_covered(rows):
        cov = (r.get("evidence") or {}).get("cover")
        if not isinstance(cov, dict) or not cov.get("doc"):
            continue  # a delivered exhibit or a byte-identical file covers it
        doc = str(cov["doc"])
        hit = by_key.get((doc, int(cov.get("page") or 0))) or first.get(doc)
        if hit is None or str(hit.get("class") or "").startswith(WITHHELD_PREFIX) or hit.get("status") == UNREAD:
            bad.append(r)
    return {"count": len(bad), "docs": sorted({str(r["doc"]) for r in bad})}


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
