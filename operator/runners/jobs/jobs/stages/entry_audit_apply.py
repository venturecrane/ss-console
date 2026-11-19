"""The entry audit's decide, apply and record steps (``stages/entry_audit.py``
is the stage; split out for the module-size ceiling). $0, no model.

THE TRIPWIRE AND ITS RELEASE. More applied edits than ``entry_read.EDIT_SHARE``
of the document's sentences holds the job with the document untouched. SMD
releases one such hold, after reading the record, by writing (as root)
``runs/<unit>/entry_read/tripwire-release.json`` = ``{"by": "<who>",
"why": "<what was checked>", "doc_sha": "<the doc_sha the hold line logged>"}``;
the resumed stage then applies the edits and records the release in
``entry_read.json`` (``tripwire_release``). A release with "by" or "why"
empty, or for another document (round 3, 2026-11-18), is ignored and logged.

DUPLICATE ADDS. Two parts of one split entry can each report the same left
out sentence: an added sentence equal to, or contained in, another added to
the same entry is recorded ``duplicate_add``, never added twice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..medchron.audit import authored, claims as CL
from ..medchron.update import entry_confirm as EC, entry_read as ER, review_repair as RR
from . import entry_audit_io as IO
from .base import StageRun, read_json

CONFIRM_FAILED, DUPLICATE_ADD = "confirm_failed", "duplicate_add"
SMD_AUTHORED = "a sentence SMD corrected by hand is never edited"
NO_CONFIRM = object()  # decide records the finding whatever a confirm says
FAILED = object()  # the confirm errored twice
Item = tuple[str, ER.Unit, ER.ReadFinding]


def needs_confirm(f: ER.ReadFinding, unit: ER.Unit) -> bool:
    """False for a finding decide records whatever a confirm says."""
    return not (f.record_only or unit.record_only or f.klass in EC.RECORD_CLASSES)


def _smd(sr: StageRun) -> tuple[set[tuple[int, str]], set[str]]:
    name = f"drops-{sr.unit.unit}.json" if sr.job.joint else "drops.json"
    edits = (read_json(sr.slug_dir / name, {}) or {}).get("citation_edits") or []
    return authored.authored_texts(edits), authored.inline_texts(edits)


def _by_smd(edit: RR.Finding, unit: ER.Unit, smd: tuple[set[tuple[int, str]], set[str]]) -> bool:
    claim = next((x for x in (unit.entry.claims if unit.entry else []) if x.anchor == edit.anchor), None)
    if claim is None:
        return False
    flat = " ".join(CL.CITE.sub("", claim.text).split())
    return any(ex == claim.exhibit and t and flat.endswith(t) for ex, t in smd[0]) or any(t in flat for t in smd[1])


def decide(sr: StageRun, modes: Any, md: str, pl: IO.Plan, items: list[Item], confirms: list[Any]) -> list[EC.Decision]:
    others = RR.anchor_entries(md, pl.keep, pl.heads)
    smd = _smd(sr)
    out: list[EC.Decision] = []
    for (_name, unit, f), conf in zip(items, confirms):
        if conf is FAILED:
            out.append(("record", CONFIRM_FAILED))
            continue
        conf = None if conf is NO_CONFIRM else conf
        d = EC.decide(f, conf, EC.usable_text(modes, f.exhibit, f.page), unit, others)
        if d[0] == "apply" and d[1].action == "replace" and _by_smd(d[1], unit, smd):
            d = ("record", SMD_AUTHORED)
        out.append(d)
    return dedupe_adds(out)


def dedupe_adds(decisions: list[EC.Decision]) -> list[EC.Decision]:
    """An add whose sentence another add on the same entry already carries
    (equal, or one inside the other) is recorded ``duplicate_add``."""
    kept: dict[str, list[str]] = {}
    out: list[EC.Decision] = []
    for d in decisions:
        if d[0] == "apply" and d[1].action == "add":
            text = EC._norm(CL.CITE.sub("", d[1].corrected))
            mine = kept.setdefault(d[1].anchor, [])
            if any(text in t or t in text for t in mine):
                d = ("record", DUPLICATE_ADD)
            else:
                mine.append(text)
        out.append(d)
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


def apply(
    sr: StageRun, modes: Any, doc: Path, md: str, pl: IO.Plan, decisions: list[EC.Decision]
) -> tuple[str, dict[str, str] | None]:
    """('', SMD's release when one was used) when the edits were applied (or
    none were due), else (why held, None) with the document untouched.
    Writes the document once: added sentences first, each after its line,
    then the replacements through ``review_repair.apply`` on their claims
    found again by text."""
    edits = EC.to_repair_findings(decisions)
    before = ER.count_sentences(CL.body_of(md))
    rel = None
    if ER.over_share(len(edits), before):
        sha = doc_sha(md)
        why = f"tripwire: {len(edits)} edits on {before} sentences (more than {ER.EDIT_SHARE:.0%}); doc_sha {sha}"
        rel = release(sr, sha)
        if rel is None:
            return why, None
        sr.log(f"entry read: {why}; released by {rel['by']} ({rel['why'][:200]})")
    mid = _insert_adds(md, [e for e in edits if e.action == "add"], pl)
    rest = _reanchor(md, mid, [e for e in edits if e.action != "add"], pl)
    new = RR.apply(mid, rest, pl.keep, pl.heads, shown=EC.shown(pl.units))[0] if rest else mid
    after = ER.count_sentences(CL.body_of(new))
    if after < before:
        for e in edits:
            e.outcome = "not written: the edits together removed sentences" if e.outcome == "applied" else e.outcome
        return f"the edits removed sentences ({before} -> {after})", None
    if new != md:
        doc.write_text(new, encoding="utf-8")
        sync_entries(sr, md, new, pl)
    return "", rel


def _insert_adds(md: str, adds: list[RR.Finding], pl: IO.Plan) -> str:
    """Each added sentence on a line of its own right after the labelled
    line it names (``fid``), when it is grounded on a page its entry cites
    and that line is found once in its entry."""
    units = {u.anchor: u for u in pl.units}
    shown = EC.shown(pl.units)
    for e in adds:
        ents = {x.anchor: x for x in RR.anchor_entries(md, pl.keep, pl.heads)}
        ent, line = ents.get(e.anchor), units[e.anchor].lines.get(e.fid, "") if e.anchor in units else ""
        why = RR.grounded_text(e, pl.keep) or (
            "" if (e.exhibit, e.page) in shown.get(e.anchor, []) else "not a page of a document the entry cites"
        )
        if not why and (ent is None or not line or ent.text.count(line) != 1 or md.count(ent.text) != 1):
            why = "the line to add after is not found once in its entry"
        if why:
            e.outcome = why
            continue
        md = md.replace(ent.text, ent.text.replace(line, f"{line}\n{e.corrected}", 1), 1)
        e.outcome = "applied"
    return md


def _reanchor(old: str, new: str, edits: list[RR.Finding], pl: IO.Plan) -> list[RR.Finding]:
    """The replacements re-anchored onto the document with its added
    sentences in: a claim keeps its text, so it is found again by it."""
    if old == new:
        return edits
    was = {c.anchor: c.text for e in RR.anchor_entries(old, pl.keep, pl.heads) for c in e.claims}
    now: dict[tuple[str, str], list[str]] = {}
    for e in RR.anchor_entries(new, pl.keep, pl.heads):
        for c in e.claims:
            now.setdefault((e.anchor, c.text), []).append(c.anchor)
    for e in edits:
        hit = now.get((e.anchor.split(".")[0], was.get(e.anchor, "")), [])
        if len(hit) == 1:
            e.anchor = hit[0]
        else:
            e.outcome = "the sentence is not found once after the added sentences went in"
    return edits


def sync_entries(sr: StageRun, md: str, new: str, pl: IO.Plan) -> None:
    """Best effort, as the audit's repair does: each edited entry in
    ``entries_scoped_final.md`` too, where its old text occurs exactly once,
    so a rule that finds an entry there verbatim (``review_evidence.leave_out``)
    still finds it. Entries never move here (no date is applied)."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not p.is_file():
        return
    old = {e.anchor: e.text for e in RR.anchor_entries(md, pl.keep, pl.heads)}
    text = p.read_text(encoding="utf-8")
    for e in RR.anchor_entries(new, pl.keep, pl.heads):
        was = old.get(e.anchor, e.text)
        if e.text != was and text.count(was) == 1:
            text = text.replace(was, e.text, 1)
    p.write_text(text, encoding="utf-8")


def _rows(reads: list[IO.Read], items: list[Item], decisions: list[EC.Decision]) -> list[dict[str, Any]]:
    """One row per unit: status and what came of its findings, no text. An
    applied decision's reason is ``review_repair.apply``'s outcome for it."""
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
            row["recorded"].append({"class": f.klass, "anchor": f.anchor, "exhibit": f.exhibit, "page": f.page,
                                    "reason": why})  # fmt: skip
    return list(by.values())


LINE_COUNTS = ("lines_ok", "lines_problem", "lines_unreadable", "problem_without_finding", "omitted_over_cap")


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
    return out


def record(sr: StageRun, ask: IO.Asker, doc: Path, pl: IO.Plan, reads: list[IO.Read], decisions: list,
           items: Any = (), held: str = "", released: dict[str, str] | None = None) -> None:  # fmt: skip
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
    p = sr.slug_dir / "runs" / sr.unit.unit / IO.RECORD
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
