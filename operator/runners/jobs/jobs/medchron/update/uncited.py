"""Every paragraph of an added entry is closed by a citation, so the audit grades it.

WHY (2026-10-14). The audit grades CLAIMS, and a claim is the paragraph a
citation closes (``audit/claims._walk``). Compose writes a subsection as a
run of paragraphs and cites only the last, so every earlier paragraph of the
run was text no audit ever read. On a real update one such paragraph restated
the cited panel below it with a value misread ("1,07." for a three-digit
value) and a sentence about how hard the flags were to align; it reached the
delivered chronology, and the update review, which caught it every round,
could not drop it (it was no claim), so the job held for SMD. The same run
carried 117 uncited paragraphs in its added entries, real findings among
them (an ultrasound's impression, a hemoglobin of a transfusion level).

Dropping them would lose those findings, so each one is CITED instead, and
the existing audit and repair rules decide it: supported, kept with its
citation (widened when the audit finds it on a neighbouring page);
unsupported, repaired by removal or weakening, or dropped at the round cap
with the values it took named; a sentence about our own reading, removed by
repair, which deletes assertions about the record set. The citation is the
one that closes the paragraph's run: the first citation of the NEXT cited
paragraph of the entry (compose's own shape: a run closes with its
citation), or, when none follows, the last citation of the previous one. It
names only a page the entry already cites, so no exhibit becomes cited by
this alone.

Two shapes the audit would still skip are folded into an audited paragraph:
text after a paragraph's last citation moves before it, and a value-free
paragraph shorter than the audit's floor (``claims.graded``: 30 characters)
joins its neighbour (the next one, or the previous when the next opens a
subsection). A short paragraph that carries a value is graded on its own.
A heading standing alone carries no assertion and is left as it is.

A line that is ONLY a citation (2026-11-17: 64 such lines in 46 rows of one
update) joins the text it cites (``join_stray``), before the paragraphs are
cited and again after, and once more after the update finisher's edits
(``assemble``). A citation that closes the line above joins it; an exact
repeat, or one of an exhibit the entry cites elsewhere, goes; the only
citation of an exhibit is never dropped, so every exhibit stays cited. A
citation carries no value, so no value can be lost.
"""

from __future__ import annotations

import re
from typing import Any

from ..audit import claims as CL
from . import body as B

_ALNUM = re.compile(r"[A-Za-z0-9]")
_LEAD = re.compile(r"^[\s.;,]*")


def _cited(p: str) -> bool:
    return bool(CL.CITE.search(p))


def _heading_only(p: str, headings: set[str]) -> bool:
    return all(not ln.strip() or ln.strip() in headings for ln in p.split("\n"))


def is_header(p: str) -> bool:
    """An entry's header read as a body paragraph: a date line (a yearless
    one rode inside the entry before it until parity P) or a provider line
    ("Name | Heading"). A header is never a claim: cited, it was graded
    against a page and repaired into a bare name the audit could not count
    (2026-10-14)."""
    lines = [ln.strip() for ln in p.split("\n") if ln.strip()]
    if not lines or len(lines) > 2:
        return False
    if B.HEAD.match(lines[0]):
        return True
    return len(lines) == 1 and " | " in lines[0] and not lines[0].startswith("|")


def _short(p: str) -> bool:
    return not CL.graded(p)


def _opens_heading(p: str, headings: set[str]) -> bool:
    return p.lstrip("\n").split("\n", 1)[0].strip() in headings


def fold_tail(p: str) -> str:
    """Text after a paragraph's last citation, moved before it (the claim
    the citation closes then carries it)."""
    ms = list(CL.CITE.finditer(p))
    if not ms or not _ALNUM.search(p[ms[-1].end() :]):
        return p
    m = ms[-1]
    pre, tail = p[: m.start()].rstrip(), p[m.end() :]
    lead = _LEAD.match(tail)
    rest = tail[lead.end() if lead else 0 :].strip()
    stop = "." if "." in (lead.group(0) if lead else "") and not pre.endswith((".", "!", "?", ":")) else ""
    return f"{pre}{stop} {rest} {m.group(0)}"


def _into_prev(prev: str, p: str) -> str:
    ms = list(CL.CITE.finditer(prev))
    if not ms:
        return prev.rstrip("\n") + "\n" + p.strip("\n")
    m = ms[-1]
    return f"{prev[: m.start()].rstrip()} {' '.join(p.split())} {prev[m.start() :]}"


def _merge_short(paras: list[str], headings: set[str]) -> list[str]:
    """Each uncited paragraph below the audit's floor joined to a neighbour."""
    out = list(paras)
    i = 0
    while i < len(out):
        p = out[i]
        if not p.strip() or _cited(p) or _heading_only(p, headings) or is_header(p) or not _short(p) or len(out) == 1:
            i += 1
            continue
        nxt = i + 1 < len(out)
        if nxt and (i == 0 or not _opens_heading(out[i + 1], headings)):
            out[i + 1] = p.rstrip("\n") + "\n" + out[i + 1].lstrip("\n")
            del out[i]
        else:
            out[i - 1] = _into_prev(out[i - 1], p)
            del out[i]
            i = max(i - 1, 0)
    return out


