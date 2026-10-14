"""A new entry's provider, spelled the way the delivered chronology spells it. $0.

A scan reads "Angell" for "Angeli"; the update then draws the same clinician
as a second lane and the firm reads two names for one person. A new entry's
provider takes the delivered spelling ONLY when that is safe:

* both names are at least MIN_LEN characters (the person or facility part,
  before the credential): a short name one letter from another ("Kim",
  "Lim") is a different person as often as a misreading;
* the edit distance is at most MAX_EDITS;
* the first name or initial agrees, and so does the credential.

Exactly one delivered name must qualify. A near name that does not qualify
is left as composed and FLAGGED in the report, never changed.
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
    """(entries with qualifying providers respelled, a log row per near name)."""
    delivered = sorted({B.provider_of(b) for b in base_ents if B.provider_of(b)})
    out: list[str] = []
    log: list[dict[str, Any]] = []
    for e in entries:
        p = B.provider_of(e)
        if not p or p in delivered:
            out.append(e)
            continue
        near = [d for d in delivered if edits(_parts(p)[0], _parts(d)[0]) <= MAX_EDITS]
        ok = [d for d in near if _qualifies(p, d)]
        if len(ok) == 1:
            lines = e.splitlines()
            lines[1] = ok[0] + lines[1][len(lines[1].split("|")[0].rstrip()) :]
            out.append("\n".join(lines))
            log.append({"date": B.iso(e), "composed": p, "delivered": ok[0], "action": "respelled"})
            continue
        if near:
            log.append({"date": B.iso(e), "composed": p, "near": near, "action": "flagged, left as composed"})
        out.append(e)
    return out, log
