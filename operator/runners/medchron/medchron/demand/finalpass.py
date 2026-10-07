"""The final audit's leftovers, settled deterministically (no model call).

After the repairs the audit loop can still leave findings (a live demand job,
2026-10-07: 3 INVENTED and 6 ARITHMETIC after two repairs). Stopping the job
for them threw away a letter that was otherwise finished; filing them was
never an option. So each one is settled here, in code, and listed for the
attorney:

* an INVENTED statement is REMOVED and replaced with
  ``{{NOT IN RECORD: a statement here was removed by the final audit (<why>); not found in the file}}``.
  The marker carries the auditor's reason, never the claim: restating the
  claim would put it back in the letter. The claim itself goes to the
  attorney notes;
* an ARITHMETIC finding's figure is removed and replaced with
  ``{{ATTORNEY: verify arithmetic: <the auditor's stated discrepancy>}}``;
  a row whose own detail says the arithmetic is correct is not a discrepancy
  (the auditor sometimes files a check it passed under ARITHMETIC) and is left
  as written, noted for the attorney;
* every change is returned for the attorney notes.

A quotation the free check could not find is the drafting gate's to settle
(quotefix converts it to a paraphrase with its cite). A finding that cannot be
located in the letter at all raises, so an invented sentence can never reach
the render by being hard to find.
"""

from __future__ import annotations

import re
from typing import Any

from .draft import FINDING, audit_sections, sections

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
    s = re.sub(r"[{}|]", "", s).replace('"', "'").replace("“", "'").replace("”", "'")
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
    bare = claim.strip().strip('"“”').strip()
    units = _units(body)
    if len(bare) >= 12:
        i = " ".join(body.split()).find(" ".join(bare.split()))
        if i >= 0:
            for a, b in units:
                if " ".join(bare.split()) in " ".join(body[a:b].split()):
                    return a, b
    want = _toks(bare)
    if not want:
        return None
    best, score = None, 0.0
    for a, b in units:
        s = len(want & _toks(body[a:b])) / len(want)
        if s > score:
            best, score = (a, b), s
    return best if score >= MIN_OVERLAP else None


def settle(draft_md: str, audit_md: str) -> tuple[str, list[str]]:
    """The letter with every INVENTED statement removed and every ARITHMETIC
    figure flagged, and the attorney-notes lines saying what was done."""
    secs = sections(draft_md)
    bodies = {h: b for h, b in secs}
    notes: list[str] = []
    for head, verdict, claim, detail in findings(audit_md):
        if head not in bodies:
            raise Unlocated(f"the final audit names section {head[:60]!r}, which the letter does not have")
        body = bodies[head]
        if verdict == "ARITHMETIC" and self_cleared(detail):
            notes.append(f"{head}: the final audit checked {_clean(claim, 120)} and states it correct; left as written")
            continue
        span = locate(body, claim)
        if span is None:
            if verdict == "ARITHMETIC" and any(f in body for f in _FIG.findall(claim)):
                fig = [f for f in _FIG.findall(claim) if f in body][-1]
                a = body.find(fig)
                span = (a, a + len(fig))
            else:
                raise Unlocated(f"a final-audit {verdict} finding in {head[:60]!r} matches nothing in that section")
        a, b = span
        unit = body[a:b]
        if verdict == "INVENTED":
            # The marker says WHY, never what was claimed: restating the claim
            # would put the invented statement back in the letter.
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
                fig = figs[-1]
                i = unit.rfind(fig)
                unit = unit[:i] + mark + unit[i + len(fig) :]
            else:
                unit = mark
            body = body[:a] + unit + body[b:]
            notes.append(f"{head}: figure flagged for the attorney, {_clean(claim, 160)} ({_clean(detail, 160)})")
        bodies[head] = body
    out = "\n".join(bodies[h] for h, _ in secs)
    return out + ("\n" if draft_md.endswith("\n") else ""), notes


def summary(notes: list[str]) -> dict[str, Any]:
    return {
        "removed": sum(1 for n in notes if ": removed " in n),
        "flagged": sum(1 for n in notes if "figure flagged" in n),
        "cleared": sum(1 for n in notes if "states it correct" in n),
    }
