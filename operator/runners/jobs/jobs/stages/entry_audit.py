"""`audit` on the full chronology: each entry read against its own cited
pages before it is filed, and what that read can prove wrong corrected from
the page. Paid (the judgment tier, for the read and for its blind confirm).

WHY (2026-11-18). The per-paragraph audit (``stages/audit_loop.py``, still
the update lane's) passed every false statement of a real chronology: it
graded the paragraph a citation closes, so an entry's date and provider lines
were never read, an omission was never a defect, and the checker was shown
the claim's own values. This stage reads the ENTRY whole
(``medchron/update/entry_read.py``), has a second read that never sees the
proposed correction confirm each finding (``entry_confirm.py``), and leaves
every decision to code. In order:

1. The handwritten-records note leaves entries whose cited pages are all
   typed (``audit_loop._drop_typed_ntd``, unchanged).
2. READ each unit; an unparsed reply is asked once more, then a unit citing
   more than one page is read page by page; still unparsed, the stage HOLDS.
3. CONFIRM each finding blind; a confirm that errors twice is recorded
   ``confirm_failed``. A finding code records whatever the confirm says (a
   date, provider or another person's finding, the prior-history block) is
   not confirmed: the answer could change nothing.
4. DECIDE in code; a correction on a sentence SMD wrote by hand
   (``drops.json`` citation_edits) is recorded, never applied.
5. TRIPWIRE: more applied edits than ``entry_read.EDIT_SHARE`` of the
   document's sentences holds the job with the document untouched.
6. APPLY once through ``review_repair.apply``; an edit it rejects is
   recorded. The document is written only when it has no fewer sentences.
7. RECORD ``runs/<unit>/entry_read.json``: counts and per-unit rows, never
   the document's text. Recorded findings live only there, not in the notes.

Nothing is ever removed from the document here. Answers are saved before use
and a resume pays for none twice; ``ENTRY_AUDIT_REPLAY=1`` answers at $0
(``entry_audit_io``). Exits: 0 read and settled; 1 held (an entry not read,
or the tripwire); 75 the model could not be reached (the daemon retries);
9 answers pending, under the replay switch only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..deadline import is_transient
from ..limits import LimitHold
from ..medchron.audit import authored, claims as CL
from ..medchron.audit.run import AuditPaths, run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.page_mode import Modes
from ..medchron.update import entry_confirm as EC, entry_read as ER, review_repair as RR
from . import audit_loop, entry_audit_io as IO
from .base import StageRun, read_json
from .update_review import _client
from .update_review_io import _image_source

EXIT_HELD, EXIT_INFRA, EXIT_PENDING = 1, 75, 9
#: The exits past 1 (``dag.py`` writes exit 1's reason itself: the broker pins it).
EXITS = {
    EXIT_INFRA: (
        "failed",
        "audit incomplete (infrastructure): the model returned no answer; a later rerun reads again",
    ),
    EXIT_PENDING: ("failed", "audit replay: answers pending (ENTRY_AUDIT_REPLAY is set; a real run never sets it)"),
}
CONFIRM_FAILED = "confirm_failed"
SMD_AUTHORED = "a sentence SMD corrected by hand is never edited"
_NO_CONFIRM = object()  # decide records the finding whatever a confirm says
_FAILED = object()


@dataclass
class Ctx:
    sr: StageRun
    ask: IO.Asker
    images: Any
    modes: Any
    client: str
    workers: int


@dataclass
class Read:
    unit: ER.Unit
    status: str = "unread"  # read | unread | pending
    got: list[tuple[str, ER.Unit, ER.Parsed]] = field(default_factory=list)


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half for `jobs rehearse`: what the read and its confirms would cost."""
    return IO.projection_lines(sr)


def run(sr: StageRun) -> int:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    audit_loop._drop_typed_ntd(sr, AuditPaths(sr.slug_dir, sr.unit.unit))
    doc = rd / "final-chronology.md"
    md = doc.read_text(encoding="utf-8")
    pl = IO.plan(sr.slug_dir, sr.unit.unit, sr.cfg, md)
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    modes = Modes.open(sr.slug_dir, sr.unit.unit)
    ask = IO.Asker(sr, IO.model(sr.cfg), rd / IO.DIR, IO.replaying())
    c = Ctx(sr, ask, _image_source(sr), modes, _client(sr), workers)
    try:
        return _run(c, doc, md, pl)
    except Exception as exc:
        if not is_transient(exc):
            raise
        sr.log(f"entry read: the model could not be reached ({type(exc).__name__}); a later rerun reads again")
        return EXIT_INFRA
    finally:
        modes.close()


