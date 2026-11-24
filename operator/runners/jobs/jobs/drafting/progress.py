"""Where a running drafting job is, reported to its ledger row (2026-11-24).

THE PROBLEM. A drafting job pulling about 500 files showed nothing on its row:
cents 0 and ``updated_at`` frozen for 26 minutes, because the row moves only
when the job's state does. Neither ``ops-requests`` nor the hourly triage could
tell a long pull from a hung one, and once the job's run dir was wiped its
expected finish and how long each stage took were gone with it.

THE NOTE. The child writes its stage as it starts (``run.py``: ``current`` in
``data/state.json``), each finished stage's seconds (``data/state.json``), the
estimate's ``expected_finish`` when it has one, and a pull's n of N files
(``matter/pull.py``: ``data/pull-progress.json``). The daemon, root and owner of
the job dir, reads those on its heartbeat (every 30 seconds while the child
runs) and records a progress note with ``drafting_job_record`` in ``running``
(``workspace_broker/drafting_progress.py``): when the stage or the progress has
moved, and at least every ``REFRESH_SECONDS`` while the job runs. The broker
writes a ``DRAFTING_JOB_PROGRESS`` audit row only when the stage or the count
MOVED, so the triage reads a frozen count as a stall, never a refresh as work.

The note's stage seconds and expected finish stay on the ledger row after the
run dir is wiped; the job's ending record carries the final timings
(``final_fields``).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from ..broker_client import BrokerError

logger = logging.getLogger("jobs.drafting")

#: The longest a running job's row goes without a note.
REFRESH_SECONDS = 120.0
#: The broker's own spellings (``drafting_progress``); anything else is not sent.
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,59}$")
_FINISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
#: The most stages a note's timings carry.
MAX_STAGES = 80


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def timings(state: dict[str, Any]) -> dict[str, float]:
    """Each finished stage's seconds, from ``data/state.json``."""
    out: dict[str, float] = {}
    for name, rec in state.items():
        if not (isinstance(rec, dict) and rec.get("status") == "done" and _STAGE_RE.match(str(name))):
            continue
        secs = rec.get("seconds")
        if isinstance(secs, (int, float)) and not isinstance(secs, bool) and secs >= 0:
            out[str(name)] = round(float(secs), 1)
        if len(out) >= MAX_STAGES:
            break
    return out


def current(job_dir: Path) -> dict[str, Any]:
    """The note's fields from the job dir: ``stage`` (the one started last),
    ``progress`` ("n/N" while a pull runs), ``expected_finish``, and
    ``stage_seconds``. Empty when nothing has started."""
    data = job_dir / "data"
    state = _read(data / "state.json")
    out: dict[str, Any] = {}
    cur = state.get("current")
    stage = str(cur.get("stage") or "") if isinstance(cur, dict) else ""
    if stage and _STAGE_RE.match(stage):
        out["stage"] = stage  # the last one started, done or not: the next has not begun
    pulled = _read(data / "pull-progress.json")
    done, total = pulled.get("done"), pulled.get("total")
    if out.get("stage") == "pull" and isinstance(done, int) and isinstance(total, int) and 0 <= done <= total:
        out["progress"] = f"{done}/{total}"
    eta = state.get("expected_finish")
    at = eta.get("at") if isinstance(eta, dict) else None
    if isinstance(at, str) and _FINISH_RE.match(at):
        out["expected_finish"] = at
    secs = timings(state)
    if secs:
        out["stage_seconds"] = secs
    return out


def final_fields(job_dir: Path) -> dict[str, Any]:
    """What an ending record keeps on the row: the stage timings and the
    expected finish (never ``stage``/``progress``: the job is not running)."""
    note = current(job_dir)
    return {k: note[k] for k in ("stage_seconds", "expected_finish") if k in note}


def report(lane: Any, job_id: str, sent: dict[str, dict[str, Any]], clock: Callable[[], float]) -> None:
    """Send the note when it moved, or ``REFRESH_SECONDS`` after the last one.
    A refusal (the job ended meanwhile, the broker away) is logged and the next
    heartbeat tries again; a heartbeat never raises."""
    note = current(lane.job_dir(job_id))
    if "stage" not in note:
        return
    last = sent.get(job_id) or {}
    now = clock()
    moved = (last.get("stage"), last.get("progress")) != (note.get("stage"), note.get("progress"))
    if not moved and now - float(last.get("at") or 0) < REFRESH_SECONDS:
        return
    try:
        lane.broker.record(job_id, "running", note)
    except BrokerError as exc:
        logger.info("progress %s for %s not recorded: %s", note.get("stage"), job_id, exc)
        return
    sent[job_id] = {"stage": note.get("stage"), "progress": note.get("progress"), "at": now}


__all__ = ["REFRESH_SECONDS", "current", "final_fields", "report", "timings"]
