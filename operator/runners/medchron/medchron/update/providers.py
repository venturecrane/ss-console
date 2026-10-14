"""A new entry's provider spelled near, but not as, the delivered chronology
spells a provider: FLAGGED, never rewritten. $0.

A scan reads "Angell" for "Angeli", and the firm then reads two names for
one person. But two real clinicians can be one letter apart ("Peterson" and
"Pederson", both M.D.), and respelling one into the other merges two people
in a filed chronology, which no reader can see was done. So the composed
spelling (the record's own) always stands; each near name is listed in the
collect and assemble reports, marked "likely the same person" when the
names are long enough, one or two edits apart, and agree in first name or
initial and credential, so the pre-delivery review can settle it.
"""

from __future__ import annotations

from typing import Any

from . import body as B

MIN_LEN, MAX_EDITS = 8, 2


def edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _parts(name: str) -> tuple[str, str]:
    """(name part, credential part), both lowercased and trimmed."""
    head, _, cred = name.partition(",")
    return head.strip().lower(), cred.replace(".", "").replace(" ", "").lower()


def _qualifies(new: str, old: str) -> bool:
    (n, nc), (o, oc) = _parts(new), _parts(old)
    if len(n) < MIN_LEN or len(o) < MIN_LEN or nc != oc:
        return False
    return n.split()[0][:1] == o.split()[0][:1] and edits(n, o) <= MAX_EDITS


def snap(entries: list[str], base_ents: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """(entries, unchanged; a log row per provider spelled near a delivered one)."""
    delivered = sorted({B.provider_of(b) for b in base_ents if B.provider_of(b)})
    log: list[dict[str, Any]] = []
    for e in entries:
        p = B.provider_of(e)
        if not p or p in delivered:
            continue
        near = [d for d in delivered if edits(_parts(p)[0], _parts(d)[0]) <= MAX_EDITS]
        if near:
            likely = [d for d in near if _qualifies(p, d)]
            log.append(
                {"date": B.iso(e), "composed": p, "near": near, "likely_same_person": likely, "action": "flagged"}
            )
    return list(entries), log
