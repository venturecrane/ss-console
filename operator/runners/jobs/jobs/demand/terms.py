"""What the request decides about a demand, read in code from its own words
(Law 18: the model never decides either one).

* **The figure.** A demand is SUM-CERTAIN only when the request names a dollar
  figure for the demand itself ("a $75,000 demand", "demand $75K", "Demand
  type: $75,000"); otherwise it is a POLICY-LIMITS demand. The rule is the
  firm's own: the house skeleton already said "a fixed dollar demand replaces
  'Policy Limits' in the banner only when the attorney names one", and the
  firm's two best-practice letters of 2026-11-10 differ on exactly that (the
  Crabtree letter demands the policy limits, the Fasulo letter $75,000), with
  limits undisclosed in both files. A figure that is not tied to the word
  "demand" (a medical total, a lien) is not the demand. Two different demand
  figures in one request hold the job: which one the attorney meant is a
  question for the firm, never a guess.
* **The signer.** The attorney the request names, when it names exactly one
  attorney the firm has authored a demand signature for (a "Requesting
  attorney:" or "Signing attorney:" line first, as the firm's request template
  writes it, then any full name in the text); else the matter's responsible
  attorney, as before. The Fasulo letter (2026-11-10) was Craig Ashton's on a
  matter whose responsible attorney is Chris Price.
"""

from __future__ import annotations

import re
from typing import Iterable

_NUM = r"\$?\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d{2})?\s?(?P<k>[kK]\b|thousand\b|[mM]\b|million\b)?"
#: "$75,000 demand", "$75K sum-certain demand", "75K DEMAND"
_BEFORE = re.compile(_NUM + r"\s*(?:(?:sum[- ]certain|time[- ]limited|settlement|total)\s+)*demand\b", re.I)
#: "demand of $75,000", "demand for $75K", "demand in the amount of $75,000",
#: "Demand type: $75,000", "demand: $75,000", "demand $75,000"
_AFTER = re.compile(
    r"\bdemand(?:\s+type)?\s*(?::|of|for|at|in the (?:sum|amount) of|amount:?|figure:?)?\s*" + _NUM, re.I
)


class FigureUnclear(ValueError):
    """The request names more than one demand figure."""


def _cents(m: re.Match[str]) -> int | None:
    raw = m.group("num").replace(",", "")
    dollars = int(raw)
    unit = (m.group("k") or "").lower()
    if unit in ("k", "thousand"):
        dollars *= 1_000
    elif unit in ("m", "million"):
        dollars *= 1_000_000
    cents = dollars * 100 + (int(m.group("dec")[1:]) if m.group("dec") and not unit else 0)
    # A bare number with no dollar sign and no unit is not money ("demand 2 of 3").
    has_sign = "$" in m.group(0)
    if not has_sign and not unit:
        return None
    return cents if cents >= 100_000 else None  # under $1,000 is not a demand figure


def demand_figure(request: str) -> int | None:
    """The demand figure the request names, in cents, or None (a policy-limits
    demand). Raises FigureUnclear when it names two different figures."""
    found = set()
    for pat in (_BEFORE, _AFTER):
        for m in pat.finditer(request or ""):
            c = _cents(m)
            if c is not None:
                found.add(c)
    if len(found) > 1:
        shown = ", ".join(money(c) for c in sorted(found))
        raise FigureUnclear(f"the request names more than one demand figure ({shown})")
    return found.pop() if found else None


def money(cents: int) -> str:
    """``$75,000`` (no cents when whole), ``$75,000.50``."""
    dollars, rest = divmod(int(cents), 100)
    return f"${dollars:,}" + (f".{rest:02d}" if rest else "")


_ONES = (
    "",
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "ELEVEN",
    "TWELVE",
    "THIRTEEN",
    "FOURTEEN",
    "FIFTEEN",
    "SIXTEEN",
    "SEVENTEEN",
    "EIGHTEEN",
    "NINETEEN",
)
_TENS = ("", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY")


def _under_thousand(n: int) -> str:
    out = []
    if n >= 100:
        out.append(f"{_ONES[n // 100]} HUNDRED")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def words(cents: int) -> str:
    """The figure in words, as the firm's banner writes it: 7500000 ->
    ``SEVENTY-FIVE THOUSAND DOLLARS``; odd cents -> ``... DOLLARS AND 50/100``."""
    dollars, rest = divmod(int(cents), 100)
    if dollars == 0:
        text = "ZERO"
    else:
        parts = []
        for scale, name in ((1_000_000_000, "BILLION"), (1_000_000, "MILLION"), (1_000, "THOUSAND"), (1, "")):
            chunk = (dollars // scale) % 1000
            if chunk:
                parts.append((_under_thousand(chunk) + (f" {name}" if name else "")).strip())
        text = " ".join(parts)
    return f"{text} DOLLARS" + (f" AND {rest:02d}/100" if rest else "")


def _fold(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split())


#: The firm's request template's own line ("Requesting attorney: Christopher Price").
_LABEL = re.compile(r"(?im)^\s*(?:requesting|signing|responsible)\s+attorney\s*:\s*(?P<who>[^\n\[]+)$")


def named_attorney(request: str, candidates: dict[str, Iterable[str]]) -> str | None:
    """The key of the one attorney the request names, or None. ``candidates``
    maps each attorney's key (the roster's staff name) to every full form of
    their name the firm writes (staff name, demand signer, letters name). A
    labelled line wins; otherwise exactly one attorney named anywhere in the
    text; two or more named and no label: None (the responsible attorney)."""
    forms = {key: {_fold(f) for f in names if len(_fold(f).split()) >= 2} for key, names in candidates.items()}
    for m in _LABEL.finditer(request or ""):
        said = _fold(m.group("who"))
        hits = [k for k, fs in forms.items() if any(f == said or f in said.split(" , ") for f in fs)]
        hits = hits or [k for k, fs in forms.items() if any(re.search(rf"\b{re.escape(f)}\b", said) for f in fs)]
        if len(hits) == 1:
            return hits[0]
    text = _fold(request or "")
    hits = [k for k, fs in forms.items() if any(re.search(rf"\b{re.escape(f)}\b", text) for f in fs)]
    return hits[0] if len(hits) == 1 else None


__all__ = ["FigureUnclear", "demand_figure", "money", "named_attorney", "words"]