def _run(c: Ctx, doc: Path, md: str, pl: IO.Plan) -> int:
    sr = c.sr
    sr.log(f"entry read: {len(pl.units)} unit(s), {len(pl.gaps)} index gap(s), {c.workers} at once, {c.ask.model}")
    reads = _pool(c, pl.units, lambda u: _read_unit(c, u))
    if any(r.status == "pending" for r in reads):
        return _pending(c, "read")
    unread = [r.unit.key for r in reads if r.status != "read"]
    if unread:
        _record(c, doc, pl, reads, [], held=f"{len(unread)} unit(s) could not be read")
        sr.log(f"entry read HELD: {len(unread)} entr(ies) could not be read against their pages: {', '.join(unread)}")
        return EXIT_HELD
    items = [(f"{name}.f{i}", u, f) for r in reads for name, u, p in r.got for i, f in enumerate(p.findings)]
    confirms = _pool(c, items, lambda it: _confirm(c, *it))
    if c.ask.pending:
        return _pending(c, "confirm")
    decisions = _decide(c, md, pl, items, confirms)
    return _apply(c, doc, md, pl, reads, items, decisions)


def _pool(c: Ctx, items: list[Any], fn: Any) -> list[Any]:
    out: list[Any] = [None] * len(items)

    def task(i: int) -> Any:
        return lambda: out.__setitem__(i, fn(items[i]))

    run_pool([task(i) for i in range(len(items))], c.workers)
    return out


def _pending(c: Ctx, phase: str) -> int:
    c.sr.log(
        f"entry read REPLAY: {len(c.ask.pending)} {phase} answer(s) pending; requests written under "
        f"runs/{c.sr.unit.unit}/{IO.DIR}/ (*.request.json)"
    )
    return EXIT_PENDING


# ---- read ---------------------------------------------------------------------------
def _read_once(c: Ctx, unit: ER.Unit, name: str, lenient: bool) -> ER.Parsed | None:
    system, content = ER.request(unit, c.sr.cfg, c.client, c.images, c.modes)
    for attempt in (1, 2):
        text, stop = c.ask.ask(name, f"read{attempt}", system, content)
        if stop == "max_tokens":
            c.sr.log(f"  {name}: the reply ran out")
            continue
        try:
            return ER.parse(text, unit, lenient=lenient and attempt == 2)
        except RR.ReplyUnparsed as exc:
            c.sr.log(f"  {name}: {exc}")
    return None


def _read_unit(c: Ctx, unit: ER.Unit) -> Read:
    """The unit read once (and asked once more); else each page it cites
    read alone; else unread."""
    r = Read(unit)
    try:
        got = _read_once(c, unit, unit.key, lenient=False)
        if got is not None:
            r.status, r.got = "read", [(unit.key, unit, got)]
            return r
        cited = [pg for pg in unit.pages if pg in unit.cites()]
        if len(cited) < 2:
            return r
        c.sr.log(f"  {unit.key}: read page by page ({len(cited)} pages)")
        subs = [(f"{unit.key}~s{i}", replace(unit, pages=[pg])) for i, pg in enumerate(cited, 1)]
        parts = [_sub(c, name, u) for name, u in subs]
    except IO.Pending:
        r.status = "pending"
        return r
    if any(p == "pending" for p in parts):
        r.status = "pending"
    elif all(p is not None for p in parts):
        r.status, r.got = "read", [(n, u, p) for (n, u), p in zip(subs, parts)]
    return r


def _sub(c: Ctx, name: str, unit: ER.Unit) -> ER.Parsed | str | None:
    try:
        return _read_once(c, unit, name, lenient=True)
    except IO.Pending:
        return "pending"


# ---- confirm and decide ------------------------------------------------------------
def _recorded_anyway(f: ER.ReadFinding, unit: ER.Unit) -> bool:
    return f.record_only or unit.record_only or f.klass in EC.RECORD_CLASSES


def _confirm(c: Ctx, name: str, unit: ER.Unit, f: ER.ReadFinding) -> Any:
    if _recorded_anyway(f, unit):
        return _NO_CONFIRM
    system, content = EC.confirm_request(f, unit, c.sr.cfg, c.client, c.images, c.modes)
    for attempt in (1, 2):
        try:
            text, _stop = c.ask.ask(name, f"confirm{attempt}", system, content)
            return EC.parse_confirm(text)
        except IO.Pending:
            return None
        except Exception as exc:
            if isinstance(exc, LimitHold):
                raise
            c.sr.log(f"  {name}: confirm {attempt} failed: {type(exc).__name__}: {str(exc)[:160]}")
    return _FAILED


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


