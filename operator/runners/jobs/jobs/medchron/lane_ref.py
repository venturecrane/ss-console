"""The Treatment Timeline reference for each provider lane.

A lane's reference is the first citation of its earliest entry, unless that
citation's exhibit belongs to another lane. An entry may cite another
provider's exhibit first (an MRI entry that cites the ordering physician's
referral before the imaging report), and the lane named for one provider then
pointed the reader at another provider's records (2026-11-09, an A&P update:
the imaging lane referenced the orthopaedist's exhibit). An exhibit belongs to
another lane when another lane cites it more often than this one does; the
reference is the first citation, in date order, of an exhibit that does not.
A lane whose every exhibit belongs elsewhere (a physician whose only records
sit inside a hospital's exhibit) keeps its first citation.
"""

from __future__ import annotations

import re
from collections import Counter

CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")


def lane_references(lanes: dict[str, list[str]]) -> dict[str, str]:
    """``{lane: reference}`` (``Exhibit N - p. x``, no parentheses; '' when
    the lane cites nothing) for lanes given as their entries' texts, oldest
    first."""
    cites = {
        lane: [(int(m.group(1)), m.group(0)[1:-1]) for t in texts for m in CITE.finditer(t)]
        for lane, texts in lanes.items()
    }
    counts = {lane: Counter(n for n, _ in cs) for lane, cs in cites.items()}
    out: dict[str, str] = {}
    for lane, cs in cites.items():
        if not cs:
            out[lane] = ""
            continue
        own = next(
            (ref for n, ref in cs if all(counts[other][n] <= counts[lane][n] for other in counts if other != lane)),
            None,
        )
        out[lane] = own if own is not None else cs[0][1]
    return out
