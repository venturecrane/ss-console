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


# ---- one front matter across the sets of one request ---------------------------------------
#
# The sets one request asks for are served together on the same party: the
# attorney block, the signature block and every term they share mean the same
# thing in each. On 2026-10-13 three sets drafted by three composes defined
# LOCATION three ways and printed the attorney block in two wordings. The
# first set finished is the canon; each later set is aligned to it by code
# before its gate (``finish._gate``), and told it before its compose.

_COURT = re.compile(r"(?i)superior\s+court|court\s+of\s+the\s+state")
#: Where a set's own words end and the attachments begin (render.DECL_LINE / POS_LINE).
_ATTACHMENT = re.compile(
    r"(?im)^\W*(?:declaration for additional discovery|declaration\b.*2030\.050|proof of service\b)"
)
#: Words that make a definition belong to one kind of set ("in responding to
#: these interrogatories"): never copied into another kind.
_SET_NOUN = re.compile(
    r"(?i)\binterrogator(?:y|ies)\b|\brequests?\s+for\s+(?:admission|production)|\bthese\s+requests\b"
)
_ANY_LABEL = re.compile(r"(?im)^\W*(?:SPECIAL INTERROGATORY|REQUEST FOR (?:ADMISSION|PRODUCTION))\b")
_DEF_PARA = re.compile(r"(?ms)^(\s*\d+\.\s+)([\"“]([A-Z][A-Z0-9&'./ -]*[A-Z0-9])[\"”].*?)(?=\n\s*\n|\Z)")
_ATTORNEYS_FOR = re.compile(r"(?i)^\W*attorneys?\s+for\b")


def _plain(line: str) -> str:
    return " ".join(re.sub(r"[`*_]", "", line).split())


def _defs_section(doc: str) -> tuple[int, str] | None:
    """The Definitions section's body and its offset, ending at the first
    request label when no heading separates them."""
    m = _DEFS.search(doc)
    if not m:
        return None
    body = m.group(1)
    lab = _ANY_LABEL.search(body)
    return m.start(1), body[: lab.start()] if lab else body


def definition_texts(doc: str) -> dict[str, tuple[int, int, str]]:
    """Each defined term's definition paragraph (after its number): term ->
    (start, end, text), offsets into ``doc``."""
    got = _defs_section(doc)
    if got is None:
        return {}
    base, body = got
    out: dict[str, tuple[int, int, str]] = {}
    for p in _DEF_PARA.finditer(body):
        term = " ".join(p.group(3).split())
        out.setdefault(term, (base + p.start(2), base + p.end(2), p.group(2).strip()))
    return out


def head_lines(doc: str) -> tuple[int, list[str]]:
    """The attorney block: the lines above the court's name, as (offset where
    the court line starts, the block's non-empty lines). (-1, []) when the
    document names no court, or when a heading or a set's title sits above it
    (that is not an attorney block, and it is never rewritten)."""
    pos = 0
    lines: list[str] = []
    for ln in doc.splitlines(keepends=True):
        if _COURT.search(ln):
            if any(x.lstrip().startswith("#") or _SET_NOUN.search(x) or "|" in x for x in lines):
                return -1, []
            return pos, lines
        if ln.strip():
            lines.append(ln.rstrip("\n"))
        pos += len(ln)
    return -1, []


def signature(doc: str) -> tuple[int, int]:
    """The signature block: from its ``Dated:`` line to the end of the set,
    stopping before any declaration or proof of service a model wrote (the
    job's own are attached later; one must never travel to another set)."""
    m = _SIGNATURE.search(doc)
    if not m:
        return -1, -1
    att = _ATTACHMENT.search(doc, m.end())
    return m.start(), len(doc[: att.start() if att else len(doc)].rstrip())


