"""Anchors: the checkable tokens in a claim, and the pages that carry them.

A claim is supported or not by specific things on a page: a date, a dose, a
blood pressure, a drug name, a proper name. Those are what an auditor can
look for, so they are what this module pulls out of a claim (find_anchors),
counts on a page (score_pages), and uses to widen a citation window to the
neighbouring page that carries the value the cited page does not
(choose_window). Anchors are typed and normalised so 04/03/2021 in the claim
matches "April 3, 2021" on the page: date, num (with a clinical unit, and
blood pressures), drug (suffix rule plus the allowlist beside this file),
proper (capitalised tokens of five or more letters minus a stoplist).
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
for _m, _i in list(MONTHS.items()):
    MONTHS[_m[:3]] = _i
MONTHS["sept"] = 9
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b"),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(" + _MON + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I),
    re.compile(r"\b(\d{1,2})\s+(" + _MON + r")\s+(\d{4})\b", re.I),
]
UNITS = (
    r"mg|mcg|ug|g|kg|lbs?|oz|ml|mL|cc|l|bpm|mmhg|%|cm|mm|in|ft|units?|meq|iu|f|c|degrees|min|mins|hrs?|hours?|"
    r"days?|weeks?|months?|years?|yo|y/o|tabs?|tablets?|caps?|puffs?|drops?|mg/dl|mg/kg|ng/ml|mcg/ml|u|sec|"
    r"seconds?|rpm|/min|x"
)
NUM_UNIT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(" + UNITS + r")\b", re.I)
BP = re.compile(r"(?<![\d/])(\d{2,3})\s?/\s?(\d{2,3})(?![\d/])")
DRUG_SUFFIX = re.compile(
    r"\b[a-z]{3,}(?:pril|sartan|statin|olol|dipine|azole|mycin|cillin|cycline|floxacin|profen|codone|morphone|"
    r"oxetine|tidine|prazole|tadine|zepam|zolam|pam|lam|mab|vir|done|pin|sone|nisone|caine|tinib|parin|afil|"
    r"triptan|gliptin|glitazone|semide|thiazide)\b",
    re.I,
)
PROPER = re.compile(r"\b[A-Z][a-z]{4,}(?:-[A-Z][a-z]+)?\b")
STOPLIST = {
    "patient",
    "exhibit",
    "medical",
    "records",
    "record",
    "doctor",
    "there",
    "these",
    "those",
    "their",
    "which",
    "where",
    "while",
    "after",
    "before",
    "during",
    "under",
    "about",
    "again",
    "against",
    "between",
    "through",
    "without",
    "within",
    "would",
    "could",
    "should",
    "noted",
    "notes",
    "report",
    "reports",
    "reported",
    "history",
    "present",
    "presented",
    "physical",
    "examination",
    "assessment",
    "diagnosis",
    "diagnoses",
    "treatment",
    "plan",
    "follow",
    "visit",
    "visits",
    "encounter",
    "right",
    "left",
    "bilateral",
    "chief",
    "complaint",
    "review",
    "systems",
    "normal",
    "abnormal",
    "negative",
    "positive",
    "denies",
    "denied",
    "continued",
    "continue",
    "ordered",
    "referred",
    "referral",
    "provider",
    "office",
    "clinic",
    "hospital",
    "center",
    "imaging",
    "emergency",
    "department",
    "discharge",
    "discharged",
    "admitted",
    "prescribed",
    "prescription",
    "medication",
    "medications",
    "surgery",
    "surgical",
    "procedure",
    "results",
    "result",
    "findings",
    "impression",
    "shows",
    "showed",
    "states",
    "stated",
    "because",
    "however",
    "further",
    "additional",
    "including",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "august",
    "september",
    "october",
    "november",
    "december",
    "machine",
    "transcription",
    "chronology",
    "entry",
    "claim",
    "pages",
    "unknown",
    "other",
    "every",
    "first",
    "second",
    "third",
    "since",
    "until",
    "today",
    "later",
    "earlier",
    "still",
    "being",
    "having",
    "reviewed",
    "recorded",
    "documented",
    "documentation",
    "letter",
    "signed",
    "orders",
}


def _load_allowlist() -> set[str]:
    out: set[str] = set()
    try:
        text = resources.files(__package__).joinpath("anchors_drugs.txt").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return out
    for line in text.splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.add(line)
    return out


DRUG_ALLOWLIST = _load_allowlist()


def _iso(y: Any, m: Any, d: Any) -> str | None:
    try:
        y, m, d = int(y), int(m), int(d)
    except ValueError:
        return None
    if y < 100:
        y += 2000 if y < 50 else 1900
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def dates(text: str) -> set[str]:
    out: set[str] = set()
    for m in DATE_PATTERNS[0].finditer(text):
        iso = _iso(m.group(3), m.group(1), m.group(2))
        if iso:
            out.add(iso)
    for m in DATE_PATTERNS[1].finditer(text):
        iso = _iso(m.group(1), m.group(2), m.group(3))
        if iso:
            out.add(iso)
    for m in DATE_PATTERNS[2].finditer(text):
        mon = MONTHS.get(m.group(1).lower().rstrip(".")[:4].rstrip(".")) or MONTHS.get(m.group(1).lower()[:3])
        if mon:
            iso = _iso(m.group(3), mon, m.group(2))
            if iso:
                out.add(iso)
    for m in DATE_PATTERNS[3].finditer(text):
        mon = MONTHS.get(m.group(2).lower()[:3])
        if mon:
            iso = _iso(m.group(3), mon, m.group(1))
            if iso:
                out.add(iso)
    return out


def numbers(text: str) -> set[str]:
    out = {(m.group(1) + m.group(2)).lower().replace(" ", "") for m in NUM_UNIT.finditer(text)}
    for m in BP.finditer(text):
        s, d = int(m.group(1)), int(m.group(2))
        if 60 <= s <= 300 and 30 <= d <= 200:
            out.add(f"{s}/{d}")
    return out


def drugs(text: str) -> set[str]:
    out = {m.group(0).lower() for m in DRUG_SUFFIX.finditer(text)}
    low = text.lower()
    for name in DRUG_ALLOWLIST:
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            out.add(name)
    return out


def propers(text: str) -> set[str]:
    return {m.group(0).lower() for m in PROPER.finditer(text) if m.group(0).lower() not in STOPLIST}


def find_anchors(claim: str) -> list[str]:
    out = {f"date:{d}" for d in dates(claim)} | {f"num:{n}" for n in numbers(claim)}
    out |= {f"drug:{d}" for d in drugs(claim)} | {f"proper:{p}" for p in propers(claim)}
    return sorted(out)


def page_features(text: str) -> dict[str, set[str]]:
    return {
        "date": dates(text),
        "num": numbers(text),
        "drug": drugs(text),
        "proper": propers(text) | {w.lower() for w in re.findall(r"\b[A-Z]{5,}\b", text)},
    }


def found_on(anchors: list[str], text: str, feats: dict[str, set[str]] | None = None) -> list[str]:
    feats = feats or page_features(text)
    return [a for a in anchors if a.partition(":")[2] in feats.get(a.partition(":")[0], ())]


def score_pages(anchors: list[str], page_texts: dict[int, str]) -> dict[int, tuple[int, list[str]]]:
    out = {}
    for p, t in page_texts.items():
        hit = found_on(anchors, t or "")
        out[p] = (len(hit), hit)
    return out


def choose_window(
    cited: list[int], npages: int, index: Any, exhibit: int, anchors: list[str] = (), cap: int = 12
) -> list[int] | None:
    """Pages to send for one claim, or None when the claim cannot go text:
    every cited page must be text-eligible; the window is the cited pages,
    each eligible neighbour one page either side (the image path widens
    exactly this far), and an eligible page two either side when it carries
    at least two of the claim's anchors; never more than `cap`."""
    cited = sorted(set(cited))
    if not cited or len(cited) > cap:
        return None
    for p in cited:
        if not (1 <= p <= npages) or not index.eligible(exhibit, p):
            return None
    window = set(cited)
    for p in cited:
        for q in (p - 1, p + 1):
            if 1 <= q <= npages and q not in window and index.eligible(exhibit, q):
                window.add(q)
    if anchors:
        for p in cited:
            for q in (p - 2, p + 2):
                if 1 <= q <= npages and q not in window and index.eligible(exhibit, q):
                    if len(found_on(list(anchors), index.page_text(exhibit, q) or "")) >= 2:
                        window.add(q)
    win = sorted(window)
    while len(win) > cap:
        far = max((q for q in win if q not in cited), key=lambda q: min(abs(q - c) for c in cited), default=None)
        if far is None:
            break
        win.remove(far)
    return win


