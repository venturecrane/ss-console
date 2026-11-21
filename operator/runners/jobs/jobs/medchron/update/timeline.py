"""The Treatment Timeline of an update. $0, no model.

Split out of ``assemble.py`` (2026-11-20) so neither module crosses the size
ceiling; ``assemble`` re-exports what it used to define.

WHY (2026-11-20, a real update). Before, an update recounted every lane from
all entries: today's alias order (``stages/group.py``, first alias that
matches) moved 3 delivered entries into another lane, and the count was of
entries, not days, so a delivered row read 5 visits and the update's 8 for
the same 5 days. A delivered row is what the firm has read; an update keeps
its count and lane and adds to it only the distinct new days of the new
entries assigned to it (``update_rows``). A delivered row that disagrees with
its own entries is still reported (``assemble``'s
``delivered_table_vs_its_own_entries``), never silently accepted.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from typing import Any, Callable

from ..lane_ref import lane_references
from . import body as B


def period_has(period: str, mmddyyyy: str) -> bool:
    ds = [p.strip() for p in period.split(" - ")]

    def k(d: str) -> str:
        return d[6:10] + d[0:2] + d[3:5]

    return k(ds[0]) <= k(mmddyyyy) <= k(ds[-1])


#: How alike a delivered entry's provider must read to a delivered row's
#: label, among the rows whose period covers its date, to be that row's visit.
LANE_LIKENESS = 0.6


def delivered_lanes(
    delivered_rows: list[str], base_ents: list[str], canon: Callable[[str], str], name_of: Callable[[str], str]
) -> Callable[[str], str]:
    """lane(entry) -> its Treatment Timeline label. Delivered lanes stay as
    delivered: a row's label is never re-derived, so its credentials stay
    ("Jane Roe, RN") and it is never absorbed into another lane that the
    grouping now collapses it into (2026-10-13: an eye clinic's delivered row
    was absorbed into its health system's, and nurses lost "RN").

    An entry takes the delivered row its provider names exactly; else the one
    row whose label groups with it (``canon``), the likeliest when several
    do; else, for a DELIVERED entry, the row covering its date whose label
    reads most alike (the delivered label came from a grouping of its own
    day); else its own grouped name, a new lane."""
    rows = []
    for r in delivered_rows:
        c = _cells(r)
        if len(c) >= 4 and c[0]:
            rows.append((c[0], c[1]))
    labels = [lb for lb, _ in rows]
    delivered = set(base_ents)

    def alike(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def lane(e: str) -> str:
        name = name_of(e)
        if name in labels:
            return name
        d = e.splitlines()[0][:10]
        covering = [lb for lb, per in rows if period_has(per, d)]
        same = [lb for lb in labels if canon(lb) == canon(name)]
        if same:
            pool = [lb for lb in same if lb in covering] or same
            return max(pool, key=lambda lb: alike(lb, name))
        if e in delivered:
            near = [lb for lb in covering if alike(lb, name) >= LANE_LIKENESS]
            if near:
                return max(near, key=lambda lb: alike(lb, name))
        return canon(name)

    return lane


def _cells(row: str) -> list[str]:
    return [x.strip() for x in row.strip().strip("|").split("|")]


def _mdy(isod: str) -> str:
    return f"{isod[5:7]}/{isod[8:10]}/{isod[:4]}"


def _period(ds: list[str]) -> str:
    return _mdy(ds[0]) + (f" - {_mdy(ds[-1])}" if ds[-1] != ds[0] else "")


def provider_rows(
    entries: list[str], canon: Callable[[str], str], name_of: Callable[[str], str], days: bool = False
) -> list[str]:
    """Treatment Timeline rows exactly as build_doc computes them, from dates
    of service only: an entry dated by a records date (printed, released,
    faxed, received) is no visit. `days`: count distinct days, not entries
    (an update's new lane)."""
    es = [{"iso": B.iso(e), "provider": name_of(e), "text": e} for e in entries if B.is_service(e)]
    es.sort(key=lambda x: x["iso"])
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"dates": [], "texts": []})
    for e in es:
        a = agg[canon(e["provider"])]
        a["dates"].append(e["iso"])
        a["texts"].append(e["text"])
    refs = lane_references({prov: a["texts"] for prov, a in agg.items()})
    rows = []
    for prov, a in agg.items():
        ds = sorted(a["dates"])
        rows.append((ds[0], prov, _period(ds), len(set(ds)) if days else len(ds), refs[prov]))
    rows.sort()
    return [f"| {p} | {per} | {n} | {ref} |" for _, p, per, n, ref in rows]


def _iso(mdy: str) -> str:
    d = mdy.strip()
    return d[6:10] + "-" + d[0:2] + "-" + d[3:5]


