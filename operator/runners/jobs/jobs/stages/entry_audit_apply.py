"""The entry audit's decide, apply and record steps (``stages/entry_audit.py``
is the stage; split out for the module-size ceiling). $0, no model.

THE TRIPWIRE AND ITS RELEASE. More applied edits than ``entry_read.EDIT_SHARE``
of the document's sentences holds the job with the document untouched. SMD
releases one such hold, after reading the record, by writing (as root)
``runs/<unit>/entry_read/tripwire-release.json`` = ``{"by": "<who>",
"why": "<what was checked>"}``; the resumed stage then applies the edits and
records the release in ``entry_read.json`` (``tripwire_release``). A release
with either field empty is no release.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from ..medchron.audit import authored, claims as CL
from ..medchron.update import entry_confirm as EC, entry_read as ER, review_repair as RR
from . import entry_audit_io as IO
from .base import StageRun, read_json

CONFIRM_FAILED = "confirm_failed"
SMD_AUTHORED = "a sentence SMD corrected by hand is never edited"
NO_CONFIRM = object()  # decide records the finding whatever a confirm says
FAILED = object()  # the confirm errored twice
Item = tuple[str, ER.Unit, ER.ReadFinding]


def needs_confirm(f: ER.ReadFinding, unit: ER.Unit, today: date | None) -> bool:
    """False for a finding decide records whatever a confirm says."""
    if f.record_only or unit.record_only or f.klass in ("provider", "another_person"):
        return False
    return f.klass != "date" or EC.dated_after(unit, today)


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


def decide(
    sr: StageRun, modes: Any, md: str, pl: IO.Plan, items: list[Item], confirms: list[Any], today: date | None
) -> list[EC.Decision]:
    others = RR.anchor_entries(md, pl.keep, pl.heads)
    smd = _smd(sr)
    out: list[EC.Decision] = []
    for (_name, unit, f), conf in zip(items, confirms):
        if conf is FAILED:
            out.append(("record", CONFIRM_FAILED))
            continue
        conf = None if conf is NO_CONFIRM else conf
        d = EC.decide(f, conf, EC.usable_text(modes, f.exhibit, f.page), unit, others, today)
        if d[0] == "apply" and d[1].action == "replace" and _by_smd(d[1], unit, smd):
            d = ("record", SMD_AUTHORED)
        out.append(d)
    return out


def release(sr: StageRun) -> dict[str, str] | None:
    """SMD's release of the tripwire, when written with both fields."""
    got = read_json(sr.slug_dir / "runs" / sr.unit.unit / IO.DIR / IO.RELEASE, {}) or {}
    by, why = (str(got.get(k) or "").strip() if isinstance(got, dict) else "" for k in ("by", "why"))
    return {"by": by, "why": why} if by and why else None


def apply(
    sr: StageRun, modes: Any, doc: Path, md: str, pl: IO.Plan, decisions: list[EC.Decision]
) -> tuple[str, dict[str, str] | None]:
    """('', SMD's release when one was used) when the edits were applied (or
    none were due), else (why held, None) with the document untouched.
    Writes the document once."""
    edits = EC.to_repair_findings(decisions)
    before = ER.count_sentences(CL.body_of(md))
    rel = None
    if ER.over_share(len(edits), before):
        why = f"tripwire: {len(edits)} edits on {before} sentences (more than {ER.EDIT_SHARE:.0%})"
        rel = release(sr)
        if rel is None:
            return why, None
        sr.log(f"entry read: {why}; released by {rel['by']} ({rel['why'][:200]})")
    evidence = lambda x, p: EC.usable_text(modes, x, p)  # noqa: E731 - the redate's page text
    new = RR.apply(md, edits, pl.keep, pl.heads, evidence=evidence, shown=EC.shown(pl.units))[0] if edits else md
    after = ER.count_sentences(CL.body_of(new))
    if after < before:
        for e in edits:
            e.outcome = "not written: the edits together removed sentences" if e.outcome == "applied" else e.outcome
        return f"the edits removed sentences ({before} -> {after})", None
    if new != md:
        doc.write_text(new, encoding="utf-8")
        sync_entries(sr, md, [e for e in edits if e.outcome == "applied"], pl)
    return "", rel


def sync_entries(sr: StageRun, md: str, edits: list[RR.Finding], pl: IO.Plan) -> None:
    """Best effort, as the audit's repair does: each edited entry in
    ``entries_scoped_final.md`` too (a replacement, an added sentence, a new
    date), where its old text occurs exactly once, so a rule that finds an
    entry there verbatim (``review_evidence.leave_out``) still finds it."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not edits or not p.is_file():
        return
    ents = RR.anchor_entries(md, pl.keep, pl.heads)
    old = {e.anchor: e.text for e in ents}
    claims = {c.anchor: c.text for e in ents for c in e.claims}
    new = dict(old)
    for e in edits:
        a = e.anchor.split(".")[0]
        if e.action == "replace" and e.anchor in claims:
            new[a] = new[a].replace(claims[e.anchor], e.corrected, 1)
        elif e.action == "add":
            new[a] = RR._add(new[a], e.corrected, pl.heads)
        elif e.action == "redate":
            new[a] = e.corrected + new[a][10:]
    text = p.read_text(encoding="utf-8")
    for a, t in new.items():
        if t != old[a] and text.count(old[a]) == 1:
            text = text.replace(old[a], t, 1)
    p.write_text(text, encoding="utf-8")


def _rows(reads: list[IO.Read], items: list[Item], decisions: list[EC.Decision]) -> list[dict[str, Any]]:
    """One row per unit: status and what came of its findings, no text. An
    applied decision's reason is ``review_repair.apply``'s outcome for it."""
    by: dict[str, dict[str, Any]] = {}
    for r in reads:
        n = sum(len(p.findings) for _x, _u, p in r.got)
        by[r.unit.key] = {"key": r.unit.key, "status": r.status, "findings": n, "applied": 0, "recorded": []}
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
        "doc_sha": hashlib.sha256(doc.read_text(encoding="utf-8").encode()).hexdigest(),
        "rows": rows,
    }
    if released is not None:
        rec["tripwire_release"] = released
    p = sr.slug_dir / "runs" / sr.unit.unit / IO.RECORD
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
