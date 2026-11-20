"""The independent pre-delivery review: the last stage before anything is filed.

The drafter's own audit and repair are the same machinery grading itself. On
2026-10-12 they passed a section 2030.050 declaration whose paragraph 3 swore
the set crossed the 35-interrogatory limit while its paragraph 5 counted
exactly 35, with no prior set served; it reached the attorney. This stage runs
AFTER the documents and the attorney notes are rendered and BEFORE they are
filed, reads what would be delivered (the text of the rendered Word files and
the notes), and grades every finding:

* ``MUST-FIX``: the document would be wrong to hand an attorney: a statement
  that contradicts the document or the record (a declaration, a count, a
  prior-set claim, a date), the request's instruction not followed (the paper,
  the sets asked for), a caption that disagrees with the court's own filings,
  notes that contradict the document, more or fewer than one proof of service
  per served paper.
* ``SHOULD-FIX``: a real defect the attorney should see but can correct in
  review (a defined term in lower case, a capitalized term never defined).
* ``NOTE``: worth the attorney's eye.

Two halves. **Code checks** settle what code can settle, and cannot be
argued with: a declaration present without a proven cumulative count over 35
(``render.decl_decision``, the same rule the attach used) or with a count that
is not the set's own; a served paper without exactly one proof of service; one
file carrying two kinds of set; a requested set with no file; pleading paper
asked for and not delivered; a response set whose responses are not 1..N, one
each, N the served set's count, one document per set the Operator drafts
(``response_findings``, ``served.py``). **A model review** with its own adversarial
prompt (not the drafter's, not the auditor's) reads the request, the matter
record, the record digest and the served discovery papers in the file, the
documents and the notes, and grades the rest with a citation for every
finding; a MUST-FIX without a citation is graded SHOULD-FIX.

Any MUST-FIX ends the job ``failed`` with ``review_held``: nothing is filed,
the firm hears nothing (the broker refuses a failed job's reply), and SMD's
shortfall alert carries the job. ``review.json`` is the record; a resume reads
it and holds again without paying twice. SHOULD-FIX and NOTE findings ride
into the attorney notes.

Since 2026-10-13 the record, the hold and its two ways out are the shared
``proof.review`` (the domain checks and prompts stay here): a ``redo`` reviews
again after a fix, and an SMD release files as it stands with every finding,
the released MUST-FIX ones included, in the attorney notes. A model MUST-FIX
holds only on a check its prompt defines as MUST-FIX-capable (``HOLD_CHECKS``:
defined terms, check 4, and the catch-all, check 10, never hold), and the
review runs on the firm's ``review`` model, else its ``audit`` model, never
the drafter's own ``compose`` model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..proof import review as R
from . import pleading, render, sets
from .outcome import DraftingFailed
from ..text_rules import SENTINEL_LINE as _SENTINEL

MUST, SHOULD, NOTE = R.MUST, R.SHOULD, R.NOTE
GRADES = R.GRADES
REVIEW_MAX_TOKENS = 32_000
DISCOVERY_PAPERS_MAX_CHARS = 300_000
#: REVIEW_PROMPT's MUST-FIX-capable checks; 4 (defined terms) and 10 (anything
#: else) never hold, whatever grade the model writes.
HOLD_CHECKS = (1, 2, 3, 5, 6, 7, 8, 9)
#: PLACE_PROMPT's; 5 (his own text, flagged) never holds.
PLACE_HOLD_CHECKS = (1, 2, 3, 4)
_DECL_2033 = re.compile(r"(?i)declaration\b[^\n]{0,80}2033\.050|2033\.050[^\n]{0,80}declaration")
_PARA5 = re.compile(r"(?i)contains\s+a\s+total\s+of\s+(\d+)\s+specially\s+prepared")
_POS = re.compile(r"(?im)^\W*proof of service\W*$")
_DISCOVERY_PAPER = re.compile(
    r"(?i)(special(?:ly prepared)?|form)\s+interrogatories|requests?\s+for\s+(admission|production)|"
    r"responses?\s+to\s+.{0,60}(interrogatories|requests)"
)


#: One finding (grade, check, finding, cite, by): the shared record's shape.
Finding = R.Finding


@dataclass
class Paper:
    """One document as it would be delivered."""

    key: str  # the unit key ("" for a one-document job)
    label: str  # what the document is ("Special Interrogatories", or the class title)
    text: str  # the rendered Word file's text
    paper: str  # "plain" or "pleading"


REVIEW_PROMPT = """## INDEPENDENT PRE-DELIVERY REVIEW

