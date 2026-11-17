"""Which of round 1's open must-fix findings the update review's second round
re-checks, on which pages, and what that is projected to cost. $0
(``stages/update_review.py`` makes the paid calls).

WHY (2026-11-17). Round 2 re-reviewed every entry the open findings sat on,
with every page of every exhibit those entries cite, and fell back to every
entry on the finding's date: on a real update it read 851 pages in 47 calls
($19.12) to re-check 20 entries, and the 8 findings it left open were all
ones the reviewer had got wrong. Its cost followed whole exhibits, not
findings. Now a finding is re-checked only when a second read can settle it:

* it is not class 2 (omitted content: ``update/elsewhere.py`` settles one
  already composed elsewhere, and one it does not is a real omission that
  holds) and not a duplicate (``update/duplicates.py`` decides those);
* the finisher EDITED it and the audit REVERTED the edit: the finisher and
  the audit disagree about what a page says, which is what a second reader
  settles. A finding the finisher declined is left as it is;
* it names an exhibit and a page, and its entry is found by that page.

A part round 1 could not finish (``not reviewed``) is re-read on the pages it
names: that is its first review. Each re-check is shown only the finding's
page and the pages either side of it, and the entry's own cited pages; never
the whole exhibit, and never the other entries of that date. Every finding
not sent is recorded with why (``recheck``) and stays open: it is settled by
a rule or it holds, never dropped.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

from . import duplicates as DU, elsewhere as EL, frozen as FZ, review_parts as RP, review_repair as RR

SENT = "sent to the re-check"
#: what the finisher's reverted edit leaves in its finding's outcome (``finisher.settle``)
REVERTED = "reverted:"
_PAGES = re.compile(r"Exhibit (\d+) pp\. (\d+)-(\d+)")
#: Output tokens the re-check writes per page shown: measured on a real
#: round 2 (851 pages, 47 calls, $19.12 at the review tier's rates), whose
#: output was about 760 tokens a page beside its input.
OUT_TOKENS_PER_PAGE = 800
#: The re-check round is skipped above this projection (``levers.update_recheck_cap_usd``).
DEFAULT_CAP_USD = 5.0


def current(row: dict[str, Any], ents: list[RR.Entry]) -> RR.Finding | None:
    """The frozen finding on the document as it is now: its own anchor while
    that entry still has the row's date line, else the entry on that date
    citing its page, else any entry citing its page (on that entry's claim
    citing it). None when no entry is found that way."""
    f = copy.copy(FZ.finding(row))
    on = set(row.get("dates") or [])
    own = next((e for e in ents if e.anchor == f.anchor.split(".")[0]), None)
    if own is not None and (not on or FZ.date_line(own.text) in on):
        return f
    page = (f.exhibit, f.page)
    cites = [e for e in ents if f.exhibit and f.page and page in e.cites()]
    pick = next((e for e in cites if FZ.date_line(e.text) in on), cites[0] if cites else None)
    if pick is None:
        return None
    f.anchor = next((c.anchor for c in pick.claims if c.exhibit == f.exhibit and f.page in c.pages), pick.anchor)
    return f


def why_not(f: RR.Finding | None) -> str:
    """'' when the finding is re-checked, else why it is not."""
    if f is None:
        return "not re-checked: no entry cites its page any more"
    if f.klass == FZ.NOT_REVIEWED:
        return ""
    if EL.is_omission(f):
        return "not re-checked: an omission is settled by rule when its content is composed elsewhere, else it holds"
    if DU.is_duplicate(f) or "duplicate" in f.klass.lower():
        return "not re-checked: a duplicate is decided in code"
    if not f.exhibit or not f.page:
        return "not re-checked: it names no exhibit and page"
    if REVERTED not in f.outcome:
        return "not re-checked: the finisher made no edit the audit reverted, so no page is in dispute"
    return ""


def pick(
    rows: list[dict[str, Any]], open_: list[int], ents: list[RR.Entry]
) -> tuple[dict[int, RR.Finding], dict[int, str], dict[int, RR.Finding]]:
    """({frozen row: its finding on the current document} to re-check,
    {frozen row: why it is not re-checked}, {frozen row: its finding on the
    current document, for every row whose entry is found})."""
    now = {i: f for i in open_ if (f := current(rows[i], ents)) is not None}
    left = {i: why for i in open_ if (why := why_not(now.get(i)))}
    return {i: f for i, f in now.items() if i not in left}, left, now


def _window(f: RR.Finding, npages: Callable[[int], int]) -> tuple[int, list[int]]:
    m = _PAGES.search(f.problem) if f.klass == FZ.NOT_REVIEWED else None
    if m:
        return int(m.group(1)), list(range(int(m.group(2)), int(m.group(3)) + 1))
    n = max(npages(int(f.exhibit or 0)), int(f.page or 0))
    p = int(f.page or 0)
    return int(f.exhibit or 0), [q for q in (p - 1, p, p + 1) if 1 <= q <= n]


def parts(
    send: dict[int, RR.Finding],
    ents: list[RR.Entry],
    npages: Callable[[int], int],
    already: RP.Already | None = None,
) -> list[RP.Part]:
    """One pages part per entry and exhibit: the findings' pages and their
    neighbours, and the entry's own cited pages. A neighbour that is a copy
    of a delivered page (`already`) is not shown. A part round 1 did not
    finish is re-read on its pages with every entry it named."""
    by_e = {e.anchor: e for e in ents}
    want: dict[tuple[str, int], set[int]] = {}
    owners: dict[tuple[str, int], list[RR.Entry]] = {}
    for f in send.values():
        e = by_e.get(f.anchor.split(".")[0])
        if e is None:
            continue
        ex, pages = _window(f, npages)
        skip = (already or {}).get(ex) or {}
        pages = [p for p in pages if p == f.page or p not in skip]
        named = [by_e[a] for a in dict.fromkeys(FZ._ANCHOR.findall(f.problem)) if a in by_e] or [e]
        group = named if f.klass == FZ.NOT_REVIEWED else [e]
        k = (group[0].anchor, ex)
        want.setdefault(k, set()).update(pages)
        owners[k] = group
        for g in group:
            for x, p in g.cites():
                want.setdefault((g.anchor, x), set()).add(p)
                owners.setdefault((g.anchor, x), [g])
    return [RP.Part("pages", owners[k], k[1], sorted(ps)) for k, ps in sorted(want.items()) if k[1]]


def projected_usd(pages: int, n_parts: int, rate: Any, page_tokens: int) -> float:
    """Pages shown x the review tier's rate per page (`page_tokens` in for an
    image and its label, about OUT_TOKENS_PER_PAGE out), plus 2,000 tokens of
    entry text per part."""
    cents = (pages * page_tokens + n_parts * 2_000) * rate.input_per_million_cents
    cents += pages * OUT_TOKENS_PER_PAGE * rate.output_per_million_cents
    return cents / 1e8


def cap_usd(cfg: Any) -> float:
    try:
        v = cfg.get("levers", "update_recheck_cap_usd", DEFAULT_CAP_USD)
        return float(DEFAULT_CAP_USD if v is None else v)
    except (TypeError, ValueError):
        return DEFAULT_CAP_USD


__all__ = ["DEFAULT_CAP_USD", "SENT", "cap_usd", "current", "parts", "pick", "projected_usd", "why_not"]
