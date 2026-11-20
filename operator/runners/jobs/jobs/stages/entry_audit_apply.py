"""The entry audit's propose, decide, apply and record steps
(``stages/entry_audit.py`` is the stage; split out for the module-size
ceiling). $0, no model.

PROPOSE. Each finding becomes the whole line it would write
(``entry_confirm.propose``) or a reason code. Only the FIRST finding on a
labelled line of an entry is verified and may apply; the others are
``same_line``. An added sentence equal to, or contained in, another added to
the same entry is ``duplicate_add``. A swap of a sentence SMD corrected by
hand (``drops.json`` citation_edits) is ``smd_authored``.

APPLY. A swap replaces the whole labelled line in its entry. An add is its
own paragraph (a blank line before and after) right after the paragraph
holding its line, so no paragraph already in the entry changes by a byte.
Edits are made one at a time on a copy, in document order; each is checked
against the text before it (``broken``: the same entries and header lines,
no fewer sentences, no other paragraph changed, the same closing citation,
no new date or provider line) and an edit that fails is reverted alone and
recorded ``breaks_structure`` (round 5, 2026-11-18). Nothing is removed; the
document is written once.

THE TRIPWIRE AND ITS RELEASE. More applied edits than ``entry_read.EDIT_SHARE``
of the document's sentences holds the job with the document untouched. SMD
releases one such hold, after reading the record, by writing (as root)
``runs/<unit>/entry_read/tripwire-release.json`` = ``{"by": "<who>",
"why": "<what was checked>", "doc_sha": "<the doc_sha the hold line logged>"}``;
the resumed stage then applies the edits and records the release in
``entry_read.json`` (``tripwire_release``). A release with "by" or "why"
empty, or for another document, is ignored and logged.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from ..matter.store import write_atomic
from ..medchron.audit import authored, claims as CL
from ..medchron.update import body as B, entry_confirm as EC, entry_lines as EL, entry_read as ER
from . import entry_audit_io as IO
from .base import StageRun, read_json

SMD_AUTHORED = "smd_authored"
FAILED = object()  # the verify was refused twice
NO_PICTURE = object()  # no page of the verify had a picture: never sent
Item = tuple[str, ER.Unit, ER.ReadFinding]


def _smd(sr: StageRun) -> tuple[set[tuple[int, str]], set[str]]:
    name = f"drops-{sr.unit.unit}.json" if sr.job.joint else "drops.json"
    edits = (read_json(sr.slug_dir / name, {}) or {}).get("citation_edits") or []
    return authored.authored_texts(edits), authored.inline_texts(edits)


def _by_smd(line: str, smd: tuple[set[tuple[int, str]], set[str]]) -> bool:
    flat = " ".join(CL.CITE.sub("", line).split())
    exs = {int(m.group(1)) for m in CL.CITE.finditer(line)}
    return any(ex in exs and t and flat.endswith(t) for ex, t in smd[0]) or any(t in flat for t in smd[1])


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", CL.CITE.sub("", s).lower()).split())


def propose(sr: StageRun, items: list[Item]) -> list[EC.Proposal | str]:
    """Each finding as its proposal, or the reason code it is recorded under."""
    smd = _smd(sr)
    lines: set[tuple[str, str]] = set()
    adds: dict[str, list[str]] = {}
    out: list[EC.Proposal | str] = []
    for _name, unit, f in items:
        p = EC.propose(f, unit)
        if isinstance(p, EC.Proposal):
            if (unit.anchor, f.line) in lines:
                p = EC.SAME_LINE
            elif p.edit.kind == "swap" and _by_smd(p.edit.line, smd):
                p = SMD_AUTHORED
            elif p.edit.kind == "add":
                text, mine = _norm(p.edit.new), adds.setdefault(unit.anchor, [])
                p = EC.DUPLICATE_ADD if any(text in t or t in text for t in mine) else p
                mine.append(text)
            if isinstance(p, EC.Proposal):
                lines.add((unit.anchor, f.line))
        out.append(p)
    return out


def decide(proposals: list[EC.Proposal | str], verifies: list[Any]) -> list[EC.Decision]:
    out: list[EC.Decision] = []
    for p, v in zip(proposals, verifies):
        if isinstance(p, str):
            out.append(("record", p))
        elif v is FAILED:
            out.append(("record", EC.VERIFY_FAILED))
        elif v is NO_PICTURE:
            out.append(("record", EC.NO_PICTURE))
        else:
            out.append(EC.decide(p, v))
    return out


def doc_sha(md: str) -> str:
    return hashlib.sha256(md.encode()).hexdigest()


def release(sr: StageRun, sha: str) -> dict[str, str] | None:
    """SMD's release of the tripwire for THIS document: written with "by",
    "why" and the "doc_sha" the hold line logged. Any other release is
    ignored, and the log says why."""
    got = read_json(sr.slug_dir / "runs" / sr.unit.unit / IO.DIR / IO.RELEASE, None)
    if got is None:
        return None
    rel = {k: str(got.get(k) or "").strip() for k in ("by", "why", "doc_sha")} if isinstance(got, dict) else {}
    if not rel.get("by") or not rel.get("why"):
        sr.log("entry read: the tripwire release is ignored: it names no one or no reason")
        return None
    if rel.get("doc_sha") != sha:
        sr.log(f"entry read: the tripwire release is ignored: it is for another document (doc_sha {sha} expected)")
        return None
    return rel


def _edit_entry(text: str, e: EC.Edit, added: list[str]) -> str | None:
    """The entry with one edit made, or None when its line is not there
    once. An add goes after its line's paragraph and after any sentence
    already added there, so adds keep document order."""
    if text.count(e.line) != 1:
        return None
    if e.kind == "swap":
        return text.replace(e.line, e.new, 1)
    paras = text.split("\n\n")
    at = [i for i, p in enumerate(paras) if e.line in p]
    if len(at) != 1:
        return None
    k = at[0] + 1
    while k < len(paras) and paras[k] in added:
        k += 1
    return "\n\n".join(paras[:k] + [e.new] + paras[k:])


#: A line the entry parser reads as an entry's date line, or as a provider line.
_PROVIDER = re.compile(r"\S\s+\|\s+\S")


def _heads(md: str) -> list[tuple[str, ...]]:
    return [tuple(e.split("\n")[:2]) for e in B.entries_of(md)[1]]


def _sentences(text: str) -> int:
    return sum(len(EL.line_sentences(ln)) for ln in text.split("\n"))


def _last_cite(text: str) -> str:
    got = [m.group() for m in CL.CITE.finditer(text)]
    return got[-1] if got else ""


def broken(before: str, after: str, e: EC.Edit) -> str:
    """'' when the edit kept the document's shape, else the invariant it
    broke: (a) the same entries with the same date and provider lines; (b)
    no fewer sentences, an add exactly its own; (c) no other paragraph
    changed; (d) a swapped paragraph ends with its old citation; (e) no new
    line reads as a date or provider line."""
    if _heads(after) != _heads(before):
        return "a"
    n = _sentences(e.new)
    gain = ER.count_sentences(CL.body_of(after)) - ER.count_sentences(CL.body_of(before))
    if gain < 0 or (e.kind == "add" and (n < 1 or gain != n)):
        return "b"
    pb, pa = before.split("\n\n"), after.split("\n\n")
    if e.kind == "add":
        if len(pa) != len(pb) + 1 or e.new not in pa or [p for p in pa if p != e.new] != pb:
            return "c"
    else:
        diff = [i for i in range(max(len(pa), len(pb))) if i >= len(pa) or i >= len(pb) or pa[i] != pb[i]]
        if len(pa) != len(pb) or len(diff) != 1:
            return "c"
        if _last_cite(pa[diff[0]]) != _last_cite(pb[diff[0]]):
            return "d"
    if any(B.HEAD.match(ln.strip()) or _PROVIDER.search(ln) for ln in e.new.split("\n")):
        return "e"
    return ""


def _order(md: str, e: EC.Edit) -> tuple[int, int]:
    ents = B.entries_of(md)[1]
    k = int(e.anchor[1:]) - 1 if e.anchor[1:].isdigit() else len(ents)
    return k, ents[k].find(e.line) if k < len(ents) else 0


def edit_document(md: str, edits: list[EC.Edit]) -> tuple[str, Counter]:
    """Each edit made one at a time on a copy, in document order, and kept
    only when ``broken`` finds nothing; a reverted one is recorded
    ``breaks_structure``. Returns the text and the broken invariants."""
    broke: Counter = Counter()
    added: dict[str, list[str]] = {}
    for e in sorted(edits, key=lambda x: _order(md, x)):
        _pre, ents = B.entries_of(md)
        k = int(e.anchor[1:]) - 1 if e.anchor[1:].isdigit() else -1
        mine = added.setdefault(e.anchor, [])
        new = _edit_entry(ents[k], e, mine) if 0 <= k < len(ents) and md.count(ents[k]) == 1 else None
        if new is None:
            e.outcome = EC.QUOTE_NOT_ONCE
            continue
        after = md.replace(ents[k], new, 1)
        why = broken(md, after, e)
        if why:
            e.outcome = BREAKS_STRUCTURE
            broke[why] += 1
            continue
        md, e.outcome = after, "applied"
        if e.kind == "add":
            mine.append(e.new)
    return md, broke


BREAKS_STRUCTURE = "breaks_structure"


def apply(
    sr: StageRun, doc: Path, md: str, decisions: list[EC.Decision], veto: Callable[[], str] | None = None
) -> tuple[str, dict[str, str] | None, Counter]:
    """('', SMD's release when one was used, invariants broken) when the
    surviving edits were written (or none were due), else (why held, None,
    ...) with the document untouched. Writes the document once. `veto`
    (the update lane's, ``entry_audit_update``) is asked once every edit's
    outcome is known and before anything is written: a reason holds the run
    as the tripwire does."""
    edits = [d[1] for d in decisions if d[0] == "apply"]
    new, broke = edit_document(md, edits)
    kept = [e for e in edits if e.outcome == "applied"]
    before = ER.count_sentences(CL.body_of(md))
    rel = None
    if ER.over_share(len(kept), before):
        sha = doc_sha(md)
        why = f"tripwire: {len(kept)} edits on {before} sentences (more than {ER.EDIT_SHARE:.0%}); doc_sha {sha}"
        rel = release(sr, sha)
        if rel is None:
            for e in kept:
                e.outcome = "not applied: the run held at the tripwire"
            return why, None, broke
        sr.log(f"entry read: {why}; released by {rel['by']} ({rel['why'][:200]})")
    why = veto() if veto is not None else ""
    if why:
        for e in kept:
            e.outcome = "not applied: the run held on a line found wrong"
        return why, None, broke
    if new != md:
        write_atomic(doc, new)  # a crash never leaves half a document
        sync_entries(sr, md, new)
    return "", rel, broke


def sync_entries(sr: StageRun, md: str, new: str) -> None:
    """Best effort, as the audit's repair does: each edited entry in
    ``entries_scoped_final.md`` too, where its old text occurs exactly once,
    so a rule that finds an entry there verbatim (``review_evidence.leave_out``)
    still finds it. Entries never move or vanish here."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not p.is_file():
        return
    text = p.read_text(encoding="utf-8")
    for was, now in zip(B.entries_of(md)[1], B.entries_of(new)[1]):
        if now != was and text.count(was) == 1:
            text = text.replace(was, now, 1)
    write_atomic(p, text)


def _rows(reads: list[IO.Read], items: list[Item], decisions: list[EC.Decision]) -> list[dict[str, Any]]:
    """One row per unit: status and what came of its findings, no text. An
    applied decision's reason is its edit's outcome."""
    by: dict[str, dict[str, Any]] = {}
    for r in reads:
        n = sum(len(p.findings) for _x, _u, p in r.got)
        by[r.unit.key] = {"key": r.unit.key, "status": r.status, "findings": n, "applied": 0, "recorded": []}
        by[r.unit.key] |= _line_counts([p for _x, _u, p in r.got])
        if r.why:
            by[r.unit.key]["why"] = r.why
    for (_name, unit, f), d in zip(items, decisions):
        row = by[unit.key]
        why = d[1] if d[0] == "record" else (d[1].outcome or "not applied: the run held before applying")
        if why == "applied":
            row["applied"] += 1
        else:
            row["recorded"].append({"class": f.klass, "line": f.line, "exhibit": f.exhibit, "page": f.page,
                                    "reason": why})  # fmt: skip
    return list(by.values())


LINE_COUNTS = (
    "lines_ok",
    "lines_problem",
    "lines_unreadable",
    "problem_without_finding",
    "omitted_over_cap",
    "units_all_unreadable",
)


def _line_counts(parsed: list[ER.Parsed]) -> dict[str, int]:
    """A unit's line verdicts, its parts merged (a problem in any part is a
    problem; a line one part read is read, though another could not see it)."""
    rank = {"unreadable": 0, "ok": 1, "problem": 2}
    merged: dict[str, str] = {}
    for p in parsed:
        for lb, v in p.lines.items():
            merged[lb] = max(merged.get(lb, v), v, key=rank.__getitem__)
    out = {f"lines_{v}": sum(1 for x in merged.values() if x == v) for v in ("ok", "problem", "unreadable")}
    out["problem_without_finding"] = sum(p.problem_without_finding for p in parsed)
    out["omitted_over_cap"] = sum(p.omitted_over_cap for p in parsed)
    s_lines = [v for lb, v in merged.items() if lb.startswith("S")]
    out["units_all_unreadable"] = int(bool(s_lines) and all(v == "unreadable" for v in s_lines))  # still read
    return out


def record(sr: StageRun, ask: IO.Asker, doc: Path, pl: IO.Plan, reads: list[IO.Read], decisions: list,
           items: Any = (), held: str = "", released: dict[str, str] | None = None,
           broke: Counter | None = None, extra: dict[str, Any] | None = None) -> None:  # fmt: skip
    rows = _rows(reads, list(items), decisions)
    rec = EC.summary(pl.units, [p for r in reads for _n, _u, p in r.got], decisions, pl.gaps)
    rec |= {
        "model": ask.model,
        "complete": all(r.status == "read" for r in reads),
        "held": held,
        "applied": sum(row["applied"] for row in rows),
        "recorded": sum(len(row["recorded"]) for row in rows),
        "index_rows_malformed": pl.malformed,
        "calls": ask.calls,
        "doc_sha": doc_sha(doc.read_text(encoding="utf-8")),
        **{k: sum(row[k] for row in rows) for k in LINE_COUNTS},
        "rows": rows,
    }
    if released is not None:
        rec["tripwire_release"] = released
    rec["breaks_structure_by_invariant"] = dict(broke or {})
    rec |= extra or {}
    p = sr.slug_dir / "runs" / sr.unit.unit / IO.RECORD
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
