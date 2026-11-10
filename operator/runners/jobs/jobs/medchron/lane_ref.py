"""The Treatment Timeline reference for one provider lane.

A lane's reference used to be the first citation of its earliest entry. An
entry may cite another provider's exhibit first (an MRI entry that cites the
ordering physician's referral before the imaging report), so a lane named for
one provider pointed the reader at another provider's records (2026-11-09, an
A&P update: the imaging lane referenced the orthopaedist's exhibit). The lane's
own exhibit is the one its entries cite most; the reference is that exhibit's
first citation in date order. Ties go to the earliest-cited exhibit, which is
the old rule whenever it was right.
"""

from __future__ import annotations

import re
from collections import Counter

CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")


def lane_reference(texts_in_date_order: list[str]) -> str:
    """The reference (``Exhibit N - p. x``, no parentheses) for a lane whose
    entries' texts are given oldest first; '' when none cites an exhibit."""
    counts: Counter[int] = Counter()
    first: dict[int, str] = {}
    order: list[int] = []
    for text in texts_in_date_order:
        for m in CITE.finditer(text):
            n = int(m.group(1))
            counts[n] += 1
            if n not in first:
                first[n] = m.group(0)[1:-1]
                order.append(n)
    if not order:
        return ""
    best = max(order, key=lambda n: (counts[n], -order.index(n)))
    return first[best]
