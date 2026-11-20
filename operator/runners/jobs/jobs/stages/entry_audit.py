"""`audit` on the full chronology: each entry read against its own cited
pages before it is filed, and what that read can prove wrong corrected from
the page. Paid (the judgment tier, for the read and for the second reader's
verify).

WHY (2026-11-18). The per-paragraph audit (``stages/audit_loop.py``, still
the update lane's) passed every false statement of a real chronology: it
graded the paragraph a citation closes, so an entry's date and provider lines
were never read, an omission was never a defect, and the checker was shown
the claim's own values. This stage reads the ENTRY whole, line by line
(``medchron/update/entry_read.py``), has a second reader VERIFY each proposed
correction against the page pictures (``entry_confirm.py``, round 4: it
replaced three rounds of token rules), and leaves every decision to code:

1. The handwritten-records note leaves entries whose cited pages are all
   typed (``audit_loop._drop_typed_ntd``, unchanged).
2. READ each unit. A unit whose cited page cannot be shown (past its
   exhibit's end, or no picture and no text) is UNREAD and never sent. An
   unparsed reply is asked once more, then a unit citing more than one page
   is read page by page, each page told it is shown alone (so is a request
   the API refuses as too large); still unparsed, or refused by the API for
   another reason, the unit is unread and the stage HOLDS, naming each unit
   and why. A resume asks again only for what was not read.
3. PROPOSE: each finding becomes the whole line it would write, or a reason
   code (``entry_audit_apply``).
4. Before any verify, the verify phase is priced from the real proposals and
   checked by the driver's own limits: past the job's cap it is the firm's
   ordinary cap hold (``LimitHold``).
5. VERIFY each proposal; one the API refuses twice is ``verify_failed`` (a
   network failure exits 75 and the daemon retries).
6. DECIDE: apply only a line found wrong AND a proposed sentence found
   supported on a page the verify was shown.
7. TRIPWIRE: more applied edits than ``entry_read.EDIT_SHARE`` of the
   document's sentences holds the job with the document untouched, unless
   SMD wrote a release (``entry_audit_apply``'s docstring).
8. APPLY: a whole labelled line swapped, or a new paragraph added after the
   paragraph holding its line, one edit at a time on a copy; an edit that
   breaks the document's shape is reverted alone (``breaks_structure``,
   round 5) and the rest go on. The document is written once.
9. RECORD ``runs/<unit>/entry_read.json``: counts and per-unit rows with a
   reason code for each recorded finding, never the document's text.

Nothing is ever removed from the document here. Answers are saved before use
and a resume pays for none twice; ``ENTRY_AUDIT_REPLAY=1`` answers at $0
(``entry_audit_io``). Exits: 0 read and settled; 1 held (an entry not read,
the tripwire, or in an update a line found wrong and not corrected); 3 the
stage crashed outside a model call (a defect, never read as a hold); 75 the
model could not be reached (the daemon retries); 9 answers pending, under
the replay switch only.

THE UPDATE LANE (2026-11-20) runs this same stage as its ``audit``
(``dag_update.py``), on the audit document ``update_collect`` writes to
``runs/<unit>/final-chronology.md``: the new entries, the prior history they
add, and the diagnosis and provider-name claims, never a delivered entry.
There, ``entry_audit_update`` acts on the verdict before anything is written.

REPLAYING A SAVED UPDATE AT $0. In a copy of the job's matter directory
(``<slug dir>``): (1) rebuild the audit document with
``medchron/update/collect.audit_document`` (as ``update_collect`` calls it) reading ``runs/<unit>/update/base.md``,
``update/new_entries.md``, ``update/pmh.md`` when present, the claims in
``update/icd_claims.json`` and ``update/provider_claims.json``, and the
plan's folder, written to ``runs/<unit>/final-chronology.md`` (what
``run_collect`` and ``run_summary`` write); (2) run this stage on a job whose
``update`` is set, with ``ENTRY_AUDIT_REPLAY=1`` (and
``ENTRY_READ_UPDATE_PAGES=cited`` to show cited pages only); (3) each missing
answer's request is dumped as ``runs/<unit>/entry_read/<name>.<kind>.request.json``
and the stage exits 9; (4) write each answer as ``<name>.<kind>.json`` with the
request's hash (``entry_audit_io``'s docstring), run again, and repeat until
the stage exits 0 or 1. The verdict is ``runs/<unit>/entry_read.json`` and
``runs/<unit>/entry_read/update-verdict.json``.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ..deadline import is_transient
from ..limits import LimitHold
from ..medchron.audit.run import AuditPaths, run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.page_mode import Modes
from ..medchron.update import entry_confirm as EC, entry_read as ER, review_pages as RV, review_repair as RR
from . import audit_loop, entry_audit_apply as AP, entry_audit_io as IO, entry_audit_update as EU
from .base import StageRun
from .read_inputs import client_line, image_source

EXIT_HELD, EXIT_DEFECT, EXIT_INFRA, EXIT_PENDING = 1, 3, 75, 9
#: The exits past 1 (``dag.py`` writes exit 1's reason itself: the broker pins it).
EXITS = {
    EXIT_DEFECT: ("failed", "audit: the stage crashed (a defect)"),
    EXIT_INFRA: (
        "failed",
        "audit incomplete (infrastructure): the model returned no answer; a later rerun reads again",
    ),
    EXIT_PENDING: ("failed", "audit replay: answers pending (ENTRY_AUDIT_REPLAY is set; a real run never sets it)"),
}
PAGE_MISSING = "a cited page could not be shown"


@dataclass
class Ctx:
    sr: StageRun
    ask: IO.Asker
    images: Any
    modes: Any
    client: str
    workers: int
    npages: Callable[[int], int]
    update: bool = False  # the update lane: ``entry_audit_update`` acts on the verdict


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half for `jobs rehearse`: what the read and its verifies would cost."""
    return IO.projection_lines(sr)


