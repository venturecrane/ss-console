"""The final audit's leftovers, settled deterministically (no model call).

After the repairs the audit loop can still leave findings (a live demand job,
2026-10-07: 3 INVENTED and 6 ARITHMETIC after two repairs). Stopping the job
for them threw away a letter that was otherwise finished; filing them was
never an option. So each one is settled here, in code, and listed for the
attorney:

* an INVENTED statement is REMOVED and replaced with an open-item marker in
  the draft's own dialect (``format_check.dialect``): the firm's
  ``[TO BE SUPPLIED: a statement here is not supported by the file (<why>)]``,
  or ``{{NOT IN RECORD: ...}}`` when the requester's brief named the curly
  markers. The marker carries the reason, never the claim: restating the
  claim would put it back in the letter. The claim itself goes to the
  attorney notes;
* an ARITHMETIC finding's figure is removed and replaced with
  ``[CONFIRM the arithmetic (<the stated discrepancy>) BEFORE TRANSMITTAL]``
  (curly: ``{{ATTORNEY: verify arithmetic: ...}}``); a row whose own detail
  says the arithmetic is correct is not a discrepancy (the check sometimes
  files one it passed under ARITHMETIC) and is left as written, noted for the
  attorney;
* every change is returned for the attorney notes.

Every word code writes here is read by the firm: a marker or a note names no
stage of this system ("final audit", "auditor", a gate, a pass). The 2026-10-15
proof run's review held on exactly that. The format check counts either
dialect's marker as an open item.

A quotation the free check could not find is the drafting gate's to settle
(quotefix converts it to a paraphrase with its cite). A finding that cannot be
located in the letter at all raises, so an invented sentence can never reach
the render by being hard to find.
"""

from __future__ import annotations

import re
from typing import Any

from .draft import FINDING, audit_sections, firm_reason, sections
from .format_check import CURLY, SQUARE

_TOK = re.compile(r"[a-z0-9]+")
_FIG = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
_CLEARED = re.compile(r"\b(correct(ly)?|checks out|confirmed|verified)\b", re.I)
_NOT_CLEARED = re.compile(r"\b(incorrect|not correct|does not|doesn't|should be|off by|discrepan)", re.I)
MIN_OVERLAP = 0.4


class Unlocated(RuntimeError):
    """A final-audit finding that matches nothing in its section."""


def _toks(s: str) -> set[str]:
    return {t for t in _TOK.findall(s.lower()) if len(t) > 2 or t.isdigit()}


def _clean(s: str, n: int = 220) -> str:
    s = re.sub(r"[{}\[\]|]", "", s).replace('"', "'").replace("“", "'").replace("”", "'")
    s = firm_reason(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def findings(audit_md: str) -> list[tuple[str, str, str, str]]:
    """(section, verdict, claim, detail) for each INVENTED / ARITHMETIC row."""
    out = []
    for head, body in audit_sections(audit_md).items():
        for ln in body.splitlines():
            m = FINDING.search(ln)
            if not m or m.group(1).upper() not in ("INVENTED", "ARITHMETIC"):
                continue
            claim = ln[: m.start()].strip().lstrip("-*").strip()
            detail = ln[m.end() :].strip()
            out.append((head, m.group(1).upper(), claim, detail))
    return out


def self_cleared(detail: str) -> bool:
    return bool(_CLEARED.search(detail)) and not _NOT_CLEARED.search(detail)


def _units(body: str) -> list[tuple[int, int]]:
    """Spans of the section body a finding can name: table cells, front-matter
    values, and sentences."""
    spans, pos = [], 0
    for line in body.split("\n"):
        start = pos
        pos += len(line) + 1
        if not line.strip() or re.match(r"^#{1,6} ", line) or re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line):
            continue
        if line.lstrip().startswith("|"):
            off = start
            for cell in line.split("|"):
                if cell.strip():
                    lead = len(cell) - len(cell.lstrip())
                    spans.append((off + lead, off + len(cell.rstrip())))
                off += len(cell) + 1
            continue
        fm = re.match(r"^[a-z_]+: ", line)
        base = start + (fm.end() if fm else 0)
        text = line[fm.end() :] if fm else line
        for a, b in sentences(text):
            spans.append((base + a, base + b))
    return spans


