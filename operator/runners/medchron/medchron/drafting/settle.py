"""The drafting job's final pass: the final audit's leftovers settled in code
(no model call), for an attorney's work product rather than a demand letter.

It reuses the demand final pass's helpers read-only (``demand/finalpass.py``
is the live demand lane's) and differs in one respect: a finding it cannot
place does not kill the job. A 2026-10-07 large-matter dry run filed nothing
because one paraphrased claim in a long brief could not be matched. Here a finding is placed in tiers, and only the last tier falls back:

1. the demand locator (the claim's text, then word overlap);
2. the claim's exact quoted spans and figures, taking the sentence that holds
   the most of them (an auditor paraphrases around the words it quotes);
3. a section-top ``{{ATTORNEY: ...}}`` marker and an attorney-notes line.

An INVENTED claim the SAME audit marks SUPPORTED elsewhere (it shares a quoted
span or figure with a SUPPORTED row) is a contradiction, not an invention: the
sentence is kept and wrapped in an attorney marker rather than removed.

More than ``MAX_UNPLACED_PER_SECTION`` unplaced findings in one section, or
``MAX_UNPLACED`` in the document, still fails the job: past that, the draft
is not one an attorney can review line by line.
"""

from __future__ import annotations

import re
from ..demand.draft import audit_sections, sections
from ..demand.finalpass import _FIG, Unlocated, _clean, _units, findings, locate, self_cleared

MAX_UNPLACED_PER_SECTION = 2
MAX_UNPLACED = 4
_QUOTED = re.compile(r"[\"“]([^\"“”]{8,}?)[\"”]")


_SUPPORTED = re.compile(r"\|\s*SUPPORTED\s*\|", re.I)
_TOK = re.compile(r"[a-z0-9]+")
_STOP = {"the", "and", "for", "that", "this", "with", "from", "was", "were", "his", "her", "each", "quote"}
#: Word overlap at which a SUPPORTED row elsewhere states the same thing.
SAME_CLAIM = 0.5


def _quotes(claim: str) -> list[str]:
    """The claim's exact quoted spans (eight characters or more)."""
    return list(dict.fromkeys(q.strip() for q in _QUOTED.findall(claim) if q.strip()))


def _spans(claim: str) -> list[str]:
    """The claim's exact quoted spans and its distinctive figures (a money
    amount or a number of four or more digits; never a bare day or year)."""
    figs = [f for f in _FIG.findall(claim) if re.search(r"\d,\d{3}|\$\d|\d\.\d{2}", f)]
    return list(dict.fromkeys([*_quotes(claim), *figs]))


def _has(text: str, span: str) -> bool:
    """``span`` in ``text``: a quote as written, a figure as a whole number
    ("3,550" is not in "13,550")."""
    flat = " ".join(text.split())
    s = " ".join(span.split())
    if _FIG.fullmatch(s.lstrip("$")) or _FIG.fullmatch(s):
        return re.search(r"(?<![\d,.])" + re.escape(s) + r"(?![\d,]|\.\d)", flat) is not None
    return s in flat


def _words(s: str) -> set[str]:
    return {t for t in _TOK.findall(s.lower()) if len(t) > 2 and t not in _STOP}


def locate_by_spans(body: str, claim: str) -> tuple[int, int] | None:
    """The sentence unit holding the most of the claim's exact spans (>= 1)."""
    want = _spans(claim)
    if not want:
        return None
    words = _words(claim)
    figure_only = not _quotes(claim)
    best, score, tie = None, 0, False
    for a, b in _units(body):
        s = sum(1 for w in want if _has(body[a:b], w))
        if s and figure_only and words and len(words & _words(body[a:b])) / len(words) < 0.25:
            s = 0  # a bare figure alone does not name a sentence
        if s > score:
            best, score, tie = (a, b), s, False
        elif s and s == score:
            tie = True
    return None if tie else best


def supported_rows(audit_md: str) -> list[tuple[str, str]]:
    """(section, claim) for each SUPPORTED row of the audit."""
    out = []
    for head, body in audit_sections(audit_md).items():
        for ln in body.splitlines():
            m = _SUPPORTED.search(ln)
            if m:
                out.append((head, ln[: m.start()].strip().lstrip("-*").strip()))
    return out


def _contradicted(claim: str, head: str, supported: list[tuple[str, str]]) -> str | None:
    """Another section whose SUPPORTED row states the same thing: it shares an
    exact quoted span or distinctive figure, or most of the claim's words."""
    spans = _spans(claim)
    want = _words(claim)
    for other, sclaim in supported:
        if other == head:
            continue
        if any(_has(sclaim, s) for s in spans):
            return other
        if want and len(want & _words(sclaim)) / len(want) >= SAME_CLAIM:
            return other
    return None


def _overlaps(span: tuple[int, int], done: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in done)


