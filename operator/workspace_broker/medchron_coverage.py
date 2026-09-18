"""What a delivered chronology covered: validation, and the union across
deliveries.

Split out of `medchron_ledger.py` at ss#2834, when that module crossed the
500-logical-line ceiling. The cut is along a real seam rather than at a
convenient line: everything here is about the coverage RECORD -- its shape, and
how several of them combine -- while the ledger keeps job state, transitions and
the allowance. Neither half reaches into the other's concerns.

Why the record exists at all: routine 11's UPDATE reads only the records the
delivered chronology did not cover (agreement Exhibit A). That sentence needs a
record, and an update SKIPS whatever the record says was covered -- so every
rule in this module breaks toward re-reading a document and away from marking
one covered. A re-read costs pages. A record dropped from a filed litigation
chronology cannot be recovered.
"""

from __future__ import annotations

import json
from typing import Any

#: A matter's whole document set, with room to spare. The largest A&P matter
#: runs a few hundred; this is a sanity ceiling, not a working limit.
MAX_COVERED_IDS = 20_000
MAX_ID_LEN = 200


def validate_covered(payload: Any) -> str:
    """The covered/uncovered document id sets, as JSON, or raise ValueError.

    Ids only: this record exists so an UPDATE can read what a delivered
    chronology did not cover, and nothing else about the documents belongs on a
    ledger row. Two invariants, both cheap and both load-bearing:

    * The two sets are DISJOINT. A document is either accounted for in the
      delivered document (cited, or excluded with a stated reason) or it is not.
      An id in both would let an update decide either way.
    * `total` equals their combined size, and the runner sends the count it
      pulled. A mismatch means a stage dropped rows between the coverage gate
      and this call, which is the failure species the gate itself exists for,
      so it is refused here rather than stored as a coverage claim nobody
      checked.

    One honest limit on the second invariant: when the CALLER computes `pulled`
    as the size of the two sets it is sending, this check is an arithmetic
    identity and measures nothing. It catches a stage that dropped rows between
    a separately-counted pull and this record; it cannot catch a covered set
    that over-claims. That needs a source the run did not produce -- see the
    filed-document reconcile in `operator/bin/medchron-backfill-covered.py`.
    """
    if not isinstance(payload, dict):
        raise ValueError("covered must be an object")
    out: dict[str, Any] = {}
    sets: dict[str, list[str]] = {}
    for key in ("covered", "uncovered"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            raise ValueError(f"covered.{key} must be a list of document ids")
        ids: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip() or len(item) > MAX_ID_LEN:
                raise ValueError(f"covered.{key} holds a value that is not a document id")
            ids.append(item.strip())
        if len(set(ids)) != len(ids):
            raise ValueError(f"covered.{key} repeats an id")
        sets[key] = sorted(ids)
        out[key] = sets[key]
    overlap = set(sets["covered"]) & set(sets["uncovered"])
    if overlap:
        raise ValueError(f"covered and uncovered share {len(overlap)} id(s), e.g. {sorted(overlap)[0]}")
    total = len(sets["covered"]) + len(sets["uncovered"])
    if total > MAX_COVERED_IDS:
        raise ValueError(f"covered holds {total} ids, above the {MAX_COVERED_IDS} ceiling")
    pulled = payload.get("pulled")
    if pulled is not None:
        if not isinstance(pulled, int) or isinstance(pulled, bool) or pulled < 0:
            raise ValueError("covered.pulled must be a non-negative int")
        if pulled != total:
            raise ValueError(
                f"covered accounts for {total} document(s) but the run pulled {pulled}: "
                "a stage dropped rows between the coverage gate and this record"
            )
        out["pulled"] = pulled
    return json.dumps(out, sort_keys=True)


def union_coverage(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Every delivered chronology's coverage on one matter, combined, or None
    when no row carries a record.

    Cumulative, and that is the whole point. A delivery's record covers only the
    units of that one job; nothing merges it with the delivery before it. Take
    the newest row alone and the SECOND update is told the FIRST chronology's
    documents were never covered -- so it re-reads the entire matter, thousands
    of pages against a cycle allowance, on every matter, forever.

    `uncovered` wins the union, the same direction the runner's own
    `merge_covered` takes: a document one delivery cited and another could not
    use is read again.

    A row whose JSON will not parse is skipped rather than failing the matter.
    One corrupt record must not make a matter's whole history unreadable, and
    the conservative outcome of skipping it is that its documents read as
    uncovered.
    """
    covered: set[str] = set()
    uncovered: set[str] = set()
    seen = False
    number = None
    for row in rows:
        number = row.get("matter_number") or number
        raw = row.get("covered_json")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        seen = True
        covered |= {str(x) for x in (parsed.get("covered") or [])}
        uncovered |= {str(x) for x in (parsed.get("uncovered") or [])}
    if not seen:
        return None
    covered -= uncovered
    return {
        "matter_number": number,
        "deliveries": len(rows),
        "covered_document_ids": sorted(covered),
        "uncovered_document_ids": sorted(uncovered),
    }


def parse_covered(raw: Any) -> tuple[list[str] | None, list[str] | None]:
    """One row's stored record as two lists, or `(None, None)`.

    None, not empty: "nothing was covered" and "nobody wrote down what was
    covered" lead an update to opposite actions, so the absence has to survive
    the read.
    """
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
        return list(parsed.get("covered") or []), list(parsed.get("uncovered") or [])
    except (ValueError, AttributeError):
        return None, None
