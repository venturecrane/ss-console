"""Propounded discovery: which sets a request asks for, and defined terms.

**One document per set.** An attorney's email often asks for several sets at
once ("RFA Set One, RFP Set One and SROGs Set One"). Each set is its own paper,
served on its own, with its own caption, signature block and proof of service
(and, for special interrogatories, its own section 2030.050 decision). The job
therefore drafts each requested set as its own unit (``run.py``), and
``variants`` reads, from the request's own words, which sets those are. On
2026-10-12 one compose wrote all three into one Word file with one proof of
service after the last, and a declaration that belonged to none of them.

**Defined terms.** Chris's rule D6: "Include a Definitions section with
capitalized defined terms used throughout." A model defines INCIDENT LOCATION
and then writes "the incident location" three requests later; ``enforce_caps``
writes every use of a defined term in capitals, by code, outside quotations
and markers, and reports what it changed. ``undefined_caps`` lists capitalized
phrases a set uses that its Definitions never define, for the review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: (key, label used in file names and instructions, the request's words for it).
VARIANTS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "special_interrogatories",
        "Special Interrogatories",
        re.compile(r"(?i)\bspecial(?:ly\s+prepared)?\s+interrogator(?:y|ies)\b|\bs\.?\s?rogs?\b|\bsprogs?\b"),
    ),
    (
        "requests_for_admission",
        "Requests for Admission",
        re.compile(r"(?i)\brequests?\s+for\s+admissions?\b|\brfas?\b"),
    ),
    (
        "requests_for_production",
        "Requests for Production",
        re.compile(
            r"(?i)\brequests?\s+for\s+(?:the\s+)?production\b|\brequests?\s+to\s+produce\b|\brfps?\b|\brpds?\b|"
            r"\bdocument\s+requests?\b|\brequests?\s+for\s+documents\b"
        ),
    ),
)
#: The label each variant's requests carry (the format check's label styles).
LABELS = {
    "special_interrogatories": re.compile(r"(?im)^\W*SPECIAL INTERROGATORY NO\.?\s*\d+"),
    "requests_for_admission": re.compile(r"(?im)^\W*REQUEST FOR ADMISSION NO\.?\s*\d+"),
    "requests_for_production": re.compile(r"(?im)^\W*REQUEST FOR PRODUCTION(?: OF DOCUMENTS)? NO\.?\s*\d+"),
}


@dataclass(frozen=True)
class Unit:
    key: str
    label: str

    def instruction(self, others: list[str]) -> str:
        also = (
            f" The request also asks for {', '.join(others)}; each is drafted separately as its own document, so "
            "do not write any of them here."
            if others
            else ""
        )
        return (
            f"THIS DOCUMENT (set by the job, from the request's own words): draft ONLY the {self.label} set, as one "
            f"complete paper with its own caption, identification block, Definitions, requests and signature block."
            + also
        )


def variants(request: str) -> list[Unit]:
    """The propounded sets the request names, in the order it names them.
    Empty when it names none (the compose asks, or drafts the one it can)."""
    hits = []
    for key, label, pat in VARIANTS:
        m = pat.search(request or "")
        if m:
            hits.append((m.start(), Unit(key, label)))
    return [u for _pos, u in sorted(hits, key=lambda h: h[0])]


def kinds_in(doc: str) -> list[str]:
    """The variants whose request labels a document carries."""
    return [k for k, pat in LABELS.items() if pat.search(doc)]


# ---- defined terms ------------------------------------------------------------------------

_DEFS = re.compile(r"(?ims)^#{1,6}\s*\**\s*DEFINITIONS\b.*?$(.*?)(?=^#{1,6}\s|\Z)")
_TERM = re.compile(r"[\"“]([A-Z][A-Z0-9&'./ -]*[A-Z0-9])[\"”]")
#: Spans never rewritten: quotations, markers, inline code.
_PROTECTED = re.compile(r"\{\{.*?\}\}|`[^`]*`|\"[^\"\n]*\"|“[^”\n]*”", re.S)
_LABEL_LINE = re.compile(r"(?m)^\W*(SPECIAL INTERROGATORY|REQUEST FOR (ADMISSION|PRODUCTION)|RESPONSE TO)\b.*$")


def defined_terms(doc: str) -> list[str]:
    """The quoted, capitalized terms the Definitions section defines."""
    m = _DEFS.search(doc)
    if not m:
        return []
    seen: list[str] = []
    for t in _TERM.findall(m.group(1)):
        t = " ".join(t.split())
        if t not in seen and len(t) >= 2:
            seen.append(t)
    return seen


def _defs_start(doc: str) -> int:
    m = _DEFS.search(doc)
    return m.start() if m else -1


_SIGNATURE = re.compile(r"(?m)^\W*Dated:")


def _region(doc: str) -> tuple[int, int]:
    """From the end of the Definitions to the signature block: the set's own
    words, never its caption or the firm's signature lines."""
    m = _DEFS.search(doc)
    if not m:
        return -1, -1
    at = m.end()  # the requests: a definition is never rewritten
    sig = _SIGNATURE.search(doc, at)
    return at, (sig.start() if sig else len(doc))