def run(sr: StageRun) -> int:
    """The stage, with every way out mapped: a limit hold goes to the driver,
    the network to 75, any other exception to 3 (a defect, never a hold)."""
    try:
        return _start(sr)
    except LimitHold:
        raise
    except Exception as exc:  # noqa: BLE001 - a crash is the defect exit, never read as a hold
        if is_transient(exc):
            sr.log(f"entry read: the model could not be reached ({type(exc).__name__}); a later rerun reads again")
            return EXIT_INFRA
        sr.log(f"entry read CRASHED ({type(exc).__name__}: {str(exc)[:300]})")
        sr.log(traceback.format_exc()[-1500:])
        return EXIT_DEFECT


def _start(sr: StageRun) -> int:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    audit_loop._drop_typed_ntd(sr, AuditPaths(sr.slug_dir, sr.unit.unit))
    doc = rd / "final-chronology.md"
    md = doc.read_text(encoding="utf-8")
    update = sr.job.update is not None
    if update:
        EU.path(sr.slug_dir, sr.unit.unit).unlink(missing_ok=True)  # this run's verdict, never an earlier one's
    pl = IO.plan(sr.slug_dir, sr.unit.unit, sr.cfg, md, update)
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    modes = Modes.open(sr.slug_dir, sr.unit.unit)
    ask = IO.Asker(sr, IO.model(sr.cfg), rd / IO.DIR, IO.replaying())
    c = Ctx(sr, ask, image_source(sr), modes, client_line(sr), workers, pl.npages, update)
    try:
        return _run(c, doc, md, pl)
    finally:
        modes.close()


def _run(c: Ctx, doc: Path, md: str, pl: IO.Plan) -> int:
    sr = c.sr
    sr.log(f"entry read: {len(pl.units)} unit(s), {len(pl.gaps)} index gap(s), {c.workers} at once, {c.ask.model}")
    reads = _pool(c, pl.units, lambda u: _read_unit(c, u))
    if any(r.status == "pending" for r in reads):
        return _pending(c, "read")
    unread = [r for r in reads if r.status != "read"]
    if unread:
        AP.record(sr, c.ask, doc, pl, reads, [], held=f"{len(unread)} unit(s) could not be read")
        named = "; ".join(f"{r.unit.key} ({r.why or 'not read'})" for r in unread)
        sr.log(f"entry read HELD: {len(unread)} entr(ies) could not be read against their pages: {named}")
        return EXIT_HELD
    items = [(f"{name}.f{i}", u, f) for r in reads for name, u, p in r.got for i, f in enumerate(p.findings)]
    proposals = AP.propose(sr, items)
    _check_cost(c, [p for p in proposals if isinstance(p, EC.Proposal)])
    verifies = _pool(c, list(zip(items, proposals)), lambda ip: _verify(c, ip[0][0], ip[1]))
    if c.ask.pending:
        return _pending(c, "verify")
    decisions = AP.decide(proposals, verifies)
    v = EU.Verdict()

    def veto() -> str:
        got = EU.verdict(items, decisions)
        v.unresolved, v.withheld = got.unresolved, got.withheld
        return v.hold_line()

    held, released, broke = AP.apply(sr, doc, md, decisions, veto if c.update else None)
    extra = _update_extra(c, v, items, decisions)
    AP.record(sr, c.ask, doc, pl, reads, decisions, items, held=held, released=released, broke=broke, extra=extra)
    if held:
        sr.log(f"entry read HELD: {held}; the document is unchanged")
        return EXIT_HELD
    n = sum(1 for d in decisions if d[0] == "apply" and d[1].outcome == "applied")
    sr.log(f"entry read: {len(items)} finding(s), {n} applied, {len(items) - n} recorded; {c.ask.calls} call(s)")
    return 0


