"""`update_uncited`: every page an update's writer read and cited nothing from
is accounted for, and a dated record on one is charted. Paid (the judgment
tier for the reader; the writer's own tiers for the extra writing pass).

WHY (2026-11-20). A real update read nine pages of an EMR printout's prior
history (immunization blocks, each with its own date, product, site and
given-by line; a problem list; medications) and wrote nothing from them: the
writer's FILES-SEEN called the whole chunk "no dated encounter", and nothing
downstream noticed a READ page that produced no entry. A hand read found 17
dated records the filed chronology lacked. Of 177 read-but-uncited pages on
a saved 509-page update, 65 were named "no dated encounter", 72 in a
non-record clause, and 40 in no line at all.

1. CODE FIRST (``update_uncited_plan``): the pages each writer chunk held
   less the pages any entry cites; code settles what it can know (a page
   compose already named unread, a billing page, a blank page, a picture
   copy of a delivered or cited page, a page of a chunk ``repair_truncated``
   reads again), each with its reason.
2. A READER classes the rest (``medchron/update/uncited_read``), one request
   per run of adjacent pages of one document, through the entry reader's
   machinery (``entry_audit_io``: answers saved before use, a resume pays for
   none twice, ``ENTRY_AUDIT_REPLAY=1`` answers at $0). Before any call the
   reads are priced and checked by the driver's own limits (the firm's cap
   hold). An unparsed reply is asked once more; still unparsed, or refused by
   the API, the run is unread and the job HOLDS (exit 1).
3. CODE ROUTES: only ``dated_record`` pages go on, written by the existing
   writer as extra chunks (``update_uncited_write``); every other class is
   recorded and acted on by nobody.
4. HOLD: a routed page still cited by no entry after that pass holds the job
   for SMD (exit 2), naming the exhibit and page numbers. No second retry.
5. RECORD ``runs/<unit>/uncited_pages.json``: counts only, and the numbers of
   any page held.

Exits: 0 settled; 1 held (a run not read); 2 held (a dated record still
uncited); 3 a defect (a crash outside a model call, or a writer chunk with no
map and no reason); 75 the model could not be reached (the daemon retries);
9 answers pending, under the replay switch only.
"""

from __future__ import annotations

import json
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import budget, paid
from ..deadline import is_transient
from ..limits import LimitHold
from ..medchron.audit.run import run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.page_mode import Modes
from ..medchron.update import review_pages as RV, uncited_read as UR
from . import entry_audit_io as IO, update_uncited_plan as UP, update_uncited_write as UW
from .base import StageRun
from .update_review_io import _image_source

STAGE = "update_uncited"
EXIT_UNREAD, EXIT_STILL_UNCITED, EXIT_DEFECT, EXIT_INFRA, EXIT_PENDING = 1, 2, 3, 75, 9
EXITS: dict[int, tuple[str, str]] = {
    EXIT_UNREAD: (
        "held",
        "update_held: a page the writer read and cited nothing from could not be classed; SMD reads it",
    ),
    EXIT_STILL_UNCITED: (
        "held",
        "update_held: a page read as a dated record is cited by no entry after it was written again; SMD charts it",
    ),
    EXIT_DEFECT: ("failed", "update_uncited: the stage crashed (a defect)"),
    EXIT_INFRA: (
        "failed",
        "update_uncited incomplete (infrastructure): the model returned no answer; a later rerun reads again",
    ),
    EXIT_PENDING: (
        "failed",
        "update_uncited replay: answers pending (ENTRY_AUDIT_REPLAY is set; a real run never sets it)",
    ),
}
RECORD = "uncited_pages.json"
#: A read's output budget: one small JSON row per page.
MAX_TOKENS = 4_000
READ_OUT_EXPECTED = 400


@dataclass
class _Read:
    run: UR.Run
    status: str = "unread"  # read | unread | pending
    answers: dict[int, UR.Answer] | None = None
    why: str = ""


