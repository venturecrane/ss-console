"""The pages an update charges: what it pulled, less what the firm already holds read. $0.

WHY (2026-11-06). An update charged the cycle page allowance with every page
it extracted (``budget.pages_read``), and most of what an update pulls is pages
the delivered chronology already read: on a client seat two updates charged
451 and 1,386 pages, the firm's reply quoted those figures, and SMD credited
451 and 1,371 back by hand afterwards. The update's own selection record
(``update/selection.json``, written by ``update_select``) already says which
pages those are, so the charge leaves them out:

* a new document's pages already read in a delivered exhibit (``ALREADY``);
* every page of a re-filed copy or extract of a delivered exhibit
  (``subsets`` whose ``subset_of`` is a delivered exhibit);
* every page of a file skipped as byte-identical to a delivered exhibit or to
  a cited source, or set aside on the delivered run's own finding.

A page repeated between two NEW documents, a billing page and any page read
are still charged, as a run charges them. With no selection record (the
update stopped before ``update_select``) nothing is taken off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...stages.base import read_json, read_jsonl
from ...stages.prior_reading import PREFIX as PRIOR_PREFIX
from .select import ALREADY

#: ``update_select``'s skip key for a file the firm already holds (``_candidates``).
BYTE_IDENTICAL = "byte-identical to a delivered exhibit"


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def delivered_pages(slug_dir: Path, unit: str) -> int:
    """Pages of this update's pulled documents the delivered chronology already read."""
    sel = read_json(slug_dir / "runs" / unit / "update" / "selection.json", None)
    if not isinstance(sel, dict):
        return 0
    n = sum(_int((x.get("excluded") or {}).get(ALREADY)) for x in sel.get("exhibits") or [] if isinstance(x, dict))
    n += sum(
        _int(s.get("pages"))
        for s in sel.get("subsets") or []
        if isinstance(s, dict) and isinstance(s.get("subset_of"), dict) and not s["subset_of"].get("id")
    )
    skipped = sel.get("skipped") or {}
    held = {
        str(i) for k, ids in skipped.items() if k == BYTE_IDENTICAL or k.startswith(PRIOR_PREFIX) for i in ids or []
    }
    n += sum(_int(r.get("pages")) for r in read_jsonl(slug_dir / "extracted.jsonl") if str(r.get("id")) in held)
    return n


def charged_pages(slug_dir: Path, unit: str, pages_read: int) -> int:
    """``pages_read`` less ``delivered_pages``, never below 0."""
    return max(0, pages_read - delivered_pages(slug_dir, unit))
