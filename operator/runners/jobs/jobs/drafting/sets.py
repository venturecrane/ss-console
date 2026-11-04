"""Propounded discovery: which sets a request asks for, and defined terms.

**One document per set.** An attorney's email often asks for several sets at
once ("RFA Set One, RFP Set One and SROGs Set One"). Each set is its own paper,
served on its own, with its own caption, signature block and proof of service
(and, for special interrogatories, its own section 2030.050 decision). The job
therefore drafts each requested set as its own unit (``run.py``), and
``variants`` reads, from the request's own words, which sets those are. On
2026-10-12 one compose wrote all three into one Word file with one proof of
service after the last, and a declaration that belonged to none of them.

**Defined terms.** An attorney's rule D6: "Include a Definitions section with
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


#: Nouns that make a lower-case defined term part of a DIFFERENT phrase ("the
#: incident report" is a report, not the INCIDENT; 2026-10-14: the caps pass
#: wrote "INCIDENT report" into two requests for production).
COMPOUND_HEADS = frozenset(
    "report reports date dates scene number numbers history log logs file files form forms photo photos "
    "photograph photographs video videos investigation investigations review reviews notice notices "
    "summary summaries statement statements record records".split()
)


def _in_compound(seg: str, end: int) -> bool:
    """The match ending at ``end`` is followed by a compound head noun."""
    m = re.match(r"[ \t]+([A-Za-z]+)", seg[end:])
    return bool(m and m.group(1) == m.group(1).lower() and m.group(1) in COMPOUND_HEADS)


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

            def up(m: re.Match[str], t: str = t, seg: str = seg) -> str:
                if m.group(0) == m.group(0).upper() or _in_compound(seg, m.end()):
                    return m.group(0)
                counts[t] = counts.get(t, 0) + 1
                return m.group(0).upper()

            seg = pat.sub(up, seg)
        return seg

    body = _outside(body, fix)
    return head + body + tail, [
        f'{n} lower-case use(s) of the defined term "{t}" were capitalized to match its definition'
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
                if m.group(0) != m.group(0).upper() and not _in_compound(seg, m.end()) and t not in found:
                    found.append(t)
        return seg

    _outside(body, scan)
    return found


#: A capitalized phrase: its words on one line (a phrase never spans a line break).
_CAPS = re.compile(r"\b[A-Z][A-Z'&.-]{2,}(?:[ \t]+[A-Z][A-Z'&.-]{2,})*\b")
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
    # The caption, the title, the attorney block and the signature block name
    # the firm and the parties; their words are never a defined term.
    framing = {w.strip(".,;:'&") for w in re.findall(r"[A-Z][A-Z'&.,-]+", doc[: _defs_start(doc)] + doc[end:])}
    body = _LABEL_LINE.sub("", doc[at:end])
    body = re.sub(r"(?m)^#{1,6}\s.*$", "", body)
    body = re.sub(r"(?m)^.*\|.*$", "", body)  # a table row is a caption or a block, never a request
    out: list[str] = []

    def known(w: str) -> bool:
        w = w.strip(".,;:'&-")
        stems = {w, w[:-1] if w.endswith("S") else w, w[:-2] if w.endswith("ES") else w}
        return bool(stems & (_COMMON | parties | term_words | framing)) or not w

    def scan(seg: str) -> str:
        for m in _CAPS.finditer(seg):
            phrase = m.group(0).strip(".-'")
            words = phrase.split()
            if phrase in terms or all(known(w) for w in words):
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


def head_lines(doc: str) -> tuple[int, int, list[str]]:
    """The attorney block: the lines just above the court's name, as (offset
    of the block's first line, offset of the court line, the block's
    non-empty lines). A heading, a table row or a set's title above the court
    line is not part of it and is never rewritten (a compose on 2026-10-13
    put the set's title above the attorney block). (-1, -1, []) when the
    document names no court."""
    pos, start = 0, 0
    lines: list[str] = []
    for ln in doc.splitlines(keepends=True):
        if _COURT.search(ln):
            return start, pos, lines
        pos += len(ln)
        if ln.lstrip().startswith("#") or _SET_NOUN.search(ln) or "|" in ln:
            start, lines = pos, []  # the block starts after this line
        elif ln.strip():
            lines.append(ln.rstrip("\n"))
    return -1, -1, []


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
    start, at, lines = head_lines(doc)
    if at < 0 or not authored:
        return doc, []
    cut = next((i for i, ln in enumerate(lines) if _ATTORNEYS_FOR.match(_plain(ln))), None)
    if cut is None:
        return doc, []  # no "Attorneys for" line: not a shape this rewrites
    if [_plain(x) for x in lines[:cut]] == [_plain(x) for x in authored]:
        return doc, []
    rest = lines[cut:]
    head = "\n".join([*authored, *rest]) + "\n\n"
    return doc[:start] + head + doc[at:], ["the attorney block was printed as the firm authors it"]


def align(doc: str, canon: str, canon_label: str) -> tuple[str, list[str]]:
    """``doc`` with the attorney block, the signature block and every shared
    definition worded as ``canon`` words them. Returns the document and one
    line per change."""
    notes: list[str] = []
    start, at, lines = head_lines(doc)
    cstart, cat, clines = head_lines(canon)
    if at >= 0 and cat >= 0 and clines and [_plain(x) for x in lines] != [_plain(x) for x in clines]:
        doc = doc[:start] + canon[cstart:cat] + doc[at:]
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
    _start, _at, lines = head_lines(canon)
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


# ---- the Dated line --------------------------------------------------------------------

_DATED = re.compile(r"(?m)^(\W*Dated:)[ \t]*([^\n]*)$")


def dated(doc: str, when: str) -> tuple[str, list[str]]:
    """The signature block's Dated line carries the draft's date, the same in
    every set of one request (the skeleton's ``{{FILL: date | today}}``). On
    2026-10-13 two sets carried a NOT IN RECORD marker there and one a blank."""
    m = _DATED.search(doc)
    if not m or m.group(2).strip().strip("`*") == when:
        return doc, []
    return doc[: m.end(1)] + " " + when + doc[m.end(2) :], [f"the Dated line carries the draft's date ({when})"]


# ---- what the pipeline itself may leave, and the set number the request states ----------

_SYSTEM_LINE = re.compile(r"(?im)^[ \t]*#*[ \t]*(?:SECTION TO REPAIR:[ \t]*)?PREAMBLE[ \t]*\n?")


def strip_system_lines(doc: str) -> tuple[str, list[str]]:
    """A heading the pipeline wrote ("PREAMBLE", the repair's section name)
    never reaches a served paper (2026-10-14: one sat above an attorney block)."""
    out, n = _SYSTEM_LINE.subn("", doc)
    return out, ([f"removed {n} system heading line(s)"] if n else [])


_ORDINAL = {"first": "ONE", "second": "TWO", "third": "THREE", "fourth": "FOUR", "fifth": "FIVE"}
_CARDINAL = {"one": "ONE", "two": "TWO", "three": "THREE", "four": "FOUR", "five": "FIVE"}
_STATED = re.compile(
    r"(?i)\b(first|second|third|fourth|fifth)\s+sets?\b|\bsets?\s+(?:no\.?\s*)?(one|two|three|four|five|[1-5])\b"
)
_SET_NO = re.compile(r"(?im)^(\|?\s*SET NO\.?:?\s*\|?\s*)([^|\n]*?)(\s*\|?\s*)$")


def stated_set_number(request: str) -> str | None:
    """The set number the attorney's request states, when it states exactly
    one ("plaintiff's first set", "set one"); None when it states none or
    several (the sets differ and the compose reads them per set)."""
    found = set()
    for m in _STATED.finditer(request or ""):
        if m.group(1):
            found.add(_ORDINAL[m.group(1).lower()])
        else:
            v = m.group(2).lower()
            found.add(_CARDINAL.get(v) or list(_CARDINAL.values())[int(v) - 1])
    return found.pop() if len(found) == 1 else None


def set_number(doc: str, request: str) -> tuple[str, list[str]]:
    """The identification block's SET NO. carries the number the request
    states (its statement of fact is adopted; 2026-10-14: one set carried an
    {{ATTORNEY: confirm the set number}} marker where the request said "first
    set"), and so does a title whose set number is a marker."""
    want = stated_set_number(request)
    if not want:
        return doc, []
    notes: list[str] = []
    m = _SET_NO.search(doc)
    if m and " ".join(m.group(2).strip("` ").split()).upper() != want:
        doc = doc[: m.start(2)] + want + doc[m.end(2) :]
        notes.append(f"set number written as {want}, as the request states it")
    titled = re.sub(r"(?i)(\bSET\s+)`?\{\{[^{}]*\}\}`?", lambda t: t.group(1) + want, doc)
    if titled != doc:
        doc = titled
        notes.append(f"the title's set number written as {want}, as the request states it")
    return doc, notes


def attorneys_for(doc: str) -> tuple[str, list[str]]:
    """The signature block's "Attorney(s) for ..." line reads as the attorney
    block's does (2026-10-14: the caption said "Attorney for Plaintiff", the
    signature "Attorneys for"; the attorney is the firm's only one on it)."""
    _start, _at, lines = head_lines(doc)
    head = next((x for x in lines if _ATTORNEYS_FOR.match(_plain(x))), None)
    s, e = signature(doc)
    if head is None or s < 0:
        return doc, []
    want = _plain(head)
    block = doc[s:e]
    new = re.sub(
        r"(?im)^([ \t>*_`]*)attorneys?\s+for\b[^\n]*$",
        lambda m: m.group(1) + want,
        block,
    )
    if new == block:
        return doc, []
    return doc[:s] + new + doc[e:], [f'the signature block reads "{want}", as the attorney block does']


# ---- the court's particulars the record carries -----------------------------------------

_PARTICULAR = re.compile(r"(?im)^\W*(Judge|Dept\.?|Department|Trial Date)\s*:\s*(.+?)\s*$")
_LABELS_OUT = {"judge": "Judge", "dept": "Dept.", "dept.": "Dept.", "department": "Dept.", "trial date": "Trial Date"}


def court_particulars(digest: str) -> list[str]:
    """The judge, department and trial date as the papers in the digest
    print them, each only when every mention agrees and it is a value (never
    "N/A", never two different values): the record's, or nothing."""
    seen: dict[str, set[str]] = {}
    segments = "\n".join(seg for ln in digest.splitlines() for seg in ln.split("|"))
    for m in _PARTICULAR.finditer(segments):
        key = _LABELS_OUT[m.group(1).lower()]
        value = " ".join(m.group(2).split()).strip(" .")
        if not value or re.fullmatch(r"(?i)n/?a|none|tbd|not set|_+", value):
            continue
        seen.setdefault(key, set()).add(value)
    return [f"{k}: {next(iter(v))}" for k, v in seen.items() if len(v) == 1]


def particulars(doc: str, lines: list[str]) -> tuple[str, list[str]]:
    """The particulars added to the caption's case-number cell, once."""
    if not lines:
        return doc, []
    m = re.search(r"(?m)^(\|[^\n]*\|\s*)(Case No\.[^|\n]*?)(\s*\|)\s*$", doc)
    if not m or all(x in m.group(2) for x in lines):
        return doc, []
    cell = m.group(2).rstrip() + "; " + "; ".join(x for x in lines if x not in m.group(2))
    return doc[: m.start(2)] + cell + doc[m.end(2) :], [
        "the caption carries " + "; ".join(lines) + ", as the papers in the file print them"
    ]


_RECORD_ALIAS = re.compile(r"(?i)\b(?:also\s+)?appears\s+in\s+the\s+record\s+as\b")


def alias_wording(doc: str) -> tuple[str, list[str]]:
    """A definition's alias says "also spelled", never the drafting's own
    "appears in the record as" (2026-10-14, a served paper's Definition 9)."""
    out, n = _RECORD_ALIAS.subn("also spelled", doc)
    return out, (
        [f'"also spelled" written for {n} alias(es) worded as the drafting\'s own "in the record"'] if n else []
    )