def _decide(c: Ctx, md: str, pl: IO.Plan, items: list[Any], confirms: list[Any]) -> list[EC.Decision]:
    others = RR.anchor_entries(md, pl.keep, pl.heads)
    smd = _smd(c.sr)
    out: list[EC.Decision] = []
    for (_name, unit, f), conf in zip(items, confirms):
        if conf is _FAILED:
            out.append(("record", CONFIRM_FAILED))
            continue
        conf = None if conf is _NO_CONFIRM else conf
        d = EC.decide(f, conf, EC.usable_text(c.modes, f.exhibit, f.page), unit, others)
        if d[0] == "apply" and d[1].action == "replace" and _by_smd(d[1], unit, smd):
            d = ("record", SMD_AUTHORED)
        out.append(d)
    return out


# ---- apply and record --------------------------------------------------------------
def _apply(c: Ctx, doc: Path, md: str, pl: IO.Plan, reads: list[Read], items: list[Any], decisions: list) -> int:
    edits = EC.to_repair_findings(decisions)
    before = ER.count_sentences(CL.body_of(md))
    if ER.over_share(len(edits), before):
        why = f"tripwire: {len(edits)} edits on {before} sentences (more than {ER.EDIT_SHARE:.0%})"
        _record(c, doc, pl, reads, decisions, items, held=why)
        c.sr.log(f"entry read HELD: {why}; the document is unchanged")
        return EXIT_HELD
    old = {cl.anchor: cl.text for e in RR.anchor_entries(md, pl.keep, pl.heads) for cl in e.claims}
    new = RR.apply(md, edits, pl.keep, pl.heads, shown=EC.shown(pl.units))[0] if edits else md
    after = ER.count_sentences(CL.body_of(new))
    if after < before:
        for e in edits:
            e.outcome = "not written: the edits together removed sentences" if e.outcome == "applied" else e.outcome
        _record(c, doc, pl, reads, decisions, items, held=f"the edits removed sentences ({before} -> {after})")
        c.sr.log(f"entry read HELD: applying the edits would remove sentences ({before} -> {after}); nothing written")
        return EXIT_HELD
    if new != md:
        doc.write_text(new, encoding="utf-8")
        _sync_entries(c.sr, [(old[e.anchor], e.corrected) for e in edits if e.outcome == "applied" and e.anchor in old])
    _record(c, doc, pl, reads, decisions, items)
    n = sum(e.outcome == "applied" for e in edits)
    c.sr.log(f"entry read: {len(items)} finding(s), {n} applied, {len(items) - n} recorded; {c.ask.calls} call(s)")
    return 0


def _sync_entries(sr: StageRun, pairs: list[tuple[str, str]]) -> None:
    """Best effort, as the audit's repair does: each corrected sentence in
    ``entries_scoped_final.md`` too, where it occurs exactly once."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not pairs or not p.is_file():
        return
    text = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if text.count(old) == 1:
            text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")


def _rows(reads: list[Read], items: list[Any], decisions: list[EC.Decision]) -> list[dict[str, Any]]:
    """One row per unit: status and what came of its findings, no text. An
    applied decision's reason is ``review_repair.apply``'s outcome for it."""
    by: dict[str, dict[str, Any]] = {}
    for r in reads:
        n = sum(len(p.findings) for _x, _u, p in r.got)
        by[r.unit.key] = {"key": r.unit.key, "status": r.status, "findings": n, "applied": 0, "recorded": []}
    for (_name, unit, f), d in zip(items, decisions):
        row = by[unit.key]
        why = d[1] if d[0] == "record" else (d[1].outcome or "not applied: the run held before applying")
        if why == "applied":
            row["applied"] += 1
        else:
            row["recorded"].append({"class": f.klass, "anchor": f.anchor, "exhibit": f.exhibit, "page": f.page,
                                    "reason": why})  # fmt: skip
    return list(by.values())


def _record(
    c: Ctx, doc: Path, pl: IO.Plan, reads: list[Read], decisions: list, items: Any = (), held: str = ""
) -> None:
    rows = _rows(reads, list(items), decisions)
    rec = EC.summary(pl.units, [p for r in reads for _n, _u, p in r.got], decisions, pl.gaps)
    rec |= {
        "model": c.ask.model,
        "complete": all(r.status == "read" for r in reads),
        "held": held,
        "applied": sum(row["applied"] for row in rows),
        "recorded": sum(len(row["recorded"]) for row in rows),
        "index_rows_malformed": pl.malformed,
        "calls": c.ask.calls,
        "doc_sha": hashlib.sha256(doc.read_text(encoding="utf-8").encode()).hexdigest(),
        "rows": rows,
    }
    p = c.sr.slug_dir / "runs" / c.sr.unit.unit / IO.RECORD
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