def _outside(text: str, fn) -> str:  # type: ignore[no-untyped-def]
    out, last = [], 0
    for m in _PROTECTED.finditer(text):
        out.append(fn(text[last : m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _term_pattern(term: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(s|es)?(?![A-Za-z0-9])", re.I)


def enforce_caps(doc: str) -> tuple[str, list[str]]:
    """Every use of a defined term after the Definitions heading in capitals,
    outside quotations and markers. Returns the document and one line per
    term changed (how many uses)."""
    terms = sorted(defined_terms(doc), key=len, reverse=True)
    at, end = _region(doc)
    if not terms or at < 0:
        return doc, []
    head, body, tail = doc[:at], doc[at:end], doc[end:]
    counts: dict[str, int] = {}

    def fix(seg: str) -> str:
        for t in terms:
            pat = _term_pattern(t)

            def up(m: re.Match[str], t: str = t) -> str:
                if m.group(0) == m.group(0).upper():
                    return m.group(0)
                counts[t] = counts.get(t, 0) + 1
                return m.group(0).upper()

            seg = pat.sub(up, seg)
        return seg

    body = _outside(body, fix)
    return head + body + tail, [
        f'defined term "{t}" written in capitals where the set used it in lower case ({n} use(s))'
        for t, n in counts.items()
    ]


def lowercase_uses(doc: str) -> list[str]:
    """Defined terms the set still uses in lower or mixed case after the
    Definitions heading (outside quotations and markers)."""
    at, end = _region(doc)
    if at < 0:
        return []
    found: list[str] = []
    body = doc[at:end]

    def scan(seg: str) -> str:
        for t in defined_terms(doc):
            for m in _term_pattern(t).finditer(seg):
                if m.group(0) != m.group(0).upper() and t not in found:
                    found.append(t)
        return seg

    _outside(body, scan)
    return found


_CAPS = re.compile(r"\b[A-Z][A-Z'&.-]{2,}(?:\s+[A-Z][A-Z'&.-]{2,})*\b")
_COMMON = {
    "YOU", "YOUR", "PLAINTIFF", "PLAINTIFFS", "DEFENDANT", "DEFENDANTS", "NOT", "AND", "THE", "SET", "ONE",
    "TWO", "THREE", "IDENTIFY", "DESCRIBE", "STATE", "ADMIT", "CODE", "CIVIL", "PROCEDURE", "CALIFORNIA", "DEFINITIONS",
}  # fmt: skip


def undefined_caps(doc: str) -> list[str]:
    """Capitalized phrases in the requests that the Definitions never define
    (a set that defines SURVEILLANCE VIDEO and asks for SURVEILLANCE FOOTAGE).
    Party names, labels and the common words of a set are not counted."""
    at, end = _region(doc)
    if at < 0:
        return []
    terms = set(defined_terms(doc))
    term_words = {w for t in terms for w in t.split()}
    parties = set(
        re.findall(
            r"[A-Z][A-Z'&.-]+", " ".join(re.findall(r"(?i)(?:PROPOUNDING|RESPONDING) PARTY:\s*\|?\s*([^|\n]+)", doc))
        )
    )
    body = _LABEL_LINE.sub("", doc[at:end])
    body = re.sub(r"(?m)^#{1,6}\s.*$", "", body)
    out: list[str] = []

    def scan(seg: str) -> str:
        for m in _CAPS.finditer(seg):
            phrase = m.group(0).strip(".-'")
            words = phrase.split()
            if phrase in terms or all(w in _COMMON or w in parties or w in term_words for w in words):
                continue
            if phrase not in out:
                out.append(phrase)
        return seg

    _outside(body, scan)
    return out
