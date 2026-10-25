"""Two of ``review_policy``'s steps, kept in their own module (the 500-line
module ceiling): 3b deletes the system's own words from the entries, 5
discloses two confirmed record statements that disagree. Both settle by
deletion or disclosure only; no word is written. The WHY and the order are
in ``review_policy``'s docstring (2026-10-24, the Pamplona hold).
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from ..proof import review
from .base import StageRun
from .review_policy import _DATE, _EXHIBIT_PAGE, _QUOTED, Outcome, _norm, _phrases, _text, check_no

# ---- step 3b: the system's own words, deleted from the entries --------------------
#: A sentence boundary inside one paragraph line (``\x00`` closes a held
#: citation). Abbreviations a record writes mid-sentence ("Dr. Roe") are not
#: boundaries (``_split``).
_BOUNDARY = re.compile(r"(?<=[.!?\x00])\s+")
#: Credentials and clock marks too: "Thomas Roe, M.D. at 12:41 PM" is one
#: sentence; split at "M.D." the Pamplona system sentence matched neither half
#: and a deletion would have left "at 12:41 PM, reads as follows." (2026-10-25).
_ABBREV = re.compile(
    r"(?i)(?:^|\s|,)(?:dr|mr|mrs|ms|st|vs|no|pp?|approx|e\.g|i\.e|jr|sr"
    r"|m\.d|d\.o|r\.n|p\.a|n\.p|ph\.d|a\.m|p\.m|l\.v\.n|f\.n\.p|d\.p\.m)\.$"
)
#: A list item's number ("7.") opens its sentence, never ends one (Pamplona
#: 2026-10-25: "7. Remaining systems as printed." split in two and the quote
#: matched neither piece).
_LIST_NO = re.compile(r"^\d{1,2}\.$")
_HELD = re.compile(r"\x00(\d+)\x00")
#: An entry's date line (exactly a date: the entry's first line).
_DATE_LINE = re.compile(r"^\d{2}/\d{2}/\d{4}\s*$")
#: A fragment of a quoted phrase shorter than this never deletes anything.
_MIN_FRAGMENT = 6


#: Every quote mark read as one: a reviewer who writes 'Findings' quotes the
#: document's "Findings" (Pamplona 2026-10-25).
_QUOTES = str.maketrans(dict.fromkeys('"“”‘’`', "'"))


def _flat(text: str) -> str:
    return " ".join(text.split()).lower().translate(_QUOTES)


def _fragments(phrase: str) -> list[str]:
    """A quoted phrase as the pieces it must contain, lower case, spaced
    once, quote marks made one: a reviewer's ellipsis ("printed after ...
    reads as follows") splits it. Pieces shorter than ``_MIN_FRAGMENT`` are
    dropped; none left, no deletion."""
    out = [_flat(p).strip(" ,;:") for p in re.split(r"\.\.\.|…", phrase)]
    return [p for p in out if len(p) >= _MIN_FRAGMENT]


def _has(text: str | None, frags: list[str]) -> bool:
    if text is None or not frags:
        return False
    t = _flat(text)
    return all(f in t for f in frags)


def _body_range(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) line indices of the entries body: after "## Medical
    Chronology", before the next "## " heading. Limitations and the Exhibit
    List are never inside it."""
    start = next((i for i, ln in enumerate(lines) if ln.strip() == CL.BODY_START), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    return start + 1, end


def _split(line: str) -> list[tuple[str, bool]]:
    """A paragraph line as (piece, is_citation) in order: each citation its
    own piece, sentences split on terminal punctuation but never after an
    abbreviation."""
    cites: list[str] = []

    def hold(m: re.Match[str]) -> str:
        cites.append(m.group(0))
        return f" \x00{len(cites) - 1}\x00 "

    raw: list[str] = []
    cur = ""
    for part in _BOUNDARY.split(CL.CITE.sub(hold, line.strip()).strip()):
        cur = f"{cur} {part}" if cur else part
        if not _ABBREV.search(cur) and not _LIST_NO.match(cur.strip()):
            raw.append(cur)
            cur = ""
    if cur:
        raw.append(cur)
    out: list[tuple[str, bool]] = []
    for r in raw:
        for bit in re.split(r"(\x00\d+\x00)", r):
            bit = " ".join(bit.split())
            if bit:
                m = _HELD.fullmatch(bit)
                out.append((cites[int(m.group(1))], True) if m else (bit, False))
    return out


def _cut(line: str, frags: list[str]) -> str | None:
    """The line with every sentence holding the phrase deleted. A citation
    covers the sentences since the one before it: it stays while any of
    them remains, and goes with the last. None when no sentence is left
    (the paragraph goes)."""
    groups: list[list[tuple[str, bool]]] = [[]]
    for p in _split(line):
        groups[-1].append(p)
        if p[1]:
            groups.append([])
    kept: list[tuple[str, bool]] = []
    for g in groups:
        texts = [p for p in g if not p[1]]
        left = [p for p in texts if not _has(p[0], frags)]
        if left or not texts:
            kept += [p for p in g if p[1] or not _has(p[0], frags)]
    if not any(not p[1] for p in kept):
        return None
    return line if len(kept) == len(_split(line)) else " ".join(p[0] for p in kept)


#: A provider heading's trailing clause that names a role or an inbox, not a
#: provider ("<Clinic> Surgery, hand RN triage supporting <Doctor>, MD" is a
#: message pool, Pamplona 2026-10-25). Cut, the name
#: before the comma stays; a credential after a name ("Jane Roe, RN") is no role.
_ROLE_CLAUSE = re.compile(
    r"^(?P<keep>[^,|()]*[^,|()\s])\s*,\s*(?P<role>[^|]*\b(?:triage|pool|supporting|on behalf of|inbox"
    r"|nurse line|documenting|care team|staff)\b[^|]*?)\s*$",
    re.I,
)


def _heading_cut(line: str, frags: list[str]) -> str:
    """A provider heading with each parenthetical the phrase overlaps
    deleted, with the space before it; with none, a trailing role clause the
    phrase overlaps (``_ROLE_CLAUSE``) is cut. The provider's name and the
    section after " | " stay; the line itself is never deleted."""
    low = line.lower().translate(_QUOTES)  # same length as `line`: spans index it
    spans: list[tuple[int, int]] = []
    for f in frags:
        i = low.find(f)
        while i >= 0:
            spans.append((i, i + len(f)))
            i = low.find(f, i + 1)
    out = line
    for m in reversed(list(re.finditer(r"\s*\([^()]*\)", line))):
        if any(a < m.end() and m.start() < b for a, b in spans):
            out = out[: m.start()] + out[m.end() :]
    if out != line or not spans:
        return out
    head, sep, rest = line.partition(" | ")
    m = _ROLE_CLAUSE.match(head)
    if m and any(m.start("role") < b and a < len(head) for a, b in spans):
        return m.group("keep") + sep + rest
    return out


def _content(ln: str | None) -> bool:
    return ln is not None and bool(CL.CITE.search(ln))


def _only_content(lines: list[str | None], i: int, lo: int, hi: int) -> bool:
    """True when line i is the only content paragraph (a cited one) left in
    its entry. A line before the first entry has no entry to empty."""
    s = next((j for j in range(i, lo - 1, -1) if _DATE_LINE.match(lines[j] or "")), None)
    if s is None:
        return False
    e = next((j for j in range(i + 1, hi) if _DATE_LINE.match(lines[j] or "")), hi)
    return not any(j != i and _content(lines[j]) for j in range(s, e))


def _scrub_lines(
    lines: list[str | None], lo: int, hi: int, frags: list[str], edits: list[tuple[str, str | None]]
) -> None:
    """Every occurrence of the phrase in lines [lo, hi), deleted in place (a
    deleted line becomes None); `edits` collects (old, new) per line."""
    for i in range(lo, hi):
        ln = lines[i]
        if ln is None or not _has(ln, frags) or _DATE_LINE.match(ln):
            continue
        if i > lo and _DATE_LINE.match(lines[i - 1] or ""):
            new: str | None = _heading_cut(ln, frags)  # the entry's provider heading
        else:
            new = _cut(ln, frags)
            if new is None and _content(ln) and _only_content(lines, i, lo, hi):
                continue  # the entry's only content paragraph: left, the finding stays MUST
        if new != ln:
            edits.append((ln, new))
            lines[i] = new


def _scrubbable(f: review.Finding) -> bool:
    """A MUST check 3 finding on the chronology itself: the model's, or
    code's on "the chronology" or an entry (never a file name or the
    worksheet, which no edit here reaches)."""
    if f.grade != review.MUST or check_no(f) != 3:
        return False
    return f.by == "model" or f.cite == "the chronology" or f.cite.startswith("entry ")


def scrub_system(sr: StageRun, doc: Path, out: Outcome, stats: dict[str, int]) -> None:
    """Step 3b: for each check 3 MUST-FIX left, every quoted phrase is
    deleted from the entries body (see the module docstring). Settled, a
    NOTE, only when every phrase was in the body before and none is after."""
    raw = doc.read_text(encoding="utf-8").split("\n")
    rng = _body_range(raw)
    if rng is None:
        return
    lo, hi = rng
    lines: list[str | None] = list(raw)
    edits: list[tuple[str, str | None]] = []
    body = list(raw[lo:hi])  # as it stood before any deletion: a finding's phrase was in the entries
    for k, f in enumerate(out.findings):
        if not _scrubbable(f):
            continue
        frags = [_fragments(p) for p in _phrases(f)]
        if not frags or not all(frags):
            continue
        if not all(any(_has(ln, fr) for ln in body) for fr in frags):
            continue  # a phrase not in the entries (Limitations, a table): not this step's to edit
        for fr in frags:
            _scrub_lines(lines, lo, hi, fr, edits)
        if any(_has(ln, fr) for ln in lines[lo:hi] for fr in frags):
            continue
        out.findings[k] = replace(f, grade=review.NOTE, finding=f"removed before delivery, system wording: {f.finding}")
        stats["system_scrubbed"] += 1
    if not edits:
        return
    out.renamed = _rename_in_tables(lines, lo, edits)
    doc.write_text(re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln is not None)), encoding="utf-8")
    out.changed = True
    _sync_edits(sr, edits)
    sr.log(f"review: {len(edits)} line(s) of system wording deleted from the entries")
    if out.renamed:
        sr.log(f"review: {out.renamed} summary-table cell(s) carry the shortened provider heading")