def authored_head(doc: str, authored: list[str]) -> tuple[str, list[str]]:
    """The attorney block printed exactly as the firm authored it: the lines
    above the "Attorneys for" line replaced by the authored lines when they
    differ (in words, ignoring markup)."""
    at, lines = head_lines(doc)
    if at < 0 or not authored:
        return doc, []
    cut = next((i for i, ln in enumerate(lines) if _ATTORNEYS_FOR.match(_plain(ln))), None)
    if cut is None:
        return doc, []  # no "Attorneys for" line: not a shape this rewrites
    if [_plain(x) for x in lines[:cut]] == [_plain(x) for x in authored]:
        return doc, []
    rest = lines[cut:]
    head = "\n".join([*authored, *rest]) + "\n\n"
    return head + doc[at:], ["the attorney block was printed as the firm authors it"]


def align(doc: str, canon: str, canon_label: str) -> tuple[str, list[str]]:
    """``doc`` with the attorney block, the signature block and every shared
    definition worded as ``canon`` words them. Returns the document and one
    line per change."""
    notes: list[str] = []
    at, lines = head_lines(doc)
    cat, clines = head_lines(canon)
    if at >= 0 and cat >= 0 and [_plain(x) for x in lines] != [_plain(x) for x in clines]:
        doc = canon[:cat] + doc[at:]
        notes.append(f"attorney block worded as in the {canon_label}")
    mine, theirs = definition_texts(doc), definition_texts(canon)
    for term in sorted(set(mine) & set(theirs), key=lambda t: -mine[t][0]):
        a, b, text = mine[term]
        if _SET_NOUN.search(theirs[term][2]) or _SET_NOUN.search(text):
            continue  # worded for its own kind of set; the review reads it
        if _plain(text) != _plain(theirs[term][2]):
            doc = doc[:a] + theirs[term][2] + doc[b:]
            notes.append(f'"{term}" defined in the same words as in the {canon_label}')
    s, e = signature(doc)
    cs, ce = signature(canon)
    if s >= 0 and cs >= 0 and _plain(doc[s:e]) != _plain(canon[cs:ce]):
        doc = doc[:s] + canon[cs:ce] + doc[e:]
        notes.append(f"signature block worded as in the {canon_label}")
    return doc, notes


def front_matter(canon: str) -> str:
    """What a later set's compose is told of the first: its attorney block,
    Definitions and signature block, verbatim."""
    at, lines = head_lines(canon)
    got = _defs_section(canon)
    s, e = signature(canon)
    parts = []
    if lines:
        parts.append("ATTORNEY BLOCK:\n" + "\n".join(lines))
    if got:
        parts.append("DEFINITIONS:\n" + got[1].strip())
    if s >= 0:
        parts.append("SIGNATURE BLOCK:\n" + canon[s:e].strip())
    return "\n\n".join(parts)


# ---- the compound marker names the variant's own section -------------------------------

_COMPOUND = re.compile(r"\{\{ATTORNEY:\s*compound under (?:section\s+)?\d{4}\.\d{3}\([a-z]\)\??\s*\}\}", re.I)
#: The section that bars a compound request in each variant. A request for
#: production has none: its rule is the category's reasonable particularity.
COMPOUND_MARKER = {
    "special_interrogatories": "{{ATTORNEY: compound under section 2030.060(f)?}}",
    "requests_for_admission": "{{ATTORNEY: compound under section 2033.060(f)?}}",
    "requests_for_production": "{{ATTORNEY: one category, or two under section 2031.030(c)(1)?}}",
}


def compound_markers(doc: str) -> tuple[str, list[str]]:
    """Each "compound under section ..." marker naming the section of the set's
    own variant (the compose prompt names the interrogatory section for all
    three; on 2026-10-13 a set of requests for production carried it)."""
    kinds = kinds_in(doc)
    if len(kinds) != 1:
        return doc, []
    want = COMPOUND_MARKER[kinds[0]]
    n = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal n
        if m.group(0) != want:
            n += 1
        return want

    doc = _COMPOUND.sub(sub, doc)
    return doc, [f"compound-request marker names this set's own rule ({n} marker(s))"] if n else []
