"""The drafting job's final pass: the final audit's leftovers settled in code
(no model call), for an attorney's work product rather than a demand letter.

It reuses the demand final pass's helpers read-only (``demand/finalpass.py``
is the live demand lane's) and differs in one respect: a finding it cannot
place does not kill the job. A 2026-10-07 large-matter dry run spent $68.64 and
filed nothing because one paraphrased claim in a 480-line brief could not be
matched. Here a finding is placed in tiers, and only the last tier falls back:

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
from typing import Any

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


def _spans(claim: str) -> list[str]:
    """The claim's exact quoted spans and its distinctive figures (a money
    amount or a number of four or more digits; never a bare day or year)."""
    got = [q.strip() for q in _QUOTED.findall(claim) if q.strip()]
    got += [f for f in _FIG.findall(claim) if re.search(r"\d,\d{3}|\$\d|\d\.\d{2}", f)]
    return list(dict.fromkeys(got))


def _words(s: str) -> set[str]:
    return {t for t in _TOK.findall(s.lower()) if len(t) > 2 and t not in _STOP}


def locate_by_spans(body: str, claim: str) -> tuple[int, int] | None:
    """The sentence unit holding the most of the claim's exact spans (>= 1)."""
    want = _spans(claim)
    if not want:
        return None
    best, score = None, 0
    for a, b in _units(body):
        flat = " ".join(body[a:b].split())
        s = sum(1 for w in want if " ".join(w.split()) in flat)
        if s > score:
            best, score = (a, b), s
    return best


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
        if any(s in sclaim for s in spans):
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
    bodies = {h: b for h, b in secs}
    notes: list[str] = []
    supported = supported_rows(audit_md)
    unplaced: dict[str, list[str]] = {}
    removed: dict[str, set[str]] = {}  # per section, the quoted spans of claims already settled
    for head, verdict, claim, detail in findings(audit_md):
        if set(_spans(claim)) & removed.get(head, set()):
            notes.append(f"{head}: already settled by an earlier finding; {_clean(claim, 160)}")
            continue
        if head not in bodies:
            notes.append(f"{head}: the final audit names a section the draft does not have; {_clean(claim, 160)}")
            unplaced.setdefault(head, []).append(detail or claim)
            continue
        body = bodies[head]
        if verdict == "ARITHMETIC" and self_cleared(detail):
            notes.append(f"{head}: the final audit checked {_clean(claim, 120)} and states it correct; left as written")
            continue
        done: list[tuple[int, int]] = [
            (m.start(), m.end()) for m in re.finditer(r"\{\{(NOT IN RECORD|ATTORNEY)[^}]*\}\}", body)
        ]
        span = locate(body, claim) or locate_by_spans(body, claim)
        if span is not None and _overlaps(span, done):
            notes.append(f"{head}: already settled by an earlier finding; {_clean(claim, 160)}")
            continue
        if span is None and verdict == "ARITHMETIC":
            figs = [f for f in _FIG.findall(claim) if f in body]
            if figs:
                a = body.find(figs[-1])
                span = (a, a + len(figs[-1]))
        if span is None:
            unplaced.setdefault(head, []).append(detail or claim)
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
        bodies[head] = body
        removed.setdefault(head, set()).update(_spans(claim))
    total = sum(len(v) for v in unplaced.values())
    worst = max((len(v) for v in unplaced.values()), default=0)
    if worst > MAX_UNPLACED_PER_SECTION or total > MAX_UNPLACED:
        raise Unlocated(f"{total} final-audit findings could not be placed in the draft (most in one section: {worst})")
    for head, reasons in unplaced.items():
        if head not in bodies:
            continue
        body = bodies[head]
        first, _, rest = body.partition("\n")
        mark = (
            "{{ATTORNEY: the final audit flags a statement in this section as not supported by the file ("
            + "; ".join(_clean(r, 120) for r in reasons)
            + "); find and confirm or remove it before use}}"
        )
        bodies[head] = f"{first}\n\n{mark}\n{rest}" if first.lstrip().startswith("#") else f"{mark}\n\n{body}"
    out = "\n".join(bodies[h] for h, _ in secs)
    return out + ("\n" if draft_md.endswith("\n") else ""), notes


def summary(notes: list[str]) -> dict[str, Any]:
    return {"settled": len(notes), "notes": notes}