#: Tokens a period follows without ending the sentence ("p. 2", "Dr. Example", "No. 7").
_ABBREV = {
    "p",
    "pp",
    "no",
    "nos",
    "dr",
    "mr",
    "mrs",
    "ms",
    "st",
    "v",
    "vs",
    "inc",
    "co",
    "corp",
    "ltd",
    "jr",
    "sr",
    "subd",
    "civ",
    "proc",
    "evid",
    "veh",
    "ca",
    "cal",
    "dept",
    "ex",
    "fig",
    "approx",
    "etc",
    "md",
    "dpm",
    "dpt",
    "pa",
    "u",
    "s",
    "e",
    "g",
    "i",
}


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans: an end mark ends a sentence only before whitespace (or
    the end) and never after an abbreviation, an initial, or inside a figure
    like $1,200.00 or a citation like p. 2."""
    out, start, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in ".!?":
            j = i + 1
            while j < n and text[j] in ".!?)\"”'":
                j += 1
            if j == n or text[j].isspace():
                word = re.findall(r"[A-Za-z]+$", text[start:i])
                w = word[0].lower() if word else ""
                if c != "." or not (w in _ABBREV or len(w) == 1):
                    seg = text[start:j]
                    if seg.strip():
                        lead = len(seg) - len(seg.lstrip())
                        out.append((start + lead, j))
                    start = j
            i = j
            continue
        i += 1
    if text[start:].strip():
        lead = len(text[start:]) - len(text[start:].lstrip())
        out.append((start + lead, n))
    return out


def locate(body: str, claim: str) -> tuple[int, int] | None:
    return _match(body, claim)[0]


def _match(body: str, claim: str) -> tuple[tuple[int, int] | None, float]:
    """The unit a claim names and how well it matched: a verbatim match
    scores above any word overlap, so two same-titled sections pick the one
    that carries the claim, not a near neighbour."""
    bare = claim.strip().strip('"“”').strip()
    units = _units(body)
    if len(bare) >= 12:
        i = " ".join(body.split()).find(" ".join(bare.split()))
        if i >= 0:
            for a, b in units:
                if " ".join(bare.split()) in " ".join(body[a:b].split()):
                    return (a, b), 2.0
    want = _toks(bare)
    if not want:
        return None, 0.0
    best, score = None, 0.0
    for a, b in units:
        s = len(want & _toks(body[a:b])) / len(want)
        if s > score:
            best, score = (a, b), s
    return (best, score) if score >= MIN_OVERLAP else (None, 0.0)


#: The words code writes, one place: the markers by dialect and the notes.
MARK = {
    (SQUARE, "INVENTED"): "[TO BE SUPPLIED: a statement here is not supported by the file ({why})]",
    (CURLY, "INVENTED"): "{{{{NOT IN RECORD: a statement here is not supported by the file ({why})}}}}",
    (SQUARE, "ARITHMETIC"): "[CONFIRM the arithmetic ({why}) BEFORE TRANSMITTAL]",
    (CURLY, "ARITHMETIC"): "{{{{ATTORNEY: verify arithmetic: {why}}}}}",
}
NOTE_REMOVED = "{head}: taken out, the file does not support it: {claim} ({why})"
NOTE_FLAGGED = "{head}: figure marked for the attorney to confirm: {claim} ({why})"
NOTE_KEPT = (
    "{head}: the file may not support this and it is still in the letter, for the attorney to decide: {claim} ({why})"
)
NOTE_CLEARED = "{head}: rechecked {claim}; the arithmetic is correct and it is left as written"


_QUOTED = re.compile(r"[\"“]([^\"“”]{3,})[\"”]")


def _flat(s: str) -> str:
    return " ".join(s.split()).strip(" ,;.").lower()


def remaining(claim: str, body: str) -> list[str] | None:
    """What of ``claim`` is still in ``body`` after its removal: the quoted
    items a list-shaped claim names (two or more) that are still there, ``[]``
    when nothing is, and ``None`` when a single statement found verbatim before
    the edit is still verbatim after it (the edit hit the wrong unit). A note
    saying "taken out" is written only on ``[]``."""
    flat = _flat(body)
    items = [_flat(q) for q in _QUOTED.findall(claim)]
    items = [q for q in items if len(q) >= 3]
    if len(items) >= 2:
        return [q for q in items if q in flat]
    bare = _flat(claim.strip().strip('"“”'))
    return None if len(bare) >= 12 and bare in flat else []


def _place(
    heads: list[str], bodies: list[str], head: str, claim: str, verdict: str
) -> tuple[int, tuple[int, int] | None]:
    """The same-named section that locates the claim best (the first on a
    tie), and the span (an ARITHMETIC finding falls back to its figure); else
    the first such section and None."""
    idxs = [i for i, h in enumerate(heads) if h == head]
    found = [(score, -i, i, span) for i in idxs for span, score in [_match(bodies[i], claim)] if span is not None]
    if found:
        _s, _n, i, span = max(found)
        return i, span
    if verdict == "ARITHMETIC":
        for i in idxs:
            figs = [f for f in _FIG.findall(claim) if f in bodies[i]]
            if figs:
                a = bodies[i].find(figs[-1])
                return i, (a, a + len(figs[-1]))
    return idxs[0], None


def settle(draft_md: str, audit_md: str, dialect: str = SQUARE) -> tuple[str, list[str]]:
    """The letter with every INVENTED statement removed and every ARITHMETIC
    figure flagged, in ``dialect``'s markers, and the attorney-notes lines
    saying what was done."""
    if dialect not in (SQUARE, CURLY):
        raise ValueError(f"unknown marker dialect {dialect!r}")
    secs = sections(draft_md)
    # A list, never a dict keyed by heading: two sections with the same title
    # (two "Damages" headings) are two bodies, and a dict kept only the last,
    # writing it twice and dropping the first (drafting/settle.py's shape).
    heads = [h for h, _ in secs]
    bodies = [b for _, b in secs]
    notes: list[str] = []
    for head, verdict, claim, detail in findings(audit_md):
        if head not in heads:
            raise Unlocated(f"the final audit names section {head[:60]!r}, which the letter does not have")
        if verdict == "ARITHMETIC" and self_cleared(detail):
            notes.append(NOTE_CLEARED.format(head=head, claim=_clean(claim, 120)))
            continue
        idx, span = _place(heads, bodies, head, claim, verdict)
        if span is None:
            raise Unlocated(f"a final-audit {verdict} finding in {head[:60]!r} matches nothing in that section")
        body = bodies[idx]
        a, b = span
        unit = body[a:b]
        if verdict == "INVENTED":
            # The marker says WHY, never what was claimed: restating the claim
            # would put the invented statement back in the letter.
            edited = body[:a] + MARK[(dialect, verdict)].format(why=_clean(detail, 160)) + body[b:]
            left = remaining(claim, edited)
            if left is None:
                raise Unlocated(
                    f"a final-audit INVENTED finding in {head[:60]!r} is still in the section after removal"
                )
            if left:
                # A list of items spread through the section (2026-10-15: six
                # exhibit cites, one cell replaced, the note said all six went).
                # Removing every one rewrites the letter; the attorney decides.
                notes.append(NOTE_KEPT.format(head=head, claim=_clean(claim, 160), why=_clean(detail, 160)))
                continue
            body = edited
            notes.append(NOTE_REMOVED.format(head=head, claim=_clean(claim, 160), why=_clean(detail, 160)))
        else:
            mark = MARK[(dialect, verdict)].format(why=_clean(detail or claim))
            figs = [f for f in _FIG.findall(claim) if f in unit and re.search(r"\d", f)]
            if figs:
                fig = figs[-1]
                i = unit.rfind(fig)
                unit = unit[:i] + mark + unit[i + len(fig) :]
            else:
                unit = mark
            edited = body[:a] + unit + body[b:]
            if mark not in edited or edited.count(mark) != body.count(mark) + 1:
                raise Unlocated(f"a final-audit ARITHMETIC finding in {head[:60]!r} could not be marked")
            body = edited
            notes.append(NOTE_FLAGGED.format(head=head, claim=_clean(claim, 160), why=_clean(detail, 160)))
        bodies[idx] = body
    out = "\n".join(bodies)
    return out + ("\n" if draft_md.endswith("\n") else ""), notes


def summary(notes: list[str]) -> dict[str, Any]:
    return {
        "removed": sum(1 for n in notes if ": taken out, " in n),
        "flagged": sum(1 for n in notes if ": figure marked for the attorney" in n),
        "cleared": sum(1 for n in notes if "the arithmetic is correct and it is left as written" in n),
        "kept": sum(1 for n in notes if "it is still in the letter, for the attorney to decide" in n),
    }
