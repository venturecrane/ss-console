"""One matter through the paid part of a case file job, start to finish.

    yield -> fetch -> extract -> pack -> read -> validate -> audit

Every step writes the matter's own files (``data/m/<id>/``), so a resume
re-pays nothing a matter already finished (``review.json`` present: skipped)
and a matter's failure is that matter's alone (``held.json``). A limit (the
job's cap, the month's budget) or a Machine stop is not one matter's failure:
it stops the run.

THE YIELD. Before each matter starts, the run waits while a client-requested
demand, drafting or chronology child is live (``yield_pids``): the case file
job is long and scheduled work, and those are a client waiting. It never stops
mid-matter, so such a job waits at most for the matters already in flight.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from ..limits import LimitHold
from ..litigation import extract as extract_mod, fetch as fetch_mod
from ..litigation.tools import ReadIncomplete, dump
from ..stop_request import StopRequested
from . import audit, pack, read, validate
from .inventory import inv_of

YIELD_ENV = "MEDCHRON_CASEFILE_YIELD_PIDS"
YIELD_POLL_SECONDS = 15.0
REVIEW = "review.json"
HELD = "held.json"


def live(pidfile: Path) -> bool:
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def yield_pids() -> list[Path]:
    return [Path(p) for p in os.environ.get(YIELD_ENV, "").split(",") if p.strip()]


def wait_turn(
    pids: list[Path],
    sleep: Callable[[float], Any] = time.sleep,
    check: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> float:
    """Block while any client-requested child is live; seconds waited."""
    waited = 0.0
    told = False
    while any(live(p) for p in pids):
        if not told:
            log("[matters] a client-requested job is running; this job waits before its next matter")
            told = True
        if check is not None:
            check()
        sleep(YIELD_POLL_SECONDS)
        waited += YIELD_POLL_SECONDS
    return waited


def held(run: Any, mid: str, why: str, detail: str) -> None:
    dump(run._mfile(mid, HELD), {"why": why, "detail": detail[:300]})
    run.log(f"[matters] a matter is held alone ({why}); the others go on")


def _fetch(run: Any, mid: str, inv: dict[str, Any], changed: set[str] | None) -> None:
    files = inv["files"]
    ids = pack.fetch_ids(inv, run.firm, changed)
    fetch_mod.fill_from_cache(run.cache, run.data, mid, files, ids)
    r = fetch_mod.fetch_matter(run.seat, mid, files, ids, run.data, run.log)
    fetch_mod.index_downloads(run.cache, run.data, mid, files)
    dump(run._mfile(mid, "fetch.json"), r)
    if r["retry"]:
        raise _MatterHeld("fetch", f"{len(r['retry'])} files are not fetched for a reason a retry can fix")


def _extract(run: Any, mid: str) -> None:
    lost = fetch_mod.heal_cached(lambda: run.seat, run.cache, run.data, mid, inv_of(run.data, mid)["files"], run.log)
    if lost:
        raise _MatterHeld("fetch", f"{len(lost)} cached files lost their text and could not be fetched")
    rows = list(fetch_mod.pulled(run.data, mid).values())
    extract_mod.extract_matter(
        fetch_mod.matter_dir(run.data, mid), rows, run.no_ocr, None, concurrency=2, cache=run.cache
    )


class _MatterHeld(RuntimeError):
    def __init__(self, why: str, detail: str) -> None:
        super().__init__(detail)
        self.why = why


def context(run: Any, mid: str, inv: dict[str, Any]) -> read.CasefileContext:
    return read.CasefileContext(
        mid,
        inv["files"],
        run.data,
        int(run.firm.get("chunk_chars")),
        seat=run.seat,
        ocr=run.no_ocr,
        log=run.log,
        cache=run.cache,
    )


def _review(run: Any, m: dict[str, Any], plan: dict[str, Any]) -> None:
    mid = m["id"]
    inv = inv_of(run.data, mid)
    changed = set(plan["changed"]) if plan.get("changed") is not None else None
    _fetch(run, mid, inv, changed)
    _extract(run, mid)
    ctx = context(run, mid, inv)
    prior = run.prior(mid) if plan["mode"] == "delta" else None
    built = pack.build(ctx, inv, run.firm, prior, changed)

    def text_of(fid: str) -> str | None:
        return ctx.cached_text(fid)

    def check(rec: dict[str, Any]) -> list[str]:
        refresh(run, mid, built["meta"])  # a document the reader opened since the pack was built is read now
        return validate.check(rec, str(m["number"]), inv, built["meta"], run.firm, text_of)

    result = read.read_matter(run.doorway, run.firm, ctx, built["text"], check)
    if result.problems:
        raise _MatterHeld("validate", "; ".join(result.problems)[:300])
    todo = audit.items(result.record, inv, built["meta"], text_of, int(run.firm.get("audit_doc_chars")))
    verdicts = audit.audit_matter(run.doorway, run.firm, mid, todo) if todo else {}
    scans = [
        r["name"]
        for r in pack.rows_of(inv)
        if built["meta"][r["id"]]["state"].startswith("SCAN") and pack.priority(r, run.firm) == 1
    ]
    stats = {k: built[k] for k in ("readable", "packed", "scale")} | {
        "chars": len(built["text"]),
        "turns": result.turns,
    }
    dump(run._mfile(mid, "meta.json"), built["meta"])
    dump(
        run._mfile(mid, REVIEW),
        {"review": result.record, "verdicts": verdicts, "pack": stats, "scanned_bills": scans, "mode": plan["mode"]},
    )


def refresh(run: Any, mid: str, meta: dict[str, Any]) -> None:
    """Re-derive the state of every document not read when the pack was built:
    ``fetch_doc`` fetches and extracts on demand, and a proof the reader opened
    is a proof read in this run."""
    stale = [fid for fid, m in meta.items() if not pack.is_readable(str(m.get("state") or ""))]
    if not stale:
        return
    pulled = fetch_mod.pulled(run.data, mid)
    extracted = pack._extracted(run.data, mid)
    for fid in stale:
        text = fetch_mod.cached_text(run.data, mid, fid)
        meta[fid]["state"] = pack.doc_state(fid, pulled, extracted, {fid: text} if text is not None else {})


def process(run: Any, m: dict[str, Any], plan: dict[str, Any]) -> None:
    """One matter; its own failure becomes ``held.json``, a stop propagates."""
    mid = m["id"]
    if run._mfile(mid, REVIEW).is_file() or run._mfile(mid, HELD).is_file():
        return
    wait_turn(yield_pids(), check=run.stop_check, log=run.log)
    try:
        _review(run, m, plan)
    except (LimitHold, StopRequested):
        raise
    except _MatterHeld as exc:
        held(run, mid, exc.why, str(exc))
    except ReadIncomplete as exc:
        held(run, mid, "read", str(exc))
    except Exception as exc:  # noqa: BLE001 - one matter's failure is that matter's; the run goes on
        held(run, mid, "read", f"{type(exc).__name__}: {str(exc)[:200]}")


def load_review(run: Any, mid: str) -> dict[str, Any] | None:
    p = run._mfile(mid, REVIEW)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


__all__ = ["HELD", "REVIEW", "YIELD_ENV", "context", "held", "live", "load_review", "process", "wait_turn"]