def run(sr: StageRun) -> int:
    """The stage, with every way out mapped (``entry_audit.run``'s form)."""
    try:
        return _start(sr)
    except LimitHold:
        raise
    except Exception as exc:  # noqa: BLE001 - a crash is the defect exit, never read as a hold
        if is_transient(exc):
            sr.log(f"uncited pages: the model could not be reached ({type(exc).__name__}); a later rerun reads again")
            return EXIT_INFRA
        sr.log(f"uncited pages CRASHED ({type(exc).__name__}: {str(exc)[:300]})")
        sr.log(traceback.format_exc()[-1500:])
        return EXIT_DEFECT


def _start(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    extras = UW.read_extras(d)
    sw = UP.sweep(sr.slug_dir, sr.unit.unit, extras)
    rec: dict[str, Any] = {"uncited": sw.uncited, "settled_by_code": sw.counts(), "open": len(sw.open)}
    sr.log(f"uncited pages: {sw.uncited} read and cited by no entry; {len(sw.settled)} settled by code {sw.counts()}")
    if not sw.open:
        return _finish(sr, d, sw, rec, {})
    ask = IO.Asker(sr, IO.model(sr.cfg), d / UW.DIR, IO.replaying(), stage=STAGE)
    modes = Modes.open(sr.slug_dir, sr.unit.unit)
    try:
        reads = _read_all(sr, ask, sw, modes)
    finally:
        modes.close()
    rec["calls"] = ask.calls
    if any(r.status == "pending" for r in reads):
        sr.log(f"uncited pages REPLAY: {len(ask.pending)} answer(s) pending under runs/{sr.unit.unit}/{UW.DIR}/")
        return EXIT_PENDING
    unread = [r for r in reads if r.status != "read"]
    if unread:
        rec["unread"] = [{"exhibit": r.run.exhibit, "pages": list(r.run.pages), "why": r.why} for r in unread]
        _record(d, rec)
        named = "; ".join(f"Exhibit {r.run.exhibit} pp. {list(r.run.pages)} ({r.why})" for r in unread)
        sr.log(f"uncited pages HELD: {len(unread)} run(s) could not be classed: {named}")
        return EXIT_UNREAD
    answers = {(r.run.exhibit, p): a for r in reads for p, a in (r.answers or {}).items()}
    return _finish(sr, d, sw, rec, answers)


def _read_all(sr: StageRun, ask: IO.Asker, sw: UP.Sweep, modes: Modes) -> list[_Read]:
    images = _image_source(sr)
    plan = [UR.Run(x, pages) for x, pages in UP.runs(sw.open)]
    reqs = [UR.request(r, sw.context.get(r.exhibit, []), sr.cfg, images, modes) for r in plan]
    sr.log(f"uncited pages: {len(sw.open)} page(s) to class in {len(plan)} read(s)")
    _check_cost(sr, ask, [rq for r, rq in zip(plan, reqs) if not _answered(ask, r, *rq)])
    out: list[_Read] = [_Read(r) for r in plan]
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)

    def task(i: int) -> Any:
        return lambda: _read_one(sr, ask, out[i], *reqs[i])

    run_pool([task(i) for i in range(len(plan))], workers)
    return out


def _answered(ask: IO.Asker, run: UR.Run, system: str, content: list[dict[str, Any]]) -> bool:
    ok = UR.parses(run)
    return any(ok(t) for k in ("read1", "read2") if (t := ask.peek(run.name, k, system, content, ok)) is not None)