You are a senior California litigator's most exacting reviewer. A drafting system produced the documents below for an attorney at the firm. They have NOT been delivered. Your review decides whether they may be. You did not write them and you owe them nothing: find what is wrong. Assume the drafter made at least one serious error until you have checked.

Check, at minimum, each of these, against the sources given (the attorney's request, the matter record, the record digest, the served discovery papers in the file):

1. STATEMENTS THAT CONTRADICT THE DOCUMENT OR THE RECORD. Every declaration, verification, recital and count. A section 2030.050 declaration is true only if the CUMULATIVE count of specially prepared interrogatories this party has SERVED on this responding party, this set included, exceeds 35; a prior set counts only if a served copy (or the responding party's responses to it) is in the record. A memo, note, email or task that MENTIONS discovery (including discovery the OTHER side served, or drafts uploaded to a tool) is not a prior set. A declaration whose paragraphs disagree with each other or with the set it is attached to is MUST-FIX.
2. THE REQUEST'S INSTRUCTIONS. Every instruction in the attorney's words: which documents, which sets, which party, the paper (pleading paper when asked), the caption, the topics. Each instruction not carried out is MUST-FIX.
3. THE CAPTION against the court's own filings in the digest (court, county, case number, parties exactly as captioned, attorney block). A disagreement is MUST-FIX unless the record itself disagrees, then NOTE.
4. DEFINED TERMS. Every capitalized defined term used consistently; a term defined and then used in lower case or in an undefined variant, or a capitalized term never defined. SHOULD-FIX.
5. ONE PROOF OF SERVICE PER SEPARATE DOCUMENT, after that document's own signature (and declaration, if any). Two papers in one file, or one proof of service for several papers, is MUST-FIX.
6. THE ATTORNEY NOTES against the documents and the record. A note that contradicts a document (says a term is undefined when it is defined, says no declaration when one is attached, lines that contradict each other, a line that says nothing) is MUST-FIX; so is a note whose cite does not carry what the note says it does (the wrong document, page, paragraph or pleading form section; the attorney relies on these cites). A note that omits something the attorney must know (a held statement, a reserved call) is SHOULD-FIX.
7. CONSISTENCY ACROSS THE DOCUMENTS. Requests in different sets (or the same set) that contradict each other (who sent what to whom, a date, a party, a direction of communication), or that leave an inconsistency the attorney notes say the audit flagged. MUST-FIX.
8. THE REQUEST'S OWN STATEMENTS OF FACT. Where the attorney's request states a fact about the file (for example which side served a document), the notes and the documents adopt it; a note that hedges on it ("on either reading") or contradicts it is MUST-FIX. A note that describes a request in the opposite direction from the attorney's words (who produces what to whom) is MUST-FIX.
9. ARTIFACTS. A request that is only a marker ({{NOT IN RECORD}} standing as the request), stray markup (backticks, brackets) around a slot, a heading or sentinel the system wrote ("=== ... ==="), or the system's own vocabulary in the notes (the job, code, a rule of the system, the auditor, the drafter). MUST-FIX for a marker standing as a request or a sentinel; SHOULD-FIX otherwise.
10. Anything else an attorney would be embarrassed to sign or to have drafted: an invented fact, a request whose premise the record contradicts, a compound request, a wrong party, a wrong date. Grade by consequence.

The CODE FINDINGS block lists what code already found; do not repeat them, and do not contradict them.

GRADES. MUST-FIX: the attorney must not receive this as it is. SHOULD-FIX: a real defect the attorney can correct in review. NOTE: worth the attorney's eye. Every finding cites where it is: the document and its paragraph or item, and the record source (FILE: name, p. N, or the digest line). A finding you cannot cite is not a finding.

OUTPUT, and nothing else: one line per finding, exactly

| <MUST-FIX or SHOULD-FIX or NOTE> | <check number and name> | <the finding, one or two sentences> | <citation> |

then a final line exactly `VERDICT: <n> MUST-FIX, <n> SHOULD-FIX, <n> NOTE`. No preamble, no summary, no em dashes. If you find nothing, write only the VERDICT line with zeros."""


PLACE_PROMPT = """## INDEPENDENT PRE-DELIVERY REVIEW: THE ATTORNEY'S OWN SETS, PLACED IN THE CAPTION

The attorney wrote these discovery sets himself and asked for each to be put in the case caption on the firm's paper. A system did that; nothing has been delivered. Your review decides whether it may be. The REQUESTS ARE HIS: never grade, rewrite or second-guess their wording. Grade only what the system did, and that it changed nothing of his:

1. THE CAPTION against the court's own filings (the caption source in the matter record): court, county, case number, parties exactly as captioned. A disagreement is MUST-FIX.
2. THE ATTORNEY BLOCK, THE TITLE AND THE SIGNATURE LINES: the right attorney and firm, a title that names the right set, party and set number as his document states them, signature lines for the right party. A wrong one is MUST-FIX.
3. HIS TEXT UNCHANGED: every request, definition and line of his set present, in order, word for word. A dropped, altered or added word is MUST-FIX.
4. ONE SET PER DOCUMENT, ONE PROOF OF SERVICE PER DOCUMENT, and the paper the request asked for. MUST-FIX.
5. Anything in his own text an attorney would want flagged before signing (a declaration that contradicts his own count): NOTE only.

The CODE FINDINGS block lists what code already found; do not repeat them. Every finding cites where it is.

OUTPUT, and nothing else: one line per finding, exactly

| <MUST-FIX or SHOULD-FIX or NOTE> | <check number and name> | <the finding> | <citation> |

then a final line exactly `VERDICT: <n> MUST-FIX, <n> SHOULD-FIX, <n> NOTE`. No preamble, no em dashes."""


NOTES_PROMPT = """## THE ATTORNEY NOTES, FROM THE FINAL DOCUMENT: THE BASIS ROWS

The drafting system has finished the documents below. For the attorney's notes, give the basis in the file for each request, as ROWS that code will check word for word against the file. Write no prose, no tables, no other notes: code writes the rest.

For each request in each document whose subject rests on something in the file, one row:
- "document": the document's label exactly as given ("Requests for Admission", ...);
- "request": the request's number (an integer);
- "file": the exact FILE name the digest cites for it;
- "paper": what that file is, in plain words: "served responses", "served set", "unserved draft", "pleading", "court paper", "medical record", "correspondence" or "other". Never call a draft, or the firm's own working copy, served; never call requests responses;
- "quote": the words in that file the request rests on, copied exactly (12 to 300 characters). A quote code cannot find in that file drops the row.

Where the attorney's request states a fact about the file (who handles the matter, which set this is), it is adopted as stated and needs no row.

OUTPUT: JSON only, exactly {"basis": [{"document": ..., "request": ..., "file": ..., "paper": ..., "quote": ...}, ...]}. No preamble."""


def _rows(text: str, hold_checks: tuple[int, ...] = HOLD_CHECKS) -> list[Finding]:
    """The model's rows, graded under the closed-list rule (``proof.review``):
    a MUST-FIX without a citation, or on a check outside ``hold_checks``, is
    SHOULD-FIX."""
    return R.parse(text, hold_checks)


def complete(text: str) -> bool:
    return R.complete(text)


_LABEL_ALONE = re.compile(
    r"(?m)^\W*(SPECIAL INTERROGATORY|REQUEST FOR ADMISSION|REQUEST FOR PRODUCTION)\s+NO\.?\s*\d+\s*:?\s*$"
)
#: The system's own vocabulary, which never belongs in what an attorney reads.
_INTERNAL = re.compile(
    r"(?i)\b(job rule|under the job|the job's|code counts|counted by code|by code|the pipeline|the auditor|"
    r"the drafter|the gate|drafting gate|compose stage|the model)\b"
)
_MARKER_ONLY = re.compile(r"^\s*`?\{\{[^{}]*\}\}`?\s*\.?\s*$")


def _artifact_findings(cls: str, papers: list[Paper], notes_md: str) -> list[Finding]:
    """Assembly and wording defects code can see (the 2026-10-12 review of a
    corrected run): a document's requests landing in the notes, a sentinel in a
    document, a request that is only a marker, stray backticks, the system's
    vocabulary in the notes."""
    out: list[Finding] = []
    if len(_LABEL_ALONE.findall(notes_md)) >= 2 or _SENTINEL.search(notes_md) or _POS.search(notes_md):
        out.append(
            Finding(
                MUST,
                "5 assembly",
                "requests or a proof of service are in the attorney notes, not a document",
                "attorney notes",
                "code",
            )
        )
    m = _INTERNAL.search(notes_md)
    if m:
        out.append(
            Finding(
                MUST,
                "9 notes wording",
                f'the attorney notes use the system\'s own vocabulary: "{m.group(0)}"',
                "attorney notes",
                "code",
            )
        )
    for p in papers:
        if _SENTINEL.search(p.text):
            out.append(Finding(MUST, "9 artifacts", f"{p.label} carries a system sentinel line", p.label, "code"))
        if "`" in p.text:
            out.append(Finding(SHOULD, "9 artifacts", f"{p.label} carries stray backticks", p.label, "code"))
        if cls == "discovery_set":
            lines = [ln for ln in p.text.splitlines() if ln.strip()]
            for i, ln in enumerate(lines[:-1]):
                if _LABEL_ALONE.match(ln) and _MARKER_ONLY.match(lines[i + 1]):
                    out.append(
                        Finding(
                            MUST, "9 artifacts", f"{p.label}: {ln.strip()} is a marker, not a request", p.label, "code"
                        )
                    )
    return out


def _f(grade: str, check: str, finding: str, where: str) -> Finding:
    return Finding(grade, check, finding, where, "code")


def _decl_findings(where: str, text: str, digest: str, corpus: Mapping[str, str]) -> list[Finding]:
    """A section 2030.050 declaration only over a PROVEN count, and counting the
    set it is attached to; a section 2033.050 declaration never."""
    out: list[Finding] = []
    if render.DECL_LINE.search(text):
        d = render.decl_decision(text, digest, corpus)
        if not d.attach:
            out.append(
                _f(
                    MUST,
                    "1 section 2030.050 declaration",
                    f"{where} carries a section 2030.050 declaration, but the proven cumulative count is {d.total} "
                    f"({d.this_set} in this set, {d.prior_special} in served prior sets), not over 35",
                    where,
                )
            )
        m = _PARA5.search(text)
        if m and int(m.group(1)) != d.this_set:
            out.append(
                _f(
                    MUST,
                    "1 section 2030.050 declaration",
                    f"{where}: the declaration counts {m.group(1)}; the set has {d.this_set}",
                    where,
                )
            )
    if _DECL_2033.search(text):
        out.append(_f(MUST, "1 section 2033.050 declaration", f"{where} carries a section 2033.050 declaration", where))
    return out


def _set_findings(where: str, text: str, digest: str, corpus: Mapping[str, str]) -> list[Finding]:
    """One propounded set: one kind per file, the declaration rule, defined terms."""
    out: list[Finding] = []
    kinds = sets.kinds_in(text)
    if len(kinds) > 1:
        out.append(
            _f(
                MUST,
                "5 one paper per set",
                f"{where} carries {len(kinds)} kinds of set in one file: {', '.join(kinds)}",
                where,
            )
        )
    out += _decl_findings(where, text, digest, corpus)
    out += [
        _f(SHOULD, "4 defined terms", f'{where}: the defined term "{t}" is used in lower case', where)
        for t in sets.lowercase_uses(text)
    ]
    out += [
        _f(NOTE, "4 defined terms", f'{where}: "{t}" is capitalized but not defined', where)
        for t in sets.undefined_caps(text, corpus or ())[:12]
    ]
    return out


def _paper_findings(
    cls: str, p: Paper, wants: bool, pleading_authored: bool, digest: str, corpus: Mapping[str, str]
) -> list[Finding]:
    out: list[Finding] = []
    if cls in render.DISCOVERY:
        n = len(_POS.findall(p.text))
        if n != 1:
            out.append(_f(MUST, "5 proof of service", f"{p.label} carries {n} proofs of service, not one", p.label))
    if cls == "discovery_set":
        out += _set_findings(p.label, p.text, digest, corpus)
    if wants and p.paper != "pleading":
        why = "the firm has no pleading paper authored" if not pleading_authored else "it was rendered on plain paper"
        out.append(
            _f(
                MUST,
                "2 the request's paper",
                f"the request asks for pleading paper; {p.label} is not ({why})",
                "the request",
            )
        )
    return out


def _asked_findings(asked: list[Any], papers: list[Paper]) -> list[Finding]:
    have = {sets.kinds_in(p.text)[0] for p in papers if sets.kinds_in(p.text)}
    return [
        _f(MUST, "2 the sets asked for", f"the request asks for {u.label}; no document carries them", "the request")
        for u in asked
        if u.key not in have
    ]


#: A served set's request label, as a response restates it (``served.SET_TYPES``).
_RESPONSE_LABELS = {
    "special_interrogatories": r"SPECIAL(?:LY\s+PREPARED)?\s+INTERROGATOR(?:Y|IES)",
    "form_interrogatories": r"FORM\s+INTERROGATOR(?:Y|IES)",
    "requests_for_production": r"(?:REQUEST\s+FOR\s+PRODUCTION(?:\s+OF\s+DOCUMENTS)?|DEMAND\s+FOR\s+(?:PRODUCTION|INSPECTION))",
    "requests_for_admission": r"REQUEST\s+FOR\s+ADMISSION",
}
_NO = r"\s+NO\.?\s*(\d+(?:\.\d+)?)"
#: A response HEADING, not a sentence: a line that starts "RESPONSE" and is
#: either numbered ("RESPONSE TO SPECIAL INTERROGATORY NO. 3:", "Response No.
#: 3.", "RESPONSE TO ... NO. 12(a):") or exactly "RESPONSE" with optional
#: closing punctuation. At most ``_HEADING_WORDS`` words; after its number only
#: a lettered subpart and closing punctuation. "Response to this interrogatory
#: will be supplemented." is a sentence, never a response.
_RESPONSE_HEAD = re.compile(
    r"(?i)^[\W_]*RESPONSE(?:\s+TO\b[^\n.:]{0,80}?)?\s+NO\.?\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<sub>\(\s*[a-z0-9]{1,3}\s*\))?"
    r"\s*[:.]?[\s*_]*$"
)
_RESPONSE_BARE = re.compile(r"(?i)^[\W_]*RESPONSE\s*[:.]?[\s*_]*$")
_HEADING_WORDS = 12


def _heading(ln: str) -> tuple[str, bool] | None:
    """(number or "", is a subpart) for a response heading line, else None."""
    if len(ln.split()) > _HEADING_WORDS:
        return None
    m = _RESPONSE_HEAD.match(ln)
    if m:
        return m.group("n"), bool(m.group("sub"))
    return ("", False) if _RESPONSE_BARE.match(ln) else None


def _scan(text: str, label: str) -> tuple[list[str], list[str], dict[str, str]]:
    """(restated request numbers, response numbers, restated text by number),
    in document order. Tolerant of how a firm words its labels, strict on the
    numbers: a response heading (``_heading``) takes its own number, or the
    number of the request restated just above it ("RESPONSE:"); a restated
    request is a line starting with the set's own label and its number, its
    text the lines up to the next heading. A lettered subpart (12(a)) belongs
    to its number and is never a second response."""
    requests: list[str] = []
    responses: list[str] = []
    restated: dict[str, list[str]] = {}
    req = re.compile(rf"(?i)^[\W_]*(?:{label}){_NO}\s*(?:\(\s*[a-z0-9]{{1,3}}\s*\))?\s*[:.]?[\s*_]*")
    current: str | None = None
    for ln in text.splitlines():
        h = _heading(ln)
        if h is not None:
            current = None
            n, sub = h
            n = n or (requests[-1] if requests else "")
            if n and not (sub and responses and responses[-1] == n):
                responses.append(n)
            continue
        r = req.match(ln)
        if r:
            n = r.group(1)
            if not (requests and requests[-1] == n):
                requests.append(n)
            current = n
            restated.setdefault(n, []).append(ln[r.end() :])
            continue
        if current is not None:
            restated[current].append(ln)
    return requests, responses, {k: " ".join(" ".join(v).split()) for k, v in restated.items()}


def _sequence_problems(got: list[str], n: int, form: bool, what: str) -> list[str]:
    """``got`` is 1..n in order, each once (a form set's dotted numbers: n of
    them, each once, ascending)."""
    out: list[str] = []
    dup = sorted({x for x in got if got.count(x) > 1}, key=_num_key)
    out += [f"two or more {what}s to number {x}" for x in dup]
    if form:
        if len(set(got)) != n:
            out.append(f"{len(set(got))} {what}s, but the served set has {n}")
        if got != sorted(got, key=_num_key):
            out.append(f"the {what}s are out of order")
        return out
    want = [str(i) for i in range(1, n + 1)]
    missing = [x for x in want if x not in got]
    extra = sorted({x for x in got if x not in want}, key=_num_key)
    out += [f"no {what} to number {x}" for x in missing[:12]]
    if len(missing) > 12:
        out.append(f"and {len(missing) - 12} more numbers with no {what}")
    out += [f"a {what} numbered {x}, past the served set's {n}" for x in extra[:12]]
    if not dup and not missing and not extra and got != want:
        out.append(f"the {what}s are out of order")
    return out


def _num_key(x: str) -> tuple[int, ...]:
    return tuple(int(p) for p in x.split("."))


def _form_findings(p: Paper, s: Mapping[str, Any]) -> list[Finding]:
    """A form set whose plan carries its checked numbers, checked in code
    against the official form (``forms.checks.structure``): exactly the
    checked numbers in the form's order, each restated as the catalogue
    prints it, every lettered subpart present per item on its own line."""
    from .forms import catalog, checks

    cat = catalog.load(s.get("form") or catalog.DISC_001, s.get("form_revision") or catalog.CURRENT)
    found = checks.structure(p.text, list(s["checked"]), cat)
    return [_f(MUST, "1 response numbering", f"{p.label}: {x.number}: {x.detail}", p.label) for x in found]


def response_findings(papers: list[Paper], served: Mapping[str, Mapping[str, Any]]) -> list[Finding]:
    """A response set, in code, before anything is filed: the served requests
    restated 1..N with no gap and in order (when the document restates them),
    each restated word for word as served (whitespace aside), exactly one
    response to each, and N the served set's request count (``served.py``).
    Each failure is a MUST-FIX: the job holds for SMD. (One document per
    served set is the plan's own shape, ``served.plan_units``: no check here.)"""
    out: list[Finding] = []
    for p in papers:
        s = served.get(p.key)
        if s is None:
            out.append(_f(MUST, "2 the served sets", f"{p.label} answers no served set", p.label))
            continue
        label, n, form = _RESPONSE_LABELS[s["set_type"]], int(s["count"]), s["set_type"] == "form_interrogatories"
        if form and s.get("checked"):
            out += _form_findings(p, s)
            continue
        requests, responses, restated = _scan(p.text, label)
        problems = _sequence_problems(responses, n, form, "response")
        if requests:
            problems += _sequence_problems(requests, n, form, "restated request")
        given = {str(k): " ".join(str(v).split()) for k, v in (s.get("requests") or {}).items()}
        problems += [
            f"request {k} is not restated as served" for k, t in restated.items() if k in given and t != given[k]
        ]
        out += [_f(MUST, "1 response numbering", f"{p.label}: {x}", p.label) for x in problems]
    return out


def code_findings(
    cls: str,
    papers: list[Paper],
    request: str,
    digest: str,
    corpus: Mapping[str, str],
    pleading_authored: bool,
    theirs: bool = False,
    notes_md: str = "",
    served: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Finding]:
    """What code can settle, graded. ``theirs``: the documents are the
    attorney's own (place.py): what they SAY (a declaration, a defined term) is
    his and is reported as a NOTE; what the job did to them is graded as ever.
    ``served``: a response job's served sets by unit key (``served.review_map``),
    whose numbering is checked (``response_findings``)."""
    wants = pleading.wants_pleading(request)
    out: list[Finding] = []
    for p in papers:
        out += _paper_findings(cls, p, wants, pleading_authored, digest, corpus)
    out += _artifact_findings(cls, papers, notes_md)
    if cls == "discovery_response" and served is not None:
        out += response_findings(papers, served)
    if theirs:
        for f in out:
            if f.check[:2] in ("1 ", "4 "):
                f.grade = NOTE
                f.finding = f"in the attorney's own text (not changed): {f.finding}"
    elif cls == "discovery_set":
        out += _asked_findings(sets.variants(request), papers)
    return out


def discovery_papers(corpus: Mapping[str, str]) -> str:
    """The served discovery papers in the file (either side's sets and
    responses), whole, under a cap: what a prior-set claim is checked against."""
    parts, size = [], 0
    for name, text in corpus.items():
        head = text[:4000]
        if not _DISCOVERY_PAPER.search(head):
            continue
        piece = f"=== FILE: {name} ===\n{text}"
        if size + len(piece) > DISCOVERY_PAPERS_MAX_CHARS:
            parts.append(f"=== FILE: {name} === (not included: over the review's size cap)")
            continue
        parts.append(piece)
        size += len(piece)
    return "\n\n".join(parts) or "(none in the file)"


def review_user(
    request: str, context: list[str], papers: list[Paper], notes_md: str, findings: list[Finding], served: str
) -> str:
    docs = "\n\n".join(
        f"=== DOCUMENT {i}: {p.label} (as delivered, on {p.paper} paper) ===\n{p.text}" for i, p in enumerate(papers, 1)
    )
    code = "\n".join(f"- {f.grade} | {f.check} | {f.finding}" for f in findings) or "(none)"
    return (
        "THE ATTORNEY'S REQUEST (verbatim; it is the instruction, never a source of facts):\n\n"
        + request
        + "\n\n---\n\n"
        + "\n\n".join(context)
        + "\n\n---\n\nTHE SERVED DISCOVERY PAPERS IN THE FILE (whole):\n\n"
        + served
        + "\n\n---\n\nCODE FINDINGS:\n"
        + code
        + "\n\n---\n\n"
        + docs
        + "\n\n---\n\n=== THE ATTORNEY NOTES (as delivered) ===\n"
        + notes_md
    )


def run_model(
    doorway: Any,
    model: str,
    system_digest: str,
    user: str,
    key: str,
    prompt: str | None = None,
    hold_checks: tuple[int, ...] = HOLD_CHECKS,
) -> list[Finding]:
    """The model review: one call, retried once when it does not finish
    (``proof.review.run_model``), graded under the closed-list rule."""
    from ..demand.gapaudit import output_max
    from .compose import DraftingError

    system = (
        (prompt or REVIEW_PROMPT)
        + "\n\n---\n\n## THE RECORD DIGEST (every cite in it is the record)\n\n"
        + system_digest
    )
    try:
        return R.run_model(
            doorway, model, system, user, key, hold_checks, max_tokens=output_max(model, REVIEW_MAX_TOKENS)
        )
    except R.ReviewIncomplete:
        raise DraftingError("the pre-delivery review did not complete") from None


def model_for(firm: Any) -> str:
    """The firm's ``review`` model when authored, else its ``audit`` model:
    never ``compose``, the drafter's own (2026-10-13)."""
    from ..demand.review import model_for as shared

    return shared(firm)


def decide(
    data: Path, job_dir: Path, fresh: Callable[[], tuple[list[Finding], str]], log: Callable[[str], None]
) -> dict[str, Any]:
    """The review's decision for this attempt: SMD's redo or release applied,
    the review run when there is no record, and a hold raised as
    ``review_held`` (failed, resumable, nothing filed)."""
    from ..demand.review import decide as shared

    status, rec = shared(data, job_dir, fresh, lane="drafting")
    log(f"  review: {rec['must_fix']} must-fix, {rec['should_fix']} should-fix, {rec['notes']} note(s): {status}")
    if status == "held":
        raise DraftingFailed(held_reason(rec))
    return rec


def final_notes(
    doorway: Any,
    model: str,
    digest: str,
    papers: list[Paper],
    drafter_tables: str,
    open_items: list[str],
    key: str,
    facts: list[str] | None = None,
    refused: list[str] | None = None,
) -> str:
    """The end tables rewritten from the FINAL documents (never intermediate
    state). ``facts``: what the request and the matter record settle (who asked,
    the responsible attorney, whose attorney block), stated so the notes never
    hedge on them. ``refused``: what a previous answer carried that the notes
    may not (``wording.notes_findings``); the rewrite is told."""
    from ..demand.gapaudit import output_max
    from .compose import DraftingError

    docs = "\n\n".join(f"=== FINAL DOCUMENT: {p.label} ===\n{p.text}" for p in papers)
    user = (
        docs
        + "\n\n---\n\n=== THE FACTS (from the request and the matter record; state them as facts) ===\n"
        + ("\n".join(f"- {x}" for x in facts or []) or "(none)")
        + "\n\n---\n\n=== THE DRAFTER'S END TABLES (about an earlier version) ===\n"
        + (drafter_tables or "(none)")
        + "\n\n---\n\n=== THE AUDITOR'S OPEN ITEMS (about an earlier version) ===\n"
        + ("\n".join(f"- {x}" for x in open_items) or "(none)")
    )
    if refused:
        user += (
            "\n\n---\n\nYOUR PREVIOUS ANSWER WAS REFUSED for carrying: "
            + "; ".join(refused)
            + ". Write the tables again without any of these."
        )
    system = NOTES_PROMPT + "\n\n---\n\n## THE RECORD DIGEST\n\n" + digest
    tag = "r" if refused else ""
    for attempt in (1, 2):
        r = doorway.call(
            "notes",
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=output_max(model, REVIEW_MAX_TOKENS),
            stream=True,
            effort="high",
            custom_id=f"notes-{key}{tag}-{attempt}",
        )
        if r.stop_reason != "max_tokens" and r.text.strip():
            return r.text.strip()
    raise DraftingError("the final attorney notes did not complete")


def save(data: Path, findings: list[Finding], model: str = "") -> dict[str, Any]:
    return R.save(data, findings, model=model, lane="drafting")


def load(data: Path) -> dict[str, Any] | None:
    return R.load(data)


def notes_section(rec: dict[str, Any] | None) -> list[str]:
    """The review's SHOULD-FIX and NOTE findings for the attorney notes, and
    the MUST-FIX ones too when SMD released past them."""
    return R.notes_section(rec)


def held_reason(rec: dict[str, Any]) -> str:
    return R.held_reason(rec)
