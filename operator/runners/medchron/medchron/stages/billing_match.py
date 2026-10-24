"""Matching bills to the chronology's providers by rule, with no authored
table (stop audit row 28, 2026-10-24).

The Treatment Timeline is the billing chart's spine, and its provider cell is
whatever the entries' header line names: often a clinician, with the facility
in parentheses ("Jane Roe, PA-C (NVRH 9W Laboratory)"), or a clinician alone.
Bills are labelled by facility or organization ("NV Ridge Health"). The
chart matched only through the firm's authored `billing.provider_match`
table and a head-substring of the cell, so a matter whose facilities nobody
had authored matched nothing: the $0 replay of a 4,418-page matter produced a
worksheet reading SUBTOTAL $0.00 with 175 providers as gaps. An authored
table per matter is a hand intervention; this module is the rule instead.

* A provider cell's facilities are its parenthesized parts, plus the cell
  itself when it is not a person's name.
* A bill label matches a facility when the significant words of one are all
  in the other (at least one of them distinctive, not a word like "health"),
  or when one carries the other's initials as a word: "NV Ridge Health" is
  NVRH, an acronym taken whole ("NV") contributing all its letters.
* Rows a single bill label matches together are merged into one row: the
  bill does not say which department it covers, so one total cannot be split.
* One clinician listed twice ("Eva Ramos, M.D." / "Eva M Ramos, MD") is one
  row: first and last name, credentials and middle initials dropped, same
  facility.

Nothing here produces an amount; it decides which row a printed figure sits
on. A label that still matches nothing is the chart's own row, as printed.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .group import CRED, FACILITY_WORD

STOP = {"the", "of", "at", "and", "for", "a", "an", "dba", "inc", "llc", "pc", "apc", "corp", "ltd", "co", "&"}
GENERIC = {
    "health",
    "healthcare",
    "medical",
    "medicine",
    "center",
    "centre",
    "clinic",
    "hospital",
    "care",
    "services",
    "service",
    "group",
    "associates",
    "laboratory",
    "lab",
    "labs",
    "imaging",
    "radiology",
    "department",
    "dept",
    "system",
    "physicians",
    "home",
    "urgent",
    "emergency",
}
WORD = re.compile(r"[A-Za-z0-9]+")
PAREN = re.compile(r"\(([^()]*)\)")
CRED_WORD = re.compile(rf"^(?:{CRED})\.?$", re.I)
PERSON = re.compile(r"^[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){1,3}$")


def _words(s: str) -> list[str]:
    return WORD.findall(s or "")


def significant(s: str) -> set[str]:
    return {w.lower() for w in _words(s) if len(w) >= 2 and w.lower() not in STOP}


def acronym(s: str) -> str:
    """Initials of the significant words; a word already in capitals is an
    acronym and gives all its letters ("NV Ridge Health" -> "nvrh")."""
    out = []
    for w in _words(s):
        if w.lower() in STOP or w.isdigit():
            continue
        out.append(w.lower() if (w.isupper() and len(w) <= 5) else w[0].lower())
    return "".join(out)


def _caps_words(s: str) -> set[str]:
    return {w.lower() for w in _words(s) if w.isupper() and w.isalpha() and len(w) >= 3}


def base_and_facilities(cell: str) -> tuple[str, list[str]]:
    base = PAREN.sub("", cell or "").strip(" ,-")
    facs = [p.strip() for p in PAREN.findall(cell or "") if p.strip() and not CRED_WORD.match(p.strip())]
    return base, facs


def person_key(cell: str) -> tuple[str, str] | None:
    """(first, last) when the cell's base is a person's name with or without
    credentials, else None."""
    base, _ = base_and_facilities(cell)
    segs = [s.strip() for s in base.split(",") if s.strip()]
    if not segs or not all(CRED_WORD.match(s.replace(" ", "")) for s in segs[1:]):
        return None
    toks = [t for t in segs[0].split() if not CRED_WORD.match(t)]
    credentialed = len(segs) > 1 or len(toks) < len(segs[0].split())
    name = " ".join(toks)
    # A bare capitalised pair ("Example Clinic") is not a person: only a name
    # carrying a credential is read as a clinician.
    if not credentialed or not PERSON.match(name) or FACILITY_WORD.search(name):
        return None
    parts = [t.strip(".").lower() for t in toks if len(t.strip(".")) > 1]
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


def facilities(cell: str) -> list[str]:
    base, facs = base_and_facilities(cell)
    return facs if person_key(cell) else [base, *facs]


def facility_hit(label: str, fac: str) -> bool:
    lab, f = significant(label), significant(fac)
    if not lab or not f:
        return False
    for small, big in ((f, lab), (lab, f)):
        if small <= big and small - GENERIC:
            return True
    la, fa = acronym(label), acronym(fac)
    return (len(la) >= 3 and la in _caps_words(fac)) or (len(fa) >= 3 and fa in _caps_words(label))


def hits(label: str, spine: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in spine if any(facility_hit(label, f) for f in r.get("facilities") or facilities(r["provider"]))]


def _merge(rows: list[dict[str, Any]], title: str) -> dict[str, Any]:
    firsts = [r["first"] for r in rows if r["first"]]
    lasts = [r["last"] for r in rows if r["last"]]
    froms: list[str] = []
    facs: list[str] = []
    for r in rows:
        froms += r.get("merged_from") or [r["provider"]]
        facs += r.get("facilities") or facilities(r["provider"])
    return {
        "provider": title,
        "first": min(firsts) if firsts else None,
        "last": max(lasts) if lasts else None,
        "merged_from": froms,
        "facilities": facs,
    }


def collapse_clinicians(spine: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per clinician: same first and last name, same facilities."""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for i, row in enumerate(spine):
        pk = person_key(row["provider"])
        key = (pk, tuple(sorted(f.lower() for f in base_and_facilities(row["provider"])[1]))) if pk else i
        groups.setdefault(key, []).append(row)
    out = []
    for rows in groups.values():
        if len(rows) == 1:
            out.append(rows[0])
            continue
        merged = _merge(rows, rows[0]["provider"])
        merged["merged_from"] = [r["provider"] for r in rows]
        out.append(merged)
    return sorted(out, key=lambda r: r["first"] or "9999")


def merge_by_labels(
    spine: list[dict[str, Any]], labels: list[str], resolved: Callable[[str], str | None]
) -> list[dict[str, Any]]:
    """Merge the rows one bill label matches together, titled by that label
    as printed. A label the authored tiers already resolve is left alone."""
    spine = list(spine)
    for label in labels:
        if not label.strip() or resolved(label):
            continue
        hit = hits(label, spine)
        if len(hit) < 2:
            continue
        ids = {id(r) for r in hit}
        merged = _merge(hit, f"{label.strip()} (all departments)")
        spine = [r for r in spine if id(r) not in ids] + [merged]
    return sorted(spine, key=lambda r: r["first"] or "9999")