def _first_iso(row: str) -> str:
    return _iso(_cells(row)[1].split(" - ")[0])


#: A reference cell new references may be appended to: empty, or citations
#: (``Exhibit N - p. x``) separated by "; ".
REF_CELL = re.compile(r"(?:Exhibit \d+(?: - p\. [0-9,\s\-]+)?(?:; Exhibit \d+(?: - p\. [0-9,\s\-]+)?)*)?")
PERIOD_CELL = re.compile(r"\d{2}/\d{2}/\d{4}(?: - \d{2}/\d{2}/\d{4})?")


def _gains(
    base_ents: list[str],
    new_ents: list[str],
    lane: Callable[[str], str],
    canon: Callable[[str], str],
    name_of: Callable[[str], str],
) -> dict[str, list[str]]:
    """{lane: the new service entries on a day it gains}. A new entry on a
    day a DELIVERED service entry of the same canonical provider already has
    adds no day, whichever lane today's grouping gives that delivered entry
    (2026-11-20 review: a delivered visit today's alias order moves to
    another lane made the same day count again)."""
    seen = {(B.iso(e), canon(name_of(e))) for e in base_ents if B.is_service(e)}
    out: dict[str, list[str]] = defaultdict(list)
    for e in new_ents:
        if B.is_service(e) and (B.iso(e), canon(name_of(e))) not in seen:
            out[lane(e)].append(e)
    return out


def _grown(row: str, ents: list[str], notes: list[str]) -> str:
    """A delivered row with its new days: count, period and reference. A cell
    in a shape this cannot extend keeps the whole row as delivered, noted."""
    c = _cells(row)
    if not c[2].isdigit() or not PERIOD_CELL.fullmatch(c[1]):
        notes.append(f"kept as delivered (visits {c[2]!r}, period {c[1]!r} not extendable): {c[0]}")
        return row
    days = {B.iso(e) for e in ents}
    ends = c[1].split(" - ")
    ds = sorted({_iso(ends[0]), _iso(ends[-1]), *days})
    ref, add = c[3], lane_references({c[0]: sorted(ents, key=B.iso)})[c[0]]
    if add and not REF_CELL.fullmatch(ref):
        notes.append(f"reference {ref!r} not extendable; {add} not added: {c[0]}")
    elif add and f"Exhibit {add.split()[1]}" not in re.findall(r"Exhibit \d+", ref):
        ref = f"{ref}; {add}" if ref else add
    return f"| {c[0]} | {_period(ds)} | {int(c[2]) + len(days)} | {ref} |"


def update_rows(
    delivered_rows: list[str],
    base_ents: list[str],
    new_ents: list[str],
    lane: Callable[[str], str],
    canon: Callable[[str], str] = str,
    name_of: Callable[[str], str] | None = None,
) -> tuple[list[str], list[str]]:
    """(the update's Treatment Timeline rows, notes for SMD). A delivered row
    is kept as delivered (its lane, count and reference; byte for byte when no
    new entry joins it); it gains only the distinct service days of the NEW
    entries assigned to it on which no delivered visit of the same provider
    falls, its period widens to them, and its reference names the new
    exhibit. Delivered entries are never re-laned or recounted. A new entry no
    delivered row takes starts a lane of its own, counted in distinct days,
    placed by its first day."""
    kept = [r.strip() for r in delivered_rows if len(_cells(r)) >= 4 and _cells(r)[0]]
    gain = _gains(base_ents, new_ents, lane, canon, name_of or B.provider_of)
    labels = {_cells(r)[0] for r in kept}
    notes: list[str] = []
    out = [_grown(r, gain[_cells(r)[0]], notes) if gain.get(_cells(r)[0]) else r for r in kept]
    new_lanes = [e for lb, es in gain.items() if lb not in labels for e in es]
    for row in provider_rows(new_lanes, str, lane, days=True):
        k = next((i for i, r in enumerate(out) if _first_iso(r) > _first_iso(row)), len(out))
        out.insert(k, row)
    return out, notes


def lane_diff(before: list[str], after: list[str]) -> list[dict[str, Any]]:
    """[{lane, before, after}] for every Treatment Timeline lane that differs."""

    def lanes(rows: list[str]) -> dict[str, dict[str, Any]]:
        out = {}
        for r in rows:
            c = _cells(r)
            if len(c) >= 4:
                out[c[0]] = {"period": c[1], "visits": c[2], "reference": c[3]}
        return out

    b, a = lanes(before), lanes(after)
    return [
        {"lane": k, "before": b.get(k), "after": a.get(k)}
        for k in [*b, *[k for k in a if k not in b]]
        if b.get(k) != a.get(k)
    ]