def _update_extra(c: Ctx, v: EU.Verdict, items: list[AP.Item], decisions: list[EC.Decision]) -> dict[str, Any] | None:
    """The update lane's verdict, written beside the answers and summed into
    the record (counts only) on EVERY run that reached a decision, held or
    not, so a $0 replay reports how often the rule would hold; None on the
    full chronology. When the tripwire held first, the veto was never asked:
    the lines that would hold are counted here (no edit applied)."""
    if not c.update:
        return None
    if not (v.unresolved or v.withheld):
        got = EU.verdict(items, decisions)
        v.unresolved, v.withheld = got.unresolved, got.withheld
    EU.write(c.sr.slug_dir, c.sr.unit.unit, v, EU.removed_values(decisions))
    if v.withheld:
        c.sr.log(f"entry read: {len(v.withheld)} diagnosis claim(s) found wrong and not corrected get no table row")
    return {
        "unresolved": sum(v.unresolved.values()),
        "unresolved_by_class": dict(v.unresolved),
        "diagnosis_rows_withheld": len(v.withheld),
    }


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


def _check_cost(c: Ctx, proposals: list[EC.Proposal]) -> None:
    """The verify phase, priced from the real proposals and the pictures each
    verify shows, through the driver's own limits: past the job's cap it
    raises the same ``LimitHold`` (the firm's cap hold) as any paid stage.
    Not under the $0 replay."""
    if c.ask.replay or not proposals:
        return
    usd = IO.verify_usd(c.sr, proposals)
    if usd is not None:
        c.sr.log(f"entry read: the verify phase is {len(proposals)} verif(ies), ~{usd:.2f} USD")
        IO.check_limits(c.sr, usd)


# ---- read ---------------------------------------------------------------------------
def _parses(unit: ER.Unit, lenient: bool) -> IO.Valid:
    def ok(text: str) -> bool:
        try:
            ER.parse(text, unit, lenient=lenient)
        except RR.ReplyUnparsed:
            return False
        return True

    return ok


def _saved(c: Ctx, unit: ER.Unit, name: str, lenient: bool) -> ER.Parsed | None:
    """A good answer on file for either attempt of this read, or None."""
    system, content = ER.request(unit, c.sr.cfg, c.client, c.images, c.modes)
    for attempt in (1, 2):
        lax = lenient and attempt == 2
        text = c.ask.peek(name, f"read{attempt}", system, content, _parses(unit, lax))
        if text is not None and _parses(unit, lax)(text):
            return ER.parse(text, unit, lenient=lax)
    return None


def _read_once(c: Ctx, unit: ER.Unit, name: str, lenient: bool) -> ER.Parsed | None:
    """The unit's read: a good answer on file, else asked (and asked once
    more when the reply ran out or did not parse)."""
    got = _saved(c, unit, name, lenient)
    if got is not None:
        return got
    system, content = ER.request(unit, c.sr.cfg, c.client, c.images, c.modes)
    for attempt in (1, 2):
        lax = lenient and attempt == 2
        text, stop = c.ask.ask(name, f"read{attempt}", system, content, _parses(unit, lax))
        if stop == "max_tokens":
            c.sr.log(f"  {name}: the reply ran out")
            continue
        try:
            return ER.parse(text, unit, lenient=lax)
        except RR.ReplyUnparsed as exc:
            c.sr.log(f"  {name}: {exc}")
    return None


