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
2. READ each unit. A unit whose cited page cannot be shown (past its
   exhibit's end, or no picture and no text) is UNREAD and never sent. An
   unparsed reply is asked once more, then a unit citing more than one page
   is read page by page (so is a request the API refuses as too large);
   still unparsed, or refused by the API for another reason, the unit is
   unread and the stage HOLDS. A resume whose page-by-page answers are all
   on file uses them and never pays for the whole unit again.
3. Before any confirm, the confirm phase is priced from the real findings
   and the pages each confirm shows and checked by the driver's own limits:
   past the job's cap it is the firm's ordinary cap hold (``LimitHold``).
4. CONFIRM each finding blind; a confirm the API refuses twice is recorded
   ``confirm_failed`` (a network failure
   exits 75 and the daemon retries). A finding code records whatever the
   confirm says is not confirmed.
5. DECIDE in code (``entry_audit_apply``); a correction on a sentence SMD
   wrote by hand (``drops.json`` citation_edits) is recorded, never applied;
   an added sentence another part of the same entry already adds is
   recorded ``duplicate_add``.
6. TRIPWIRE: more applied edits than ``entry_read.EDIT_SHARE`` of the
   document's sentences holds the job with the document untouched, unless
   SMD wrote a release (``entry_audit_apply``'s docstring).
7. APPLY once through ``review_repair.apply``; an edit it rejects is
   recorded. The document is written only when it has no fewer sentences.
8. RECORD ``runs/<unit>/entry_read.json``: counts and per-unit rows, never
   the document's text. Recorded findings live only there, not in the notes.

Nothing is ever removed from the document here. Answers are saved before use
and a resume pays for none twice; ``ENTRY_AUDIT_REPLAY=1`` answers at $0
(``entry_audit_io``). Exits: 0 read and settled; 1 held (an entry not read, or
the tripwire); 3 the stage crashed outside a model call (a defect, never
read as a hold); 75 the model could not be reached (the daemon
retries); 9 answers pending, under the replay switch only.
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
from . import audit_loop, entry_audit_apply as AP, entry_audit_io as IO
from .base import StageRun
from .update_review import _client
from .update_review_io import _image_source

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


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half for `jobs rehearse`: what the read and its confirms would cost."""
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
    pl = IO.plan(sr.slug_dir, sr.unit.unit, sr.cfg, md)
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    modes = Modes.open(sr.slug_dir, sr.unit.unit)
    ask = IO.Asker(sr, IO.model(sr.cfg), rd / IO.DIR, IO.replaying())
    c = Ctx(sr, ask, _image_source(sr), modes, _client(sr), workers, pl.npages)
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
    unread = [r.unit.key for r in reads if r.status != "read"]
    if unread:
        AP.record(sr, c.ask, doc, pl, reads, [], held=f"{len(unread)} unit(s) could not be read")
        sr.log(f"entry read HELD: {len(unread)} entr(ies) could not be read against their pages: {', '.join(unread)}")
        return EXIT_HELD
    items = [(f"{name}.f{i}", u, f) for r in reads for name, u, p in r.got for i, f in enumerate(p.findings)]
    _check_cost(c, items)
    confirms = _pool(c, items, lambda it: _confirm(c, *it))
    if c.ask.pending:
        return _pending(c, "confirm")
    decisions = AP.decide(sr, c.modes, md, pl, items, confirms)
    held, released = AP.apply(sr, c.modes, doc, md, pl, decisions)
    AP.record(sr, c.ask, doc, pl, reads, decisions, items, held=held, released=released)
    if held:
        sr.log(f"entry read HELD: {held}; the document is unchanged")
        return EXIT_HELD
    n = sum(1 for d in decisions if d[0] == "apply" and d[1].outcome == "applied")
    sr.log(f"entry read: {len(items)} finding(s), {n} applied, {len(items) - n} recorded; {c.ask.calls} call(s)")
    return 0


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


def _check_cost(c: Ctx, items: list[AP.Item]) -> None:
    """The confirm phase, priced from the real findings and the pages each
    confirm shows, through the driver's own limits: past the job's cap it
    raises the same ``LimitHold`` (the firm's cap hold) as any paid stage.
    Not under the $0 replay."""
    todo = [(u, f) for _n, u, f in items if AP.needs_confirm(f, u)]
    if c.ask.replay or not todo:
        return
    usd = IO.confirm_usd(c.sr, c.modes, todo)
    if usd is not None:
        c.sr.log(f"entry read: the confirm phase is {len(todo)} confirm(s), ~{usd:.2f} USD")
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
    return [(f"{unit.key}~s{i}", replace(unit, pages=[pg], cited=[pg])) for i, pg in enumerate(cited, 1)]


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
            r.status, r.got = "read", [(n, u, p) for (n, u), p in zip(subs, saved)]
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
        r.status, r.got = "read", [(n, u, p) for (n, u), p in zip(subs, parts)]
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


# ---- confirm ------------------------------------------------------------------------
def _confirm(c: Ctx, name: str, unit: ER.Unit, f: ER.ReadFinding) -> Any:
    """The blind confirm: a good answer on file, else asked, once more when
    the API refuses it or it does not parse. A network failure is raised
    (exit 75); a refusal twice is recorded ``confirm_failed``."""
    if not AP.needs_confirm(f, unit):
        return AP.NO_CONFIRM
    system, content = EC.confirm_request(f, unit, c.sr.cfg, c.client, c.images, c.modes)
    ok: IO.Valid = lambda t: EC.parse_confirm(t).answer != EC.UNPARSED  # noqa: E731
    for attempt in (1, 2):
        text = c.ask.peek(name, f"confirm{attempt}", system, content, ok)
        if text is not None and ok(text):
            return EC.parse_confirm(text)
    last: Any = AP.FAILED
    for attempt in (1, 2):
        try:
            text, _stop = c.ask.ask(name, f"confirm{attempt}", system, content, ok, max_tokens=IO.CONFIRM_MAX_TOKENS)
        except IO.Pending:
            return None
        except IO.ModelError as exc:
            c.sr.log(f"  {name}: confirm {attempt} refused: {exc}")
            continue
        last = EC.parse_confirm(text)
        if last.answer != EC.UNPARSED:
            return last
    return last
