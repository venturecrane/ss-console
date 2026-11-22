"""The page record's rows as an update's selection decided them. $0.

``update_pages`` writes a row per page of every candidate first (its picture
finding, and whether the page carries ink); ``update_select`` then writes
every page of every pulled document (``rows``), each with the evidence its
reason rests on (``medchron/pages_record``): the page that covers an
excluded page (by picture: the only rule that sets a page aside as a copy),
the page a READ page is suspected of copying and the rule that suspected it
(``evidence.suspected``: such a page is not charged, ``update/charged``), the
blank or billing words, or the document-level rule and, for a document
withheld as another person's, a pointer to ``update/identity_holds.json``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import unopened
from ..medchron import pages_record as PR
from ..medchron.gates import foreign_identity as FI
from ..medchron.update import medlegal as ML, outcome as UO, pageid_io as PG, select as S
from ..medchron.update.billing_page import BILLING
from ..medchron.update.subsets import Subset
from . import prior_reading

BY_PAGES, BY_SELECT = "update_pages", "update_select"
BYTE_IDENTICAL = "byte-identical to a delivered exhibit"
NOT_A_SOURCE = "not a PDF or an image"
#: {reason: class code}; a reason not named here is slugged.
CLASS = {
    S.ALREADY: "already_delivered",
    S.OVERLAP: "repeat",
    S.BLANK: "blank",
    BILLING: "billing",
    ML.REASON: "expert_report",
    BYTE_IDENTICAL: "byte_identical",
    NOT_A_SOURCE: "not_a_page_source",
    unopened.REASON: "password_protected",
    S.SUSPECTED: "suspected_copy",
    UO.IMAGE_UNREAD: "image_unread",
    FI.WITHHELD_REASON: "withheld_firm_direction",
    FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]: "withheld_other_person",
    FI.RULE_REASONS[FI.RULE_UNPLACED]: "withheld_unplaced",
}


def klass(reason: str) -> str:
    if reason.startswith(prior_reading.PREFIX):
        return "read_by_earlier_delivery"
    return CLASS.get(reason) or re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")[:60]


#: The rule that suspected a READ page of copying another (``evidence.suspected``).
NEAR_PICTURE, TEXT_OVERLAP, RUN_OF_PAGES = "near_picture", "text_overlap", "run_of_pages"


def suspicion(c: S.Cover) -> tuple[str, str]:
    """(the rule that suspected the page, its evidence kind)."""
    if c.kind == S.BY_PICTURE:
        return NEAR_PICTURE, PR.PICTURE
    return (RUN_OF_PAGES, PR.CONTAINED) if c.kind == S.BY_RUN else (TEXT_OVERLAP, PR.TEXT)


def _kind(reason: str) -> str:
    if reason == BYTE_IDENTICAL:
        return PR.FILE
    if reason == FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]:
        return PR.IDENTITY
    return PR.BLANK if reason == S.BLANK else PR.BILLING_WORDS if reason == BILLING else PR.RULE


def candidate_rows(
    new: list[tuple[str, int, list[bool]]],
    ident: PG.Loaded,
    sha: dict[str, str],
    unrendered: dict[str, int] | None = None,
) -> list[dict]:
    """``update_pages``'s rows: [(document id, pages, each page inked?)], and
    `unrendered` {id: pages} for a candidate whose pages could not be drawn.

    These rows are what the page question before the first paid step counts
    (``update/charged``), so they are an UPPER BOUND on what will be charged:
    a page is set aside here only when it is CERTAINLY never sent to a
    model, an exact picture match (the vision pass never transcribes it) or a
    page of an exact re-filed copy. A near picture match is read as a
    suspected copy: its row is ``read`` with its match under
    ``evidence.near`` and ``evidence.suspected``, so the page question does
    not count it (it is never charged). Every page of a file that could not
    be drawn counts as to be read."""
    out = []
    for rid, n, inked in new:
        found = ident.pages.get(rid) or {}
        whole = ident.extracts.get(rid)
        for p in range(1, n + 1):
            ink = inked[p - 1] if p - 1 < len(inked) else None
            h = found.get(p)
            if whole is not None:
                n_ex = PG.delivered_exhibit(whole.parent)
                cover = {"exhibit": n_ex} if n_ex is not None else {"doc": whole.parent}
                out.append(
                    PR.row(
                        rid,
                        sha.get(rid, ""),
                        p,
                        PR.EXCLUDED,
                        "refiled_extract",
                        PR.PICTURE,
                        BY_PAGES,
                        {**cover, "page": None, "exact": True},
                        inked=ink,
                    )  # fmt: skip
                )
            elif h is not None and h.aside:
                out.append(
                    PR.row(
                        rid,
                        sha.get(rid, ""),
                        p,
                        PR.EXCLUDED,
                        klass(h.reason),
                        PR.picture_kind(h),
                        BY_PAGES,
                        PR.picture_cover(h),
                        inked=ink,
                    )  # fmt: skip
                )
            else:  # no match, or one that only suspects a copy: to be read
                ev = {"near": PR.picture_cover(h), PR.SUSPECTED: NEAR_PICTURE} if h is not None else {}
                out.append(PR.row(rid, sha.get(rid, ""), p, PR.READ, "candidate", PR.NONE, BY_PAGES, inked=ink, **ev))
    for rid, n in (unrendered or {}).items():
        for p in range(1, max(1, n) + 1):
            out.append(PR.row(rid, sha.get(rid, ""), p, PR.READ, "candidate_unrendered", PR.NONE, BY_PAGES))
    return out


def _decided_row(rid: str, sha: str, p: int, d: str, why: str, cover: S.Cover | None, h: PG.Page | None) -> dict:
    """One decided page's row. `cover`: the page that covers it, or, for a
    READ page, the page it is suspected of copying."""
    if d == S.READ and cover is not None:
        rule, kind = suspicion(cover)
        return PR.row(
            rid, sha, p, PR.READ, klass(S.SUSPECTED), kind, BY_SELECT, cover.as_dict(), **{PR.SUSPECTED: rule}
        )
    if d == S.READ:
        return PR.row(rid, sha, p, PR.READ, "new", PR.NONE, BY_SELECT)
    if d == S.UNREAD:
        return PR.row(rid, sha, p, PR.UNREAD, "transcription_failed", PR.NONE, BY_SELECT)
    if h is not None and h.aside and why == h.reason:  # a page is set aside as a copy only by its picture
        return PR.row(rid, sha, p, PR.EXCLUDED, klass(why), PR.picture_kind(h), BY_SELECT, PR.picture_cover(h))
    return PR.row(rid, sha, p, PR.EXCLUDED, klass(why), _kind(why), BY_SELECT)


def selection_rows(
    ordered: list[dict[str, Any]],
    decided: dict[str, Any],
    extracts: dict[str, Subset],
    skipped: dict[str, list[str]],
    ident: PG.Loaded,
    sha: dict[str, str],
    npages: Callable[[str], int],
    redecided: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every page of every pulled document. `redecided`: {id: what
    ``update_pages`` decided for a file the other-person rule withheld on an
    earlier run (cleared, kept, kept: cannot re-test)}; a withheld file's rows
    carry it. `decided`: {id: update_select's
    ``_Read``} for the candidates it classified; `npages(id)`: the page count
    of a document no rule opened (its transcript's pages, 0 when unknown: one
    row at page 0 then says the document was set aside whole)."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in ordered:
        rid = str(r["id"])
        c = decided[rid]
        found = ident.pages.get(rid) or {}
        for p, (d, why) in sorted(c.decisions.items()):
            cover = c.suspects.get(p) if d == S.READ else None
            out[(rid, p)] = _decided_row(rid, sha.get(rid, ""), p, d, why, cover, found.get(p))
    for rid, sub in extracts.items():
        if any(k[0] == rid for k in out):
            continue
        n = PG.delivered_exhibit(sub.parent)
        where: dict[str, Any] = {"exhibit": n} if n is not None else {"doc": sub.parent}
        for p in range(1, int(sub.pages) + 1):
            h = (ident.pages.get(rid) or {}).get(p)
            cov = PR.picture_cover(h) if h is not None else {**where, "page": None, "exact": False}
            kind = PR.picture_kind(h) if h is not None else PR.FILE
            out[(rid, p)] = PR.row(rid, sha.get(rid, ""), p, PR.EXCLUDED, "refiled_extract", kind, BY_SELECT, cov)
    for reason, ids in skipped.items():
        for rid in ids:
            if any(k[0] == rid for k in out):
                continue
            kind = _kind(reason)
            extra: dict[str, Any] = {"pointer": PR.IDENTITY_HOLDS} if kind == PR.IDENTITY else {}
            if kind == PR.IDENTITY and rid in (redecided or {}):
                extra["redecided"] = (redecided or {})[rid]
            cover = {"sha256": sha.get(rid, ""), "exact": True} if kind == PR.FILE else None
            status = PR.UNREAD if reason in (NOT_A_SOURCE, unopened.REASON, UO.IMAGE_UNREAD) else PR.EXCLUDED
            for p in range(1, max(1, npages(rid)) + 1) if npages(rid) else [0]:
                out[(rid, p)] = PR.row(rid, sha.get(rid, ""), p, status, klass(reason), kind, BY_SELECT, cover, **extra)
    return list(out.values())


def carry_ink(run_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`rows` with the ink ``update_pages`` measured on each page carried over."""
    ink = {
        (r["doc"], r["page"]): r["evidence"].get("inked")
        for r in PR.read(run_dir)
        if r.get("by") == BY_PAGES and isinstance(r.get("evidence"), dict)
    }
    for r in rows:
        if (r["doc"], r["page"]) in ink:
            r["evidence"]["inked"] = ink[(r["doc"], r["page"])]
    return rows