def _rename_in_tables(lines: list[str | None], lo: int, edits: list[tuple[str, str | None]]) -> int:
    """A provider heading the scrub shortened is shortened in the summary
    tables before the entries too (the Treatment Timeline's provider cell), so
    the document names one provider one way. Exact cells only; the count."""
    names = {}
    for old, new in edits:
        if new is not None and " | " in old and " | " in new:
            a, b = old.partition(" | ")[0].strip(), new.partition(" | ")[0].strip()
            if a != b and b:
                names[a] = b
    n = 0
    for i in range(lo):
        ln = lines[i]
        if ln is None or not ln.lstrip().startswith("|"):
            continue
        for a, b in names.items():
            if f"| {a} |" in ln:
                lines[i] = ln = ln.replace(f"| {a} |", f"| {b} |")
                n += 1
    return n


def _sync_edits(sr: StageRun, edits: list[tuple[str, str | None]]) -> None:
    """Best effort, as ``review_policy._sync_entries``: each edited line, where it stands
    verbatim in ``entries_scoped_final.md``, edited there too."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not p.is_file():
        return
    lines: list[str | None] = list(p.read_text(encoding="utf-8").split("\n"))
    for old, new in edits:
        lines = [new if ln == old else ln for ln in lines]
    p.write_text(re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln is not None)), encoding="utf-8")


# ---- step 5: two confirmed statements that differ, disclosed ----------------------
DIFFER_NOTE = "the records differ on this point; both statements were confirmed on their cited pages: "
#: A disagreement the lane could not resolve to two confirmed claims: the
#: attorney's to check, and the wording claims nothing was confirmed.
FLAG_NOTE = (
    "flagged for the attorney: the pre-delivery review read these statements as possibly "
    "disagreeing; please compare them against the cited pages: "
)
_CONFIRMED = ("SUPPORTED", "SUPPORTED_WIDENED")


def confirmed_claims(sr: StageRun, md: str) -> list[tuple[str, dict[str, Any]]]:
    """(entry date, claim) for every live claim whose latest real row in
    ``audit-results.jsonl`` is SUPPORTED or SUPPORTED_WIDENED; the date is
    '' before the first entry."""
    from . import update_review as UR

    keep = UR._keep(sr)
    body = CL.body_of(md)
    live = {c["key"] for c in CL.extract_claims(body, keep)}
    rows = CL.read_rows(sr.slug_dir / "out" / sr.unit.unit / "audit-results.jsonl")
    ok = {k for k, r in CL.latest_real(rows, live).items() if r.get("verdict") in _CONFIRMED}
    out: list[tuple[str, dict[str, Any]]] = []
    when, block = "", []
    for ln in [*body.split("\n"), "00/00/0000"]:
        if _DATE_LINE.match(ln):
            out += [(when, c) for c in CL.extract_claims("\n".join(block), keep) if c["key"] in ok]
            when, block = ln.strip(), []
        block.append(ln)
    return out


def _pages(c: dict[str, Any]) -> set[tuple[int, int]]:
    return {(int(c["exhibit"]), p) for p in CL.parse_pages(c["page_spec"]) or [1]}


def differ_sides(f: review.Finding, claims: list[tuple[str, dict[str, Any]]]) -> list[set[tuple[int, int]]] | None:
    """The (exhibit, page) set each side of a check 2 finding resolves to:
    each quoted statement to the confirmed claims containing it, else (two
    or more cited pages) each cited page to a confirmed claim on it in the
    entry the finding dates. None when it names fewer than two sides or any
    side resolves to no confirmed claim (a diagnosis-table row, a heading, a
    sentence the audit did not confirm)."""
    t = _text(f)
    quotes = [a or b for a, b in _QUOTED.findall(t)]
    locs = list(dict.fromkeys((int(m.group(1)), int(m.group(2))) for m in _EXHIBIT_PAGE.finditer(t) if m.group(2)))
    sides: list[set[tuple[int, int]]] = []
    if len(quotes) >= 2:
        for q in quotes:
            hit = [c for _, c in claims if _norm(q) in _norm(c["claim"])]
            if not hit:
                return None
            sides.append(set().union(*(_pages(c) for c in hit)))
        return sides
    if len(locs) < 2:
        return None
    dates = set(_DATE.findall(t))
    for loc in locs:
        if not any(w in dates and loc in _pages(c) for w, c in claims):
            return None
        sides.append({loc})
    return sides


def disclose_differ(sr: StageRun, doc: Path, out: Outcome, stats: dict[str, int]) -> None:
    """Step 5: a model check 2 MUST-FIX whose every side is a confirmed live
    claim, two of them on different pages, is the records differing: a
    NOTE that says so. Any other check 2 MUST-FIX with a cite is flagged for
    the attorney in a NOTE that claims nothing was confirmed (see the module
    docstring)."""
    claims: list[tuple[str, dict[str, Any]]] | None = None
    for k, f in enumerate(out.findings):
        if f.by != "model" or f.grade != review.MUST or check_no(f) != 2:
            continue
        if claims is None:
            claims = confirmed_claims(sr, doc.read_text(encoding="utf-8"))
        sides = differ_sides(f, claims)
        if sides and any(a.isdisjoint(b) for i, a in enumerate(sides) for b in sides[i + 1 :]):
            out.findings[k] = replace(f, grade=review.NOTE, finding=DIFFER_NOTE + f.finding)
            stats["differ_disclosed"] += 1
        elif f.cite.strip():
            out.findings[k] = replace(f, grade=review.NOTE, finding=FLAG_NOTE + f.finding)
            stats["differ_flagged"] = stats.get("differ_flagged", 0) + 1


# ---- a held review, settled again on resume ---------------------------------------
_FIELDS = ("grade", "check", "finding", "cite", "by")


def resettle(
    sr: StageRun,
    rd: Path,
    rec: dict[str, Any],
    rows: list[dict[str, Any]],
    code: Any,
    *,
    update: bool,
    notes_tag: str,
    today: Any,
) -> tuple[list[review.Finding], list[dict[str, Any]]] | None:
    """A review held on its recorded findings, settled again by today's $0
    rules (2026-10-25, Pamplona: held on three findings a rule shipped after
    the review could settle, and a resume only held again, since the record
    is forgotten only when the package changes). No repair round and no
    model call: the same findings give the same result every time. When the
    rules change the chronology it is re-rendered and code's findings are
    taken afresh on what will be filed (`code(rows)`), replacing the
    recorded ones. Returns (findings, rows): rows is the same object when
    nothing at all changed; None when the re-render fails (nothing is filed)."""
    from . import review_policy

    found = [review.Finding(**{k: str(f.get(k) or "") for k in _FIELDS}) for f in rec.get("findings") or []]
    settled, new_rows = review_policy.package(
        sr, rd, found, rows, update=update, notes_tag=notes_tag, repair=False, today=today
    )
    if new_rows is None:
        return None
    if new_rows is rows:
        same = [f.grade for f in settled] == [str(f.get("grade")) for f in rec.get("findings") or []]
        return settled, rows if same else list(rows)
    fresh = list(code(new_rows))
    kept = [f for f in settled if f.by != "code"]
    left = sum(f.grade == review.MUST for f in kept + fresh)
    sr.log(f"review: settled again on resume by the current rules: {left} must-fix left (code's findings taken afresh)")
    return kept + fresh, new_rows
