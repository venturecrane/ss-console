"""The values a claim states, for the checks that no edit loses one.

A condense, a merge or an audit repair rewrites claim text, and the audit
verifies only what remains: a value that silently disappears is never seen
again (2026-10-09: a home-medication list lost every dosage to a repair).
Numbers are compared outside citations (pages are not values) and in one
spelling per value, so a format variant is neither lost nor new: 209.0 = 209,
97.10 = 97.1, 02 = 2, 02/03/25 = 2/3/2025. Times and ratios keep their parts:
16:05 != 16:06, 132/96 != 132/94.
"""

from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:[.,/:]\d+)*")
CITE = re.compile(r"\(Exhibit (\d+)\s*(?:-\s*p\.\s*([0-9,\s\-]+?))?\s*(?:,\s*machine transcription)?\)")


def norm_number(tok: str) -> str:
    if re.fullmatch(r"\d+\.\d+", tok):
        whole, frac = tok.split(".")
        frac = frac.rstrip("0")
        return str(int(whole)) + ("." + frac if frac else "")
    parts = re.split(r"([/:,.])", tok)
    nums, seps = parts[0::2], parts[1::2]
    if not all(p.isdigit() for p in nums):
        return tok
    vals = [str(int(p)) for p in nums]
    if len(vals) == 3 and seps == ["/", "/"] and len(nums[2]) == 2:
        vals[2] = str(2000 + int(nums[2]))  # m/d/yy -> m/d/yyyy
    return "".join(v + (seps[i] if i < len(seps) else "") for i, v in enumerate(vals))


def numbers(text: str | None) -> set[str]:
    """Normalized numbers outside citations."""
    return {norm_number(t) for t in NUM.findall(CITE.sub("", text or ""))}


_CLOCK = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_YEAR = re.compile(r"(?:19|20)\d{2}")


def content_numbers(text: str | None) -> set[str]:
    """``numbers`` less a time of day and a bare year: what a duplicate's
    survivor must carry before the duplicate goes. A repeated portal message
    differs from its kept copy by its time stamp, never by its care
    (2026-10-14 parity, round 4: three drops refused for "10:32"-style
    stamps and a bare "2026"). Dates, doses, vitals and labs still count."""
    return {n for n in numbers(text) if not (_CLOCK.fullmatch(n) or _YEAR.fullmatch(n))}


def lost(before: str, after: str) -> list[str]:
    """Values `before` states that `after` no longer carries, sorted."""
    return sorted(numbers(before) - numbers(after))


#: A CLINICAL value is a lab, vital or dose: a number carrying a unit, a
#: blood pressure, or a number after a vital or lab name. Page, fax, print
#: date, record-number and time digits carry none of these, so two copies of
#: one record that differ only in their fax stamp state the same values.
_UNIT = r"mg/dl|mmol/l|g/dl|meq/l|u/l|mg|mcg|ug|meq|ml|cc|units?|iu|mmhg|bpm|kg|lbs?|cm|mm|%|°\s?[fc]"
_WITH_UNIT = re.compile(rf"(?<![\d.])(\d+(?:\.\d+)?)\s*({_UNIT})(?![a-z])", re.I)
_BP = re.compile(r"(?<![\d/])(\d{3})\s*/\s*(\d{2,3})(?![\d/])")
_BP_NAMED = re.compile(r"\b(?:bp|blood pressure)\b\D{0,12}?(\d{2,3})\s*/\s*(\d{2,3})(?![\d/])", re.I)
_NAMED = re.compile(
    r"\b(pulse|heart rate|hr|temp(?:erature)?|resp(?:iratory rate|irations)?|rr|spo2|o2 sat(?:uration)?|"
    r"weight|wt|height|bmi|glucose|a1c|hgb|hemoglobin|wbc|platelets?|creatinine|inr|pain)\b"
    r"[^\d\n]{0,15}?(\d+(?:\.\d+)?)",
    re.I,
)


def clinical_values(text: str | None) -> set[str]:
    """The labs, vitals and doses `text` states, one spelling each."""
    t = CITE.sub("", text or "")
    out = {f"{norm_number(n)} {u.lower().replace(' ', '')}" for n, u in _WITH_UNIT.findall(t)}
    out |= {f"bp {a}/{b}" for a, b in _BP.findall(t) + _BP_NAMED.findall(t)}
    out |= {f"{k.lower()[:4]} {norm_number(n)}" for k, n in _NAMED.findall(t)}
    return out


#: A diagnosis or impression TERM: a content word in a clause a finding cue
#: opens ("impression", "diagnosed", "showed", "revealed", "positive for").
_CUE = re.compile(
    r"\b(?:diagnos\w*|impression|assessment|dx|findings?|show(?:s|ed|n)?|reveal(?:s|ed)?|demonstrat\w*|"
    r"consistent with|positive for|suspicious for|noted|exam(?:ination)?)\b([^.;\n]*)",
    re.I,
)
_STOP = {
    "about", "after", "again", "along", "also", "among", "being", "before", "below", "there", "their", "these",
    "those", "which", "while", "would", "could", "should", "other", "under", "until", "with", "without", "within",
    "visit", "patient", "noted", "shows", "showed", "revealed", "reveals", "findings", "finding", "since",
    "today", "level", "right", "left", "times", "place", "where", "further", "recorded",
}  # fmt: skip


def terms(text: str | None) -> set[str]:
    """The diagnosis and impression terms `text` states (lowercase words)."""
    t = CITE.sub("", text or "")
    out: set[str] = set()
    for m in _CUE.finditer(t):
        out |= {w for w in re.findall(r"[a-z]{5,}", m.group(1).lower()) if w not in _STOP}
    return out


def words(text: str | None) -> set[str]:
    """Every lowercase word of five letters or more: what a term is looked up in."""
    return set(re.findall(r"[a-z]{5,}", CITE.sub("", text or "").lower()))


def named_by(problems: list[str] | None) -> set[str]:
    """The values an audit finding names (its unsupported assertions and
    contradictions): the only values an edit may remove."""
    return numbers(" ".join(str(p) for p in problems or []))
