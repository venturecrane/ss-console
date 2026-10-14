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

import re
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


#: The audited claim a near provider name becomes: the auditor reads the name
#: off the cited page image, and a repair writes the spelling the page shows.
CLAIM = re.compile(r"^Provider named on the record: (.+?)\. \((Exhibit \d+(?: - p\. [0-9,\s\-]+)?)\)\s*$")


def claims(entries: list[str], log: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """(claim lines, {citation: composed name}) for each flagged name the
    safe rule marks likely the same person as a delivered one: its spelling
    is settled by the audit against the page, never by edit distance."""
    lines: list[str] = []
    composed: dict[str, str] = {}
    for row in log:
        if not row.get("likely_same_person"):
            continue
        name = row["composed"]
        cite = next((m.group(0) for e in entries if B.provider_of(e) == name for m in B.CITE.finditer(e)), None)
        if cite is None or cite[1:-1] in composed:
            continue
        lines.append(f"Provider named on the record: {name}. {cite}")
        composed[cite[1:-1]] = name
    return lines, composed


def split_claims(pre: str) -> tuple[str, list[tuple[str, str]]]:
    """(the preamble without provider claims, [(audited name, ref)])."""
    keep: list[str] = []
    got: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", pre or ""):
        m = CLAIM.match(para.strip())
        if m:
            got.append((m.group(1).strip(), m.group(2)))
        elif para.strip():
            keep.append(para.strip("\n"))
    return "\n\n".join(keep), got


def respell(entries: list[str], audited: list[tuple[str, str]], composed: dict[str, str]) -> tuple[list[str], dict]:
    """New entries with each audited spelling applied where the audit changed
    the composed one; {composed: confirmed} of what changed."""
    change = {composed[ref]: name for name, ref in audited if ref in composed and composed[ref] != name}
    out = []
    for e in entries:
        p = B.provider_of(e)
        if p in change:
            lines = e.splitlines()
            lines[1] = change[p] + lines[1][len(lines[1].split("|")[0].rstrip()) :]
            e = "\n".join(lines)
        out.append(e)
    return out, change


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