def build_clusters(
    windows: list[tuple[str, list[int]]],
    cap: int = 12,
    floor: int = 3,
    index: Any = None,
    exhibit: int | None = None,
    npages: int = 0,
) -> list[tuple[list[int], list[str]]]:
    """Group per-claim windows on one exhibit into cache units: overlapping
    or touching windows join while the union stays within `cap`; a cluster
    under `floor` pages is padded with CONTIGUOUS eligible neighbours."""
    items = sorted(windows, key=lambda kv: (min(kv[1]), max(kv[1])))
    clusters: list[tuple[set[int], list[str]]] = []
    for key, pages in items:
        pset = set(pages)
        if clusters:
            cp, ck = clusters[-1]
            touching = bool(pset & cp) or (min(pset) - max(cp) <= 1)
            if touching and len(cp | pset) <= cap:
                clusters[-1] = (cp | pset, ck + [key])
                continue
        clusters.append((pset, [key]))
    out = []
    for pages, keys in clusters:
        if index is not None and len(pages) < floor:
            lo, hi = min(pages), max(pages)
            grow = {"up": True, "down": True}
            while len(pages) < floor and (grow["up"] or grow["down"]):
                if grow["up"]:
                    hi += 1
                    if hi <= npages and index.eligible(exhibit, hi):
                        pages.add(hi)
                    else:
                        grow["up"] = False
                if len(pages) >= floor:
                    break
                if grow["down"]:
                    lo -= 1
                    if lo >= 1 and index.eligible(exhibit, lo):
                        pages.add(lo)
                    else:
                        grow["down"] = False
        out.append((sorted(pages), keys))
    return out
