"""`update_uncited`: every page an update's writer read and cited nothing from
is accounted for, and a dated record of the measured kinds on one is charted.
Paid (the judgment tier for the reader; the writer's tiers for the extra
writing pass). It never holds the job.

WHY (2026-11-20). A real update read nine pages of an EMR printout's prior
history (immunization blocks, each with its own date, product, site and
given-by line; a problem list; medications) and wrote nothing from them: the
writer's FILES-SEEN called the whole chunk "no dated encounter", and nothing
downstream noticed a READ page that produced no entry. A hand read found 17
dated records the filed chronology lacked (12 vaccine records, 3 TB skin-test
records, a prescription order). Of 177 read-but-uncited pages on a saved
509-page update, 65 were named "no dated encounter", 72 in a non-record
clause, and 40 in no line at all.

1. CODE FIRST (``update_uncited_plan``): the pages each writer chunk held
   less the pages any entry cites; code settles what it can know (a page
   compose already named unread, a billing page by its words or a
   BILLING-DATES line, a blank page, a picture copy of a delivered or cited
   page, a page of a chunk ``repair_truncated`` reads again), then, before
   any request, a trusted text page with no dated line and an exact copy of
   another uncited page (``prefilter``). Each reason is counted.
2. A READER classes the rest (``medchron/update/uncited_read``), one request
   per run of adjacent pages of one document, through the entry reader's
   machinery (``entry_audit_io``: answers saved before use, a resume pays for
   none twice, ``ENTRY_AUDIT_REPLAY=1`` answers at $0). Before any call the
   reads still to pay for are priced (a picture at 2,650 tokens, the output at
   the entry reader's measured figure, thinking included) and checked by the
   driver's own limits: past the cap it is the firm's cap hold. An unparsed
   reply is asked once more; still unparsed, or refused by the API, the run is
   recorded as unclassed and the stage goes on.
3. CODE ROUTES: only ``dated_record`` pages go on, to the extra writing pass
   (``update_uncited_write``, its own narrow prompt, shown the reader's dates
   and kinds and the entries already charted); ``unsure`` and every other
   class is recorded and acted on by nobody.
4. RECORD ``runs/<unit>/uncited_pages.json``: counts, and exhibit and page
   numbers only (never record text). A dated-record page the extra pass still
   leaves unaccounted for is recorded there, never held; the hourly triage
   reads the counts from the run record (``medchron/core_counts``).

LIMIT: a page that an entry cites is never examined here, though it may hold
dated rows the entry does not chart (a real update's entry cited a page of 20
immunization rows and charted 2 of them).

Exits: 0 done (whatever was classed, written or left recorded); 3 a defect (a
crash outside a model call, or an extra chunk with no map and no reason); 75
the model could not be reached (the daemon retries); 9 answers pending, under
the replay switch only.
"""

from __future__ import annotations

import importlib
import json
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..deadline import is_transient
from ..limits import LimitHold
from ..medchron.audit.run import run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.page_mode import Modes
from ..medchron.update import uncited_read as UR
from . import entry_audit_io as IO, update_uncited_cost as COST, update_uncited_plan as UP, update_uncited_write as UW
from .base import StageRun

STAGE = COST.STAGE
EXIT_DEFECT, EXIT_INFRA, EXIT_PENDING = 3, 75, 9
EXITS: dict[int, tuple[str, str]] = {
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
RECORD = COST.RECORD
#: A read's output budget: thinking at high effort plus one small JSON row per page.
MAX_TOKENS = 8_000


@dataclass
class _Read:
    run: UR.Run
    status: str = "unread"  # read | unread | pending
    answers: dict[int, UR.Answer] | None = None
    why: str = ""


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: Any) -> float | None:
    """The stage's projection for the driver's cap check (``driver._projection``)."""
    return COST.projected_usd(slug_dir, unit, cfg, pricing)


def image_source(sr: StageRun) -> Any:
    """The entry reader's page pictures, from whichever module holds them on
    this tree (a peer branch moves it to ``stages/read_inputs``)."""
    try:
        mod: Any = importlib.import_module("jobs.stages.read_inputs")
        return mod.image_source(sr)
    except (ImportError, AttributeError):
        from .update_review_io import _image_source

        return _image_source(sr)


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
    sw = UP.sweep(sr.slug_dir, sr.unit.unit, UW.read_extras(d))
    modes = UP.modes_for(sr.slug_dir, sr.unit.unit, sw.docs)
    try:
        UP.prefilter(sw, modes)
        rec: dict[str, Any] = {"uncited": sw.uncited, "settled_by_code": sw.counts(), "open": len(sw.open)}
        sr.log(f"uncited pages: {sw.uncited} read and cited by no entry; settled by code {sw.counts()}")
        if not sw.open:
            return _finish(sr, d, sw, rec, {})
        ask = IO.Asker(sr, IO.model(sr.cfg), d / UW.DIR, IO.replaying(), stage=STAGE)
        reads = _read_all(sr, ask, sw, modes)
    finally:
        modes.close()
    rec["calls"] = ask.calls
    if any(r.status == "pending" for r in reads):
        sr.log(f"uncited pages REPLAY: {len(ask.pending)} answer(s) pending under runs/{sr.unit.unit}/{UW.DIR}/")
        return EXIT_PENDING
    unread = [r for r in reads if r.status != "read"]
    if unread:
        rec["unclassed"] = sum(len(r.run.pages) for r in unread)
        rec["unclassed_pages"] = _by_exhibit([(r.run.exhibit, p) for r in unread for p in r.run.pages])
        sr.log(f"uncited pages: {rec['unclassed']} page(s) could not be classed; recorded, the job goes on")
    answers = {(r.run.exhibit, p): a for r in reads for p, a in (r.answers or {}).items()}
    return _finish(sr, d, sw, rec, answers)