def _missing(c: Ctx, unit: ER.Unit) -> bool:
    """True when a page the unit cites cannot be shown to the read."""
    for ex, p in unit.cited:
        if not 1 <= p <= c.npages(ex) or not RV.page_blocks(ex, p, "", c.images, c.modes):
            return True
    return False


def _subs(unit: ER.Unit) -> list[tuple[str, ER.Unit]]:
    """The unit's cited pages, each as a unit of its own (``N3~s1``...)."""
    cited = [pg for pg in unit.pages if pg in set(unit.cited)]
    return [(f"{unit.key}~s{i}", replace(unit, pages=[pg], cited=[pg], sub=True)) for i, pg in enumerate(cited, 1)]


def _read_unit(c: Ctx, unit: ER.Unit) -> IO.Read:
    """The unit read once (and asked once more); else each page it cites
    read alone; else unread. A resume whose page-by-page answers are all on
    file uses them and never asks for the whole unit again."""
    r = IO.Read(unit)
    if _missing(c, unit):
        r.why = PAGE_MISSING
        c.sr.log(f"  {unit.key}: {PAGE_MISSING}; not read")
        return r
    subs = _subs(unit)
    if _saved(c, unit, unit.key, lenient=False) is None and len(subs) > 1:
        saved = [_saved(c, u, n, lenient=True) for n, u in subs]
        if all(p is not None for p in saved):
            r.status, r.got = "read", [(n, u, p) for (n, u), p in zip(subs, saved) if p is not None]
            return r
    try:
        got = _read_once(c, unit, unit.key, lenient=False)
    except IO.Pending:
        r.status = "pending"
        return r
    except IO.ModelError as exc:
        c.sr.log(f"  {unit.key}: the API refused the read ({exc})")
        if not exc.too_large():
            r.why = "the API refused the read"
            return r
        got = None
    if got is not None:
        r.status, r.got = "read", [(unit.key, unit, got)]
        return r
    return _split(c, subs, r)


def _split(c: Ctx, subs: list[tuple[str, ER.Unit]], r: IO.Read) -> IO.Read:
    if len(subs) < 2:
        r.why = "the reply did not parse"
        return r
    c.sr.log(f"  {r.unit.key}: read page by page ({len(subs)} pages)")
    parts = [_sub(c, name, u) for name, u in subs]
    if any(p == "pending" for p in parts):
        r.status = "pending"
    elif all(p is not None for p in parts):
        r.status, r.got = "read", [(n, u, p) for (n, u), p in zip(subs, parts) if isinstance(p, ER.Parsed)]
    else:
        r.why = "a page read alone did not parse"
    return r


def _sub(c: Ctx, name: str, unit: ER.Unit) -> ER.Parsed | str | None:
    try:
        return _read_once(c, unit, name, lenient=True)
    except IO.Pending:
        return "pending"
    except IO.ModelError as exc:
        c.sr.log(f"  {name}: the API refused the read ({exc})")
        return None


# ---- verify -------------------------------------------------------------------------
def _verify(c: Ctx, name: str, p: EC.Proposal | str) -> Any:
    """The second reader on one proposal: a good answer on file, else asked,
    once more when the API refuses it or it does not parse. A network
    failure is raised (exit 75); a refusal twice is ``verify_failed``; an
    answer that never parses is None (``verify_unparsed``)."""
    if not isinstance(p, EC.Proposal):
        return None
    system, content = EC.verify_request(p, c.sr.cfg, c.client, c.images)
    if not p.shown:
        return AP.NO_PICTURE  # a verify with no page to look at is never sent

    def ok(t: str) -> bool:
        return EC.parse_verify(t) is not None

    for attempt in (1, 2):
        text = c.ask.peek(name, f"verify{attempt}", system, content, ok)
        if text is not None and ok(text):
            return EC.parse_verify(text)
    refused = 0
    for attempt in (1, 2):
        try:
            text, _stop = c.ask.ask(name, f"verify{attempt}", system, content, ok, max_tokens=IO.VERIFY_MAX_TOKENS)
        except IO.Pending:
            return None
        except IO.ModelError as exc:
            c.sr.log(f"  {name}: verify {attempt} refused: {exc}")
            refused += 1
            continue
        got = EC.parse_verify(text)
        if got is not None:
            return got
    return AP.FAILED if refused == 2 else None
