"""Which directory location is the provider the gap audit names: settled from the RECORDS first.

The vendor's custodian directory is nationwide, so a name alone ("Example
Health", "Saint Example") answers with five to twenty locations. Asking the
requester to pick each one hands her a checklist. The matter's own documents
already say where the provider is: the letterhead and remit-to of the bills and
records the audit row cites. So, per provider:

1. **Evidence from the file.** The documents the provider's audit rows cite
   (their "Where the file points to it" names), read as text; around each
   mention of the provider, and in each document's letterhead, the California
   city and ZIP and the phone. The Medicals-tab contact's street, city and ZIP
   are added when the tab carries the provider.
2. **Re-query the directory** with the name and each ZIP the file gives, and
   filter the name-only answer by the file's ZIPs, cities and phones.
3. **Prefer the record's own department.** A row about the ED physicians or
   radiology prefers the directory entry for that department (emergency,
   radiology or imaging, laboratory, pathology).
4. Only what is still more than one location, or none, is left as a question.

Nothing is chosen on name similarity alone.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from .records_orders import _candidate

MAX_DOCS = 4
MAX_TEXT = 300_000
_ZIP_CITY = re.compile(r"([A-Z][A-Za-z .'-]{2,30}),?\s+(?:CA|California)\.?\s+(9\d{4})")
_PHONE = re.compile(r"\(?\b(\d{3})\)?[-. ]?(\d{3})[-. ](\d{4})\b")
DEPARTMENTS = (
    (re.compile(r"\b(ed|er|emergency)\b", re.I), re.compile(r"emergenc", re.I)),
    (re.compile(r"radiolog|imaging|x-?ray|\bxr\b|\bmri\b|\bct\b", re.I), re.compile(r"radiolog|imaging", re.I)),
    (re.compile(r"\blabs?\b|laborator", re.I), re.compile(r"\blab|laborator", re.I)),
    (re.compile(r"patholog", re.I), re.compile(r"patholog", re.I)),
    (
        re.compile(r"family (practice|medicine)|\bpcp\b|primary care", re.I),
        re.compile(r"family (medicine|practice)", re.I),
    ),
    (re.compile(r"pain management", re.I), re.compile(r"pain management", re.I)),
)
#: Words that name a department or a time, not the custodian.
QUALIFIERS = {"prior", "labs", "lab", "physicians", "physician", "ed", "er", "radiology"}
GENERIC = {
    "medical",
    "health",
    "healthcare",
    "center",
    "centre",
    "group",
    "inc",
    "clinic",
    "the",
    "of",
    "and",
    "dr",
    "md",
    "llc",
    "corp",
    "care",
    "services",
    "associates",
    "hospital",
    "family",
    "practice",
    "institute",
}
TOO_MANY = 6
DEPARTMENT_WORDS = re.compile(
    r"emergenc|cardiac|patholog|wound|rehab|surgery|surgical|radiolog|imaging|\blab|pediatric|oncolog|"
    r"pharmacy|physical therapy|behavioral|birth|cancer|dialysis|infusion|sleep|urgent",
    re.I,
)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.casefold()).split())


def cited_names(rows: list[dict[str, str]]) -> list[str]:
    """The document names the rows cite, without page, part and FILE noise."""
    out = []
    for r in rows:
        for piece in re.split(r";|\band\b", r.get("where", "")):
            m = re.search(r"FILE:\s*([^,)]+)", piece)
            name = m.group(1) if m else piece
            name = re.split(r",\s*p+\.|\[part|\(", name)[0]
            name = _norm(name)
            if len(name) >= 4 and name not in out:
                out.append(name)
    return out


def _files_for(files: list[dict[str, Any]], cited: list[str], name: str) -> list[dict[str, Any]]:
    head = _norm(name).split()[:2]
    picked = []
    for f in files:
        fn = _norm(re.sub(r"\.\w{2,4}$", "", str(f.get("name") or "")))
        if not fn:
            continue
        if any(fn.startswith(c) or c.startswith(fn) for c in cited) or (head and all(w in fn for w in head)):
            picked.append(f)
    return picked[:MAX_DOCS]


def evidence(
    client: Any,
    matter_id: str,
    files: list[dict[str, Any]],
    rows: list[dict[str, str]],
    name: str,
    exclude: tuple[str, ...] = (),
) -> dict[str, list[str]]:
    """ZIPs, cities and phones the file's own documents give for this provider,
    most frequent first. Reads at most MAX_DOCS documents; a document that will
    not download or read is skipped, never a guess."""
    from .extract import extract_text_ex

    zips, cities, phones = Counter(), Counter(), Counter()
    words = [w for w in _norm(name).split() if len(w) > 3][:3]
    for f in _files_for(files, cited_names(rows), name):
        try:
            _info, blob = client.download_file(matter_id, f["id"])
            # mechanical text, or the seat's own transcription cache for a
            # scanned page (free; never a new transcription)
            text = extract_text_ex(
                blob, file_name=str(f.get("name") or ""), file_extension=str(f.get("fileExtension") or "")
            ).text
        except Exception:  # noqa: BLE001 - one unreadable document leaves that document out of the evidence
            continue
        text = (text or "")[:MAX_TEXT]
        low = text.casefold()
        spans = [text[:3000]]  # the letterhead
        for w in words:
            for m in re.finditer(re.escape(w), low):
                spans.append(text[max(0, m.start() - 200) : m.start() + 600])
        for span in spans:
            for city, z in _ZIP_CITY.findall(span):
                zips[z] += 1
                cities[city.strip().split("  ")[-1].strip()] += 1
            for a, b, c in _PHONE.findall(span):
                phones[f"{a}{b}{c}"] += 1
    # the client's own ZIP and city (on every record about her) place the
    # client, never the provider
    skip = {e.casefold() for e in exclude if e}
    return {
        "zips": [z for z, _ in zips.most_common(5) if z.casefold() not in skip],
        "cities": [c for c, _ in cities.most_common(5) if c.casefold() not in skip],
        "phones": [p for p, _ in phones.most_common(5)],
    }


def distinctive(name: str) -> list[str]:
    """The words that name THIS custodian: no generic word ("medical",
    "hospital"), no department or time word ("radiology", "prior")."""
    return [w for w in _norm(name).split() if w not in GENERIC and w not in QUALIFIERS and len(w) > 1]


_LAB = re.compile(r"\blabs?\b|laborator|patholog|diagnostics\b", re.I)


def lab_ok(name: str, candidate: str) -> bool:
    """A provider the audit describes as a lab (labs, laboratory, pathology,
    diagnostics) is only a candidate whose OWN name says lab: the clinic it sits
    in is not the lab that holds the results and the bill."""
    return not _LAB.search(name) or bool(_LAB.search(candidate))


def names_match(name: str, candidate: str) -> bool:
    """The candidate's NAME carries EVERY distinctive word of the provider's: a
    directory answer that shares only an address, a generic word
    ("Healthcare") or one word of a two-word name ("Saint Example" is not
    "Saint Other") is not this provider."""
    want = distinctive(name)
    have = set(_norm(candidate).split())
    return bool(want) and all(w in have for w in want)


#: A facility word after the custodian's words, in an address: "... SAINT
#: EXAMPLE HOSPITAL", never a street ("Example St").
_FACILITY_AFTER = r"(hospital|medical center|medical centre|health center|campus)"


def department_inside(name: str, candidate: dict[str, Any]) -> bool:
    """A department entity that bills inside the named facility (a radiology
    group whose address line is the hospital's): its own name is a department,
    and its address names the provider followed by a facility word."""
    if not DEPARTMENT_WORDS.search(candidate["name"]):
        return False
    want = distinctive(name)
    addr = _norm(candidate["address"])
    return (
        bool(want) and re.search(r"\b" + r"\s+".join(map(re.escape, want)) + r"\s+" + _FACILITY_AFTER, addr) is not None
    )


def same_place(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Directory duplicates (one name, one street, several ids) are one place:
    the vendor's own curated entry (``neo_``) when there is one, else the first."""
    if len(found) < 2:
        return found
    keys = {(_norm(c["name"]).replace(" llc", ""), " ".join(_norm(c["address"]).split()[:3])) for c in found}
    if len(keys) == 1:
        return [next((c for c in found if c["custodian_id"].startswith("neo_")), found[0])]
    return found


def _search(yc: Any, terms: list[str], zip_: str | None, name: str = "") -> tuple[str, list[dict[str, Any]]]:
    for term in terms:
        found = [c for c in (_candidate(r) for r in yc.get_locations(term, zip_)) if c is not None]
        # a department entity (the radiology group that bills inside the
        # hospital) may carry the hospital's name in its address instead
        who = name or terms[0]
        found = [
            c for c in found if (names_match(who, c["name"]) or department_inside(who, c)) and lab_ok(who, c["name"])
        ]
        if found:
            return term, found
    return terms[0], []


def _local(found: list[dict[str, Any]], zips: list[str], cities: list[str]) -> list[dict[str, Any]]:
    return [
        c
        for c in found
        if any(z in c["address"] for z in zips) or any(ci.casefold() in c["address"].casefold() for ci in cities if ci)
    ]


def department(found: list[dict[str, Any]], context: str) -> list[dict[str, Any]]:
    """The candidates of the department the rows are about, when they name one."""
    for asks, entry in DEPARTMENTS:
        if asks.search(context):
            hit = [c for c in found if entry.search(c["name"])]
            if hit:
                return hit
    return found


def the_custodian_itself(found: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    """The entry that IS the named custodian ("Example Health - Saint Example
    Medical Center" for "Saint Example Medical Center"), not one of its
    departments: its name ends with the term and carries nothing after it."""
    for term in terms:
        t = _norm(term)
        hit = [c for c in found if t in _norm(c["name"]) and not DEPARTMENT_WORDS.search(c["name"])]
        if len(hit) == 1:
            return hit
    return found


def resolve(
    yc: Any,
    name: str,
    terms: list[str],
    hint: dict[str, str] | None,
    evidence_fn: Callable[[], dict[str, list[str]]],
    context: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """matched (with the term and ZIP that re-find it) / ambiguous / no_match.
    The file's own documents are read only when the name alone is not enough."""
    term, found = _search(yc, terms, None, name)
    found = same_place(found)
    where: dict[str, tuple[str, str | None]] = {c["custodian_id"]: (term, None) for c in found}
    # even one directory hit is accepted only where the file places the provider
    ev = evidence_fn()
    zips = list(dict.fromkeys(([hint["zip"]] if hint and hint.get("zip") else []) + ev.get("zips", [])))
    cities = list(dict.fromkeys(([hint["city"]] if hint and hint.get("city") else []) + ev.get("cities", [])))
    pool = _local(found, zips, cities)
    if not pool and len(found) == 1:
        pool = list(found)
    for z in zips[:3]:  # the directory searched at the file's own ZIP
        if len(department(pool, context)) == 1:
            break
        t, at_zip = _search(yc, terms, z, name)
        for c in at_zip:
            where.setdefault(c["custodian_id"], (t, z))
        local = _local(at_zip, [z], cities) or at_zip
        pool = list({c["custodian_id"]: c for c in pool + local}.values())
    pool = department(pool, context) if len(pool) > 1 else pool
    pool = the_custodian_itself(pool, terms) if len(pool) > 1 else pool
    pool = same_place(pool)
    if len(pool) > 1 and hint and hint.get("street"):
        key = re.match(r"\s*(\d+)\s+(\w+)", hint["street"])
        if key:
            same = [c for c in pool if re.match(rf"\s*{key.group(1)}\s+{re.escape(key.group(2))}", c["address"], re.I)]
            pool = same if len(same) == 1 else pool
    log(f"  {name}: {len(found)} by name, {len(pool)} after the file's address evidence {zips[:3]}")
    if len(pool) == 1 and not _local(pool, zips, cities):
        # the only candidate is somewhere the file never places this provider
        return {"outcome": "ambiguous", "candidates": pool, "reason": "the file does not place it at that address"}
    if len(pool) == 1:
        t, z = where[pool[0]["custodian_id"]]
        return {"outcome": "matched", "location": pool[0], "term": t, "zip": z, "evidence": True}
    if pool and (len(pool) <= TOO_MANY or _local(pool, zips, cities)):
        return {"outcome": "ambiguous", "candidates": pool[:TOO_MANY]}
    # a long nationwide list the file's own address does not narrow is not a
    # choice to put to her; it is a custodian the directory does not know here
    return {"outcome": "no_match", "candidates": []}