def _read_all(sr: StageRun, ask: IO.Asker, sw: UP.Sweep, modes: Modes) -> list[_Read]:
    images = image_source(sr)
    plan = [UR.Run(x, pages) for x, pages in UP.runs(sw.open)]
    reqs = [UR.request(r, sw.context.get(r.exhibit, []), sr.cfg, images, modes) for r in plan]
    sr.log(f"uncited pages: {len(sw.open)} page(s) to class in {len(plan)} read(s)")
    if not ask.replay:
        todo = [rq for r, rq in zip(plan, reqs) if not _answered(ask, r, *rq)]
        usd = COST.reads_usd(sr.cfg, todo)
        if usd is not None and todo:
            sr.log(f"uncited pages: the reads still to pay for are {len(todo)} call(s), ~{usd:.2f} USD")
            IO.check_limits(sr, usd, stage=STAGE)
    out: list[_Read] = [_Read(r) for r in plan]
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)

    def task(i: int) -> Any:
        return lambda: _read_one(sr, ask, out[i], *reqs[i])

    run_pool([task(i) for i in range(len(plan))], workers)
    return out


def _answered(ask: IO.Asker, run: UR.Run, system: str, content: list[dict[str, Any]]) -> bool:
    ok = UR.parses(run)
    return any(ok(t) for k in ("read1", "read2") if (t := ask.peek(run.name, k, system, content, ok)) is not None)


def _read_one(sr: StageRun, ask: IO.Asker, r: _Read, system: str, content: list[dict[str, Any]]) -> None:
    """A good answer on file, else asked, and asked once more when it ran out
    or did not parse; still unparsed, or refused by the API, it is unclassed."""
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
            r.why = "refused"
            return
        if stop != "max_tokens" and ok(text):
            r.status, r.answers = "read", UR.parse(text, r.run)
            return
        sr.log(f"  {name}: the reply " + ("ran out" if stop == "max_tokens" else "did not parse"))
    r.why = "unparsed"


def _by_exhibit(pages: list[UP.Page]) -> list[dict[str, Any]]:
    by_ex: dict[int, list[int]] = {}
    for x, p in sorted(pages):
        by_ex.setdefault(x, []).append(p)
    return [{"exhibit": x, "pages": ps} for x, ps in sorted(by_ex.items())]


def _finish(sr: StageRun, d: Path, sw: UP.Sweep, rec: dict[str, Any], answers: dict[UP.Page, UR.Answer]) -> int:
    """Send the dated records to the extra writing pass and record what it left."""
    rec["classed"] = dict(Counter(a.klass for a in answers.values()))
    found = {pg: a for pg, a in answers.items() if a.klass == UR.DATED}
    rec["routed"] = len(found)
    rec["routed_kinds"] = dict(Counter(k for a in found.values() for k in a.kinds))
    base = UP.base_chunks(d, UW.read_extras(d))
    extra = UW.texts(sr, d, sw, found) if found else []
    status, nums = UW.write(sr, d, extra, (max(base) if base else 0) + 1, IO.replaying())
    rec["extra_chunks"] = len(nums)
    if status != UW.OK:
        _record(d, rec)
        return {UW.PENDING: EXIT_PENDING, UW.INFRA: EXIT_INFRA}.get(status, EXIT_DEFECT)
    maps = UP.maps_of(d, nums)
    rec["entries_written"] = len(UP.parse_maps(d, maps)[0]) if maps else 0
    now = UP.accounted(d, UP.maps_of(d, sorted(UP.whole_chunks(d))), sw.docs, sw.held)
    left = sorted(pg for pg in found if pg not in now)
    rec["still_uncited"] = len(left)
    if left:
        rec["still_uncited_pages"] = _by_exhibit(left)
        sr.log(f"uncited pages: {len(left)} dated-record page(s) the extra pass wrote nothing for; recorded")
    _record(d, rec)
    sr.log(
        f"uncited pages: {rec['classed']} classed; {len(found)} sent to the extra pass in {len(nums)} chunk(s), "
        f"{rec['entries_written']} entr(ies) written, {len(left)} still uncited"
    )
    return 0


def _record(d: Path, rec: dict[str, Any]) -> None:
    (d / RECORD).write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
