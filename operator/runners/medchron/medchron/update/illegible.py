"""A handwritten visit the audit could not confirm in full carries the firm's
illegible note, and the review stops asking for what no reader can confirm.

WHY (2026-10-14 parity runs). A chiropractic ledger is handwritten.
On its pages the image audit graded 156 of 315 claims PARTIAL and 85
SUPPORTED; on typed pages 12 of 304 were PARTIAL. The audit trims or drops
what it cannot read; the review, a stronger reading, finds the trimmed notes
"omitted" and the finisher writes them back; the grader reverts them. Rounds
did not converge (37, 29, 27 must-fix left), and four entries were left as a
date line and a provider line, raised every round.

The rule, on a NEW entry whose composed text cited a handwritten page:

* the audit emptied it: it keeps its visit as the firm's house note, cited to
  that page;
* the audit removed some of its cited sentences: the house note is added
  under what it kept.

The note is the firm's own wording (``prompts/map-system.md``: "insert
verbatim, as its own paragraph with citation"). It is an ``[NTD: ...]`` span,
so the audit never grades it (``audit/claims.NTD``). With it on an entry the
review's "omitted" and "truncated" findings are settled (``settles``): the
page is cited for the reader, and what the audit could not confirm is not
written. A value the entry states is still answerable to the review.
"""

from __future__ import annotations

import copy
import re
from typing import Callable

from . import body as B, review_repair as RR

#: Verbatim from ``prompts/map-system.md`` (the map prompt's handwritten rule).
HOUSE_NOTE = (
    "[NTD: A portion of the records were handwritten and illegible. Therefore, we were unable to annotate or "
    "decipher a portion of these records and were unable to draft a complete medical summary for this treatment.]"
)
#: Finding classes the note answers: what the entry does not say.
SETTLES = ("omitted", "truncated")
#: Finding classes on a noted entry answered by dropping the sentence: a mark
#: the page does not settle, or words about the process. The d09d63c9 pass
#: (2026-10-14) raised 11-21 "marks on a form" a round on noted ledger rows;
#: every rewrite was reverted by the grader, which cannot read the marks
#: either. A drop writes nothing new, so nothing is graded or reverted.
DROPS = ("marks on a form", "process wording")
_HEADING = re.compile(r"^\s*(#|\*\*[^*]+\*\*:?\s*$|[A-Z][A-Za-z &/()-]+:\s*$)")


def _content(entry: str) -> list[str]:
    """The entry's lines after its date line and its provider line
    ("Provider | Heading", ``body.provider_of``) that are not empty headings."""
    return [ln for ln in entry.splitlines()[2:] if ln.strip() and not _HEADING.match(ln)]


def _head(entry: str) -> str:
    """The date line and the provider line: what an emptied entry still shows."""
    return "\n".join(entry.splitlines()[:2])


def _pages(entry: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in B.CITE.finditer(entry):
        for p in re.findall(r"\d+", m.group(2) or ""):
            out.append((int(m.group(1)), int(p)))
    return out


def _prior(entry: str, priors: list[str]) -> str | None:
    """The composed entry this one came from: the same date of service and a
    shared exhibit. The date line itself is not identity: the handwritten
    re-date and the finisher rewrite it (on one matter no date line survived
    unchanged, 2026-10-14). An emptied entry, which cites nothing, is matched
    on its date and provider lines."""
    date = entry.splitlines()[0][:10]
    mine = B.cited(entry)
    same = [p for p in priors if p.splitlines()[0][:10] == date]
    hit = [p for p in same if mine & B.cited(p)] if mine else [p for p in same if _head(p) == _head(entry)]
    return hit[0] if len(hit) == 1 else None


def has_note(text: str) -> bool:
    return HOUSE_NOTE[:60] in text


def trimmed_pages(edit_rows: list[dict]) -> set[tuple[int, int]]:
    """The (exhibit, page) pairs the audit's repair rewrote or dropped a claim
    on (``repair-edits.jsonl``): a repair trims a claim to what the page
    supports, keeping its citation, so a citation count cannot see it."""
    out: set[tuple[int, int]] = set()
    for r in edit_rows:
        if r.get("action") not in ("repair", "drop-residual") or not r.get("exhibit"):
            continue
        for p in re.findall(r"\d+", str(r.get("page_spec") or "")):
            out.add((int(r["exhibit"]), int(p)))
    return out


def drop_on_noted(findings: list[RR.Finding], ents: list[RR.Entry]) -> tuple[list[RR.Finding], list[RR.Finding]]:
    """(the drop edits for ``DROPS`` findings on a claim of a noted entry, the
    findings left for the finisher). A delivered entry is never edited."""
    noted = {e.anchor for e in ents if has_note(e.text)}
    claims = {c.anchor for e in ents for c in e.claims + e.uncited}
    auto: list[RR.Finding] = []
    rest: list[RR.Finding] = []
    for f in findings:
        k = f.klass.lower()
        if f.anchor in claims and f.anchor.split(".")[0] in noted and any(w in k for w in DROPS):
            e = copy.copy(f)
            e.action, e.corrected = "drop", ""
            e.reason = "the entry carries the handwritten-records note; what the page does not settle is not stated"
            auto.append(e)
        else:
            rest.append(f)
    return auto, rest


def restore(
    before: list[str],
    after: str,
    delivered: list[str],
    handwritten: Callable[[int, int], bool],
    trimmed: set[tuple[int, int]] | frozenset = frozenset(),
) -> tuple[str, list[str]]:
    """(``after`` with the house note on each new entry the audit emptied or
    trimmed on a handwritten page, the date lines it marked). ``before``: the
    entries as the audit received them; an entry is matched by its date and
    provider lines. ``trimmed``: the pages the audit's repair touched."""
    old = set(delivered)
    priors = [e for e in before if e not in old]
    out, done = after, []
    for e in B.entries_of(after)[1]:
        head = _head(e)
        prior = _prior(e, priors)
        if e in old or prior is None or has_note(e):
            continue
        hw = [p for p in _pages(prior) if handwritten(*p)]
        lost = len(B.CITE.findall(e)) < len(B.CITE.findall(prior)) or any(p in trimmed for p in hw)
        if not hw or not lost:
            continue
        note = f"{HOUSE_NOTE} (Exhibit {hw[0][0]} - p. {hw[0][1]})"
        out = out.replace(e, (e if _content(e) else head) + "\n\n" + note, 1)
        done.append(head.splitlines()[0])
    return out, done
