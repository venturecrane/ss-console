"""Which directory listing she means, when her words match several: the obvious one, or a short question.

The vendor's custodian directory is nationwide and full of near-duplicates: a
hospital appears as its health system's entry, a bare-name entry, a street-less
entry and an NPI row, all at one address, next to its emergency room, its
cancer center and its surgery center. Handing her that list is a checklist
(2026-10-29: one request drew 50 listings for an ambulance company, 20 for a
hospital, and four for a VA campus that is one building). So, per facility:

1. **Her custodian, not its departments.** Rows whose name does not carry her
   name's distinctive words are dropped, and so are departments and physician
   groups unless she named one.
2. **One place listed several times is one place.** Rows with the same
   distinctive name at one street (or street-less in the same city or ZIP) are
   one location; the vendor's curated ``neo_`` entry represents it.
3. **Near the order's other facilities.** When places remain, the one place in
   the same city or ZIP as a facility already settled on this order, else in a
   neighbouring ZIP area, is the pick. The client's home is never an anchor: an
   ambulance is placed by the incident, and her address never reaches a reply.

A pick is shown to her, with its reason, before the order line she approves;
only her yes orders it. Two candidates that fit equally well are a question.
Pure: no I/O, so the same rows always give the same answer.
"""

from __future__ import annotations

import re
from typing import Any

from .gap_locate import (
    DEPARTMENT_WORDS,
    _norm,
    department_inside,
    distinctive,
    lab_ok,
    names_match,
)

#: A separately billing group at the hospital's address is not the hospital.
_GROUP_WORDS = re.compile(r"physician|hospitalist|anesthesi|medical group|\bplaza\b", re.I)
_REGION = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$")
_STREET = re.compile(r"^\s*(\d+[A-Za-z]?)\s+([A-Za-z0-9]+)")
#: Neighbouring ZIP areas: first three digits within this distance, same state.
ZIP3_REACH = 2
SHORTLIST = 3


def _place(address: str) -> dict[str, str]:
    """street key, city, state and ZIP read off a directory address line."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    region = _REGION.search(parts[-1]) if parts else None
    state, zip_ = (region.group(1), region.group(2)) if region else ("", "")
    if not region and parts and re.fullmatch(r"[A-Z]{2}(\s+\S+)?", parts[-1]):
        state = parts[-1][:2]
    city = parts[-2] if len(parts) >= 2 else ""
    street = _STREET.match(parts[0]) if len(parts) >= 3 else None
    key = f"{street.group(1)} {street.group(2).casefold()}" if street else ""
    return {"street": key, "city": _norm(city), "state": state, "zip": zip_}


def _name_key(her: str, listing: str) -> frozenset[str]:
    """The listing's distinctive words, read off the segment that names her
    custodian ("Example Health - Saint Example Medical Center")."""
    segments = [s for s in re.split(r"\s+-\s+", listing) if s.strip()]
    named = next((s for s in segments if names_match(her, s)), listing)
    return frozenset(distinctive(named))


def _main_listings(name: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Her custodian's own listings: name match, and no department or group she
    did not name. (Not ``the_custodian_itself``: its exact-phrase test keeps
    "VA NORTHFIELD" and drops "Northfield VA", the vendor's own curated entry.)"""
    pool = [
        c for c in found if (names_match(name, c["name"]) or department_inside(name, c)) and lab_ok(name, c["name"])
    ]
    if not (DEPARTMENT_WORDS.search(name) or _GROUP_WORDS.search(name)):
        main = [c for c in pool if not (DEPARTMENT_WORDS.search(c["name"]) or _GROUP_WORDS.search(c["name"]))]
        pool = main or pool
    return pool


def places(name: str, found: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Her custodian's listings grouped into places, each place's best entry first."""
    rows = [(c, _place(c["address"]), _name_key(name, c["name"])) for c in _main_listings(name, found)]
    groups: dict[tuple[Any, ...], list[tuple[dict[str, Any], dict[str, str]]]] = {}
    for c, p, key in rows:
        if p["street"]:
            groups.setdefault((key, p["street"]), []).append((c, p))
    for c, p, key in rows:
        if p["street"]:
            continue
        near = [g for g in groups if g[0] == key and any(_same_town(p, q) for _, q in groups[g])]
        if len(near) == 1:
            groups[near[0]].append((c, p))
        elif p["city"] or p["zip"]:
            groups.setdefault((key, "town", p["city"], p["zip"]), []).append((c, p))
    out = []
    for members in groups.values():
        members.sort(key=lambda m: (not m[0]["custodian_id"].startswith("neo_"), not m[1]["street"]))
        out.append([c for c, _ in members])
    return out


def _same_town(a: dict[str, str], b: dict[str, str]) -> bool:
    return bool((a["zip"] and a["zip"] == b["zip"]) or (a["city"] and a["city"] == b["city"]))


def _near(p: dict[str, str], anchor: dict[str, str]) -> bool:
    if not (p["zip"] and anchor["zip"]) or p["state"] != anchor["state"]:
        return False
    return abs(int(p["zip"][:3]) - int(anchor["zip"][:3])) <= ZIP3_REACH


def _local(group: list[dict[str, Any]], anchors: list[dict[str, str]], test: Any) -> bool:
    return any(test(_place(c["address"]), a) for c in group for a in anchors)


def narrow(name: str, found: list[dict[str, Any]], anchors: list[dict[str, Any]]) -> dict[str, Any]:
    """``{"pick", "why", "shortlist"}``. ``anchors`` are the order's settled
    facilities: ``{"facility": her name, "address": the listing's address}``."""
    groups = places(name, found)
    if len(groups) == 1:
        n = len(groups[0])
        why = f"{n} listings for it, all one place" if n > 1 else "the only listing for that name"
        return {"pick": groups[0][0], "why": why, "shortlist": []}
    points = [_place(a["address"]) for a in anchors]
    near_to = " and ".join(dict.fromkeys(a["facility"] for a in anchors))
    for test, how in ((_same_town, "in the same town as"), (_near, "near")):
        local = [g for g in groups if _local(g, points, test)]
        if len(local) == 1:
            return {"pick": local[0][0], "why": f"the one {name} listing {how} {near_to}", "shortlist": []}
        if local:
            others = [g for g in groups if g not in local]
            return {"pick": None, "why": "", "shortlist": [g[0] for g in local + others][:SHORTLIST]}
    pool = groups or [[c] for c in found]
    return {"pick": None, "why": "", "shortlist": [g[0] for g in pool]}


__all__ = ["narrow", "places", "SHORTLIST", "ZIP3_REACH"]