def _closing_cite(paras: list[str], i: int) -> str | None:
    for q in paras[i + 1 :]:
        m = CL.CITE.search(q)
        if m:
            return m.group(0)
    for q in reversed(paras[:i]):
        ms = list(CL.CITE.finditer(q))
        if ms:
            return ms[-1].group(0)
    return None


def cite_only(line: str) -> bool:
    """A line that is nothing but citations (and the punctuation between them)."""
    return bool(CL.CITE.search(line)) and not _ALNUM.search(CL.CITE.sub("", line))


def _ends_cited(line: str) -> bool:
    ms = list(CL.CITE.finditer(line))
    return bool(ms) and not _ALNUM.search(line[ms[-1].end() :])


def _exhibit(cite: str) -> int:
    m = re.search(r"\d+", cite)
    return int(m.group(0)) if m else -1


def _target(out: list[str], within: bool, headings: set[str]) -> int | None:
    """Where a stray citation line joins: the line just above it in its own
    paragraph (`within`), else the nearest line above that ends with a
    citation, so a joined citation never makes uncited text a new claim."""
    for k in range(len(out) - 1, -1, -1):
        ln = out[k].strip()
        if not ln:
            if within:
                return None
            continue
        if within and ln not in headings:
            return k
        within = False
        if ln not in headings and _ends_cited(ln):
            return k
    return None


def _join_one(target: str, cites: list[str], others: set[int]) -> str:
    """`target` with the stray line's citations joined: the first one when it
    closes `target`'s text; then each citation of an exhibit no other line
    of the entry cites (an exhibit stays cited); every other one dropped (a
    citation carries no value, so no value is lost)."""
    add: list[str] = []
    for c in cites:
        closes = not add and not _ends_cited(target)
        if closes or (c not in target and _exhibit(c) not in others):
            add.append(c)
            others.add(_exhibit(c))
    return f"{target.rstrip()} {' '.join(add)}" if add else target


def join_stray(entry: str, headings: set[str] | None = None, audited: bool = False) -> str:
    """The entry with no line that is only a citation (``cite_only``). Each
    joins the text above it (``_target``, ``_join_one``); a stray line with
    no line above to join waits for the next line that ends with a citation;
    one with none is left as it is. The text a claim's citation closes is
    never changed, with one exception: a stray citation right under a
    heading, with text below it in the same paragraph, kept that heading out
    of the claim below; joined before the audit, the heading is part of that
    claim as it is everywhere else. After the audit (`audited`) such a line
    is left as it is, so no audited claim changes."""
    heads = headings or set()
    lines = entry.split("\n")
    if len(lines) < 3 or not any(cite_only(ln) for ln in lines[2:]):
        return entry
    body_in = lines[2:]
    others = {_exhibit(m.group(0)) for ln in body_in if not cite_only(ln) for m in CL.CITE.finditer(ln)}
    out: list[str] = []
    waiting: list[str] = []
    for i, ln in enumerate(body_in):
        if not cite_only(ln):
            if waiting and _ends_cited(ln):
                ln, waiting = _join_one(ln, waiting, others), []
            out.append(ln)
            continue
        cites = [m.group(0) for m in CL.CITE.finditer(ln)]
        within = bool(out) and bool(out[-1].strip()) and i > 0
        below = i + 1 < len(body_in) and bool(body_in[i + 1].strip())
        if audited and within and out[-1].strip() in heads and below:
            out.append(ln)  # it closes the heading's span: removing it would change the claim below
            continue
        k = _target(out, within, heads)
        if k is None:
            waiting += cites
            continue
        out[k] = _join_one(out[k], cites, others)
    out += waiting  # nowhere to join: left as it was
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return "\n".join(lines[:2]) + "\n" + body.rstrip("\n") + ("\n" if entry.endswith("\n") else "")


def cite_entry(entry: str, headings: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """(the entry with every paragraph closed by a citation, a row per
    paragraph cited here). The date line and provider line are the entry's
    header, never a claim. A line that is only a citation joins the text it
    cites, before and after (``join_stray``)."""
    entry = join_stray(entry, headings)
    lines = entry.split("\n", 2)
    if len(lines) < 3:
        return entry, []
    head, rest = "\n".join(lines[:2]), lines[2]
    lead = rest[: len(rest) - len(rest.lstrip("\n"))]
    paras = _merge_short([fold_tail(p) if _cited(p) else p for p in rest.lstrip("\n").split("\n\n")], headings)
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(paras):
        if not p.strip() or _cited(p) or _heading_only(p, headings) or is_header(p):
            continue
        cite = _closing_cite(paras, i)
        rows.append({"entry": lines[0], "cite": cite, "chars": len(p.strip())})
        if cite is not None:
            paras[i] = p.rstrip() + " " + cite
    return join_stray(head + "\n" + lead + "\n\n".join(paras), headings), rows


def cite_all(entries: list[str], headings: set[str]) -> tuple[list[str], list[dict[str, Any]]]:
    out, rows = [], []
    for e in entries:
        got, r = cite_entry(e, headings)
        out.append(got)
        rows += r
    return out, rows