def _check_cost(sr: StageRun, ask: IO.Asker, reqs: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """The reads still to pay for, priced from their real requests, through
    the driver's own limits: past the job's cap it is the firm's cap hold."""
    if ask.replay or not reqs:
        return
    try:
        pricing = paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return
    tin = 0
    for system, content in reqs:
        reads = RV.page_reads(content)
        tin += len(system) // RV.CHARS_PER_TOKEN + reads["image_pages"] * RV.PAGE_TOKENS
        tin += sum(len(str(b.get("text", ""))) for b in content) // RV.CHARS_PER_TOKEN
    usd = IO.priced(pricing, sr.cfg, tin, len(reqs) * READ_OUT_EXPECTED)
    sr.log(f"uncited pages: the reads are {len(reqs)} call(s), ~{usd:.2f} USD")
    IO.check_limits(sr, usd, stage=STAGE)


def _read_one(sr: StageRun, ask: IO.Asker, r: _Read, system: str, content: list[dict[str, Any]]) -> None:
    """A good answer on file, else asked, and asked once more when it ran out
    or did not parse; still unparsed, or refused by the API, it is unread."""
    ok = UR.parses(r.run)
    name = r.run.name
    for kind in ("read1", "read2"):
        text = ask.peek(name, kind, system, content, ok)
        if text is not None and ok(text):
            r.status, r.answers = "read", UR.parse(text, r.run)
            return
    for kind in ("read1", "read2"):
        try:
            text, stop = ask.ask(name, kind, system, content, ok, max_tokens=MAX_TOKENS)
        except IO.Pending:
            r.status = "pending"
            return
        except IO.ModelError as exc:
            sr.log(f"  {name}: the API refused the read ({exc})")
            r.why = "the API refused the read"
            return
        if stop != "max_tokens" and ok(text):
            r.status, r.answers = "read", UR.parse(text, r.run)
            return
        sr.log(f"  {name}: the reply " + ("ran out" if stop == "max_tokens" else "did not parse"))
    r.why = "the reply did not parse after one retry"


def _finish(sr: StageRun, d: Path, sw: UP.Sweep, rec: dict[str, Any], answers: dict[UP.Page, UR.Answer]) -> int:
    """Route the dated records to the writer, then hold on any still uncited."""
    rec["classed"] = dict(Counter(a.klass for a in answers.values()))
    routed = sorted(pg for pg, a in answers.items() if a.klass == UR.DATED)
    rec["routed"] = len(routed)
    rec["dates_named"] = sum(len(a.dates) for a in answers.values())
    base = UP.base_chunks(d, UW.read_extras(d))
    first = (max(base) if base else 0) + 1
    extra = UW.texts(sr, d, sw.docs, routed) if routed else []
    status, nums = UW.write(sr, d, extra, first, IO.replaying())
    rec["extra_chunks"] = len(nums)
    if status == UW.PENDING:
        _record(d, rec)
        return EXIT_PENDING
    if status != UW.OK:
        _record(d, rec)
        return EXIT_INFRA if status == UW.INFRA else EXIT_DEFECT
    maps = UP.maps_of(d, nums)
    rec["entries_written"] = len(UP.parse_maps(d, maps)[0]) if maps else 0
    now = UP.cited(d, UP.maps_of(d, sorted(UP.whole_chunks(d))), sw.docs, sw.held)
    left = [pg for pg in routed if pg not in now]
    rec["still_uncited"] = len(left)
    if left:
        by_ex: dict[int, list[int]] = {}
        for x, p in left:
            by_ex.setdefault(x, []).append(p)
        rec["held"] = [{"exhibit": x, "pages": ps} for x, ps in sorted(by_ex.items())]
        _record(d, rec)
        named = "; ".join(f"Exhibit {x} pp. {ps}" for x, ps in sorted(by_ex.items()))
        sr.log(f"uncited pages HELD: {len(left)} page(s) read as a dated record are cited by no entry: {named}")
        return EXIT_STILL_UNCITED
    _record(d, rec)
    sr.log(
        f"uncited pages: {rec['classed']} classed; {len(routed)} routed to the writer in {len(nums)} chunk(s), "
        f"{rec['entries_written']} entr(ies) written"
    )
    return 0


def _record(d: Path, rec: dict[str, Any]) -> None:
    (d / RECORD).write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