def settle(draft_md: str, audit_md: str) -> tuple[str, list[str]]:
    """The draft with every INVENTED statement removed or marked and every
    ARITHMETIC figure flagged, and the attorney-notes lines saying what was
    done. Raises ``Unlocated`` only past the unplaced-findings ceiling."""
    secs = sections(draft_md)
    heads = [h for h, _ in secs]
    bodies = [b for _, b in secs]
    notes: list[str] = []
    supported = supported_rows(audit_md)
    unplaced: dict[str, list[str]] = {}  # by heading; reasons only, never the claim
    removed: dict[str, set[str]] = {}  # per section, the quoted passages of claims already settled
    written: dict[int, list[tuple[str, str]]] = {}  # per section index: (marker, verdict) THIS pass wrote
    for head, verdict, claim, detail in findings(audit_md):
        if set(_quotes(claim)) & removed.get(head, set()):
            notes.append(f"{head}: already settled by an earlier finding; {_clean(claim, 160)}")
            continue
        idxs = [i for i, h in enumerate(heads) if h == head]
        if not idxs:
            notes.append(f"{head}: the final audit names a section the draft does not have; {_clean(claim, 160)}")
            unplaced.setdefault(head, []).append(detail or f"{verdict.lower()}, no reason given")
            continue
        if verdict == "ARITHMETIC" and self_cleared(detail):
            notes.append(f"{head}: the final audit checked {_clean(claim, 120)} and states it correct; left as written")
            continue
        # The first same-named section that locates the claim.
        idx, span = idxs[0], None
        for i in idxs:
            quoted = locate_by_spans(bodies[i], claim) if _quotes(claim) else None
            span = quoted or locate(bodies[i], claim) or locate_by_spans(bodies[i], claim)
            if span is not None:
                idx = i
                break
        body = bodies[idx]
        done: list[tuple[int, int]] = [
            (m.start(), m.end())
            for w, kind in written.get(idx, [])
            if kind == "INVENTED" or verdict == kind
            for m in re.finditer(re.escape(w), body)
        ]
        if span is not None and _overlaps(span, done):
            # A marker ends without a period, so it reads as the start of the
            # next sentence: settle what follows the marker, not the marker.
            a, b = span
            for ma, mb in done:
                if ma <= a < mb or a <= ma < b:
                    a = max(a, mb)
            while a < b and body[a].isspace():
                a += 1
            if a >= b or not body[a:b].strip():
                notes.append(f"{head}: already settled by an earlier finding; {_clean(claim, 160)}")
                continue
            span = (a, b)
        if span is None and verdict == "ARITHMETIC":
            figs = [f for f in _spans(claim) if _FIG.fullmatch(f.lstrip("$")) and _has(body, f)]
            if figs:
                a = body.find(figs[-1])
                span = (a, a + len(figs[-1]))
        if span is None:
            unplaced.setdefault(head, []).append(detail or f"{verdict.lower()}, no reason given")
            notes.append(
                f"{head}: the final audit flagged {_clean(claim, 160)} ({_clean(detail, 160)}); not located, marked at the section top"
            )
            continue
        a, b = span
        unit = body[a:b]
        other = _contradicted(claim, head, supported) if verdict == "INVENTED" else None
        if verdict == "INVENTED" and other:
            mark = (
                "{{ATTORNEY: the final audit disagrees with itself about the next statement "
                f"(not supported in {_clean(head, 60)}, supported in {_clean(other, 60)}); confirm it against the file}} "
            )
            body = body[:a] + mark + unit + body[b:]
            notes.append(f"{head}: kept and marked, the audit contradicts itself: {_clean(claim, 160)}")
        elif verdict == "INVENTED":
            mark = (
                "{{NOT IN RECORD: a statement here was removed by the final audit ("
                + _clean(detail, 160)
                + "); not found in the file}}"
            )
            body = body[:a] + mark + body[b:]
            notes.append(f"{head}: removed {_clean(claim, 160)} ({_clean(detail, 160)})")
        else:
            mark = "{{ATTORNEY: verify arithmetic: " + _clean(detail or claim) + "}}"
            figs = [f for f in _FIG.findall(claim) if f in unit and re.search(r"\d", f)]
            if figs:
                i = unit.rfind(figs[-1])
                unit = unit[:i] + mark + unit[i + len(figs[-1]) :]
            else:
                unit = mark
            body = body[:a] + unit + body[b:]
            notes.append(f"{head}: figure flagged for the attorney, {_clean(claim, 160)} ({_clean(detail, 160)})")
        bodies[idx] = body
        written.setdefault(idx, []).append((mark, verdict))
        removed.setdefault(head, set()).update(_quotes(claim))
    total = sum(len(v) for v in unplaced.values())
    worst = max((len(v) for v in unplaced.values()), default=0)
    if worst > MAX_UNPLACED_PER_SECTION or total > MAX_UNPLACED:
        raise Unlocated(f"{total} final-audit findings could not be placed in the draft (most in one section: {worst})")
    for head, reasons in unplaced.items():
        # A section the draft does not have: the marker goes at the top of the
        # first section, so the finding still reaches the draft.
        target = heads.index(head) if head in heads else 0
        body = bodies[target]
        first, _, rest = body.partition("\n")
        mark = (
            "{{ATTORNEY: the final audit flags a statement or figure in this section that it could not place ("
            + "; ".join(_clean(r, 120) for r in reasons)
            + "); find and confirm or remove it before use}}"
        )
        if head not in heads:
            mark = mark.replace("in this section", f"in a section the draft lacks ({_clean(head, 60)})")
        bodies[target] = f"{first}\n\n{mark}\n{rest}" if first.lstrip().startswith("#") else f"{mark}\n\n{body}"
    out = "\n".join(bodies)
    return out + ("\n" if draft_md.endswith("\n") else ""), notes
