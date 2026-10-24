"""Every document filed since the delivery, counted and accounted for, for
the update's Records Reviewed paragraph. $0.

The paragraph once said 9 documents were filed since the delivery when 11
were: two byte-identical copies were dropped by the pull (``duplicate_of``)
and named nowhere (2026-10-13). Now the count is the pulled documents, and
each one that is not a new exhibit is named with what it is:

* a byte-identical copy: "duplicate copy of Exhibit N" (the exhibit its
  bytes are: a delivered one, or the new exhibit its twin became);
* a refiled subset (``selection.json`` "subsets", from ``update_select``):
  "re-filed pages of Exhibit N";
* not a PDF or an image: named as not readable as record pages;
* left out by the identity rule (its text names someone other than the
  client, 2026-10-24): named, with the rule's reason, and not counted among
  the documents reviewed;
* anything else the pull holds that no exhibit, copy or rule accounts for:
  named as not read, so no document is ever left out of the count silently.
"""

from __future__ import annotations

from typing import Any

from ..gates.foreign_identity import RULE_REASONS, WITHHELD_REASON as WITHHELD
from .locate import vendor_name

IDENTICAL = "byte-identical to a delivered exhibit"
NOT_PAGES = "not a PDF or an image"


def _where(
    rid: str, by_id: dict[str, dict], ex_of: dict[str, int], delivered: dict[str, int], subsets: dict[str, dict]
) -> str | None:
    """'Exhibit N' that the document `rid` is, or None (an uncited one)."""
    if rid in ex_of:
        return f"Exhibit {ex_of[rid]}"
    sha = (by_id.get(rid) or {}).get("sha256")
    if sha in delivered:
        return f"Exhibit {delivered[sha]}"
    sup = (subsets.get(rid) or {}).get("subset_of") or {}
    if sup:
        return _where(str(sup.get("id") or ""), by_id, ex_of, delivered, {}) or (
            f"Exhibit {sup['exhibit']}" if sup.get("exhibit") else None
        )
    return None


def _copies(by_id: dict[str, dict], sel: dict[str, Any], ex_of: dict[str, int], delivered: dict[str, int]) -> list[str]:
    """Each copy, named with the exhibit it is a copy of."""
    subsets = {str(s["id"]): s for s in sel.get("subsets") or [] if s.get("id")}
    identical = {str(x) for x in (sel.get("skipped") or {}).get(IDENTICAL) or []}
    out: list[str] = []
    for rid, r in by_id.items():
        if r.get("duplicate_of"):
            dup = str(r["duplicate_of"])
            target = _where(dup, by_id, ex_of, delivered, subsets) or (
                vendor_name(by_id[dup]) if dup in by_id else "another document filed with it"
            )
            out.append(f"{vendor_name(r)} (duplicate copy of {target})")
        elif rid in identical:
            target = _where(rid, by_id, ex_of, delivered, {}) or "an exhibit already delivered"
            out.append(f"{vendor_name(r)} (duplicate copy of {target})")
        elif rid in subsets:
            target = _where(rid, by_id, ex_of, delivered, subsets) or "an exhibit cited here"
            out.append(f"{vendor_name(r)} (re-filed pages of {target})")
    return out


def accounted(
    raw_rows: list[dict[str, Any]],
    sel: dict[str, Any],
    delivered_exhibits: list[dict[str, Any]],
    keep: list[dict[str, Any]],
    uncited: list[dict[str, Any]],
) -> dict[str, Any]:
    """{docs, copies: ["name (duplicate copy of Exhibit N)", ...], unreadable:
    [names], unread: [names], left_out: [names]}. `docs` counts every document
    the pull holds (failed retrievals, the firm's own exclusions and the
    identity rule's aside), never fewer than the ones this run accounts for.
    `left_out` names each document the identity rule withheld."""
    by_id = {str(r["id"]): r for r in raw_rows if r.get("ok") and r.get("id")}
    ex_of = {str(r["id"]): int(r["exhibit"]) for r in keep if r.get("id")}
    delivered = {str(e["sha256"]): int(e["exhibit"]) for e in delivered_exhibits if e.get("sha256")}
    skipped = sel.get("skipped") or {}
    # The firm's own exclusions: an authored name rule, or a document the firm
    # said to leave out (an identity decision, ``WITHHELD``).
    by_rule = [str(x) for why in RULE_REASONS.values() for x in skipped.get(why) or []]
    excluded = (
        {str(x.get("id")) for x in sel.get("excluded_named") or [] if x.get("id")}
        | {str(x) for x in skipped.get(WITHHELD) or []}
        | set(by_rule)
    )
    copies = _copies(by_id, sel, ex_of, delivered)
    not_pages = {str(x) for x in skipped.get(NOT_PAGES) or []}
    unreadable = [vendor_name(by_id[i]) for i in by_id if i in not_pages]
    named = (
        set(ex_of)
        | {str(r.get("id")) for r in uncited}
        | not_pages
        | excluded
        | {i for i, r in by_id.items() if r.get("duplicate_of")}
        | {str(x) for x in skipped.get(IDENTICAL) or []}
        | {str(s.get("id")) for s in sel.get("subsets") or []}
    )
    unread = [vendor_name(r) for i, r in by_id.items() if i not in named]
    floor = len(keep) + len(uncited) + len(copies) + len(unreadable) + len(unread)
    docs = max(len([i for i in by_id if i not in excluded]), floor)
    left_out = [vendor_name(by_id[i]) for i in by_rule if i in by_id]
    return {"docs": docs, "copies": copies, "unreadable": unreadable, "unread": unread, "left_out": left_out}
