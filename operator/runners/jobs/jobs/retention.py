"""How long a job's working copy stays on the seat.

A job's workdir holds the matter's source documents and the text extracted
from them -- a firm's client medical records, on our disk. The daemon has
always said "workdirs are wiped 72 h after a terminal state, never before",
and that is what it did: `wipe_expired` tested `state in TERMINAL`, and
TERMINAL is `{delivered, failed}`.

A job that STOPS AND WAITS is in neither. `held` and `refused` are not
terminal -- that is the point of them, a person is meant to clear them -- so
nothing ever deleted their workdirs. `finished_at` was already stamped for a
held job, so the expiry data was sitting right there; only the state test
excluded it.

Found live 2026-09-24 on the law firm's seat: four job dirs, 15 to 24 days
old, 1.77 GB, four separate full copies of the same matter because each
re-submit downloaded it again. They were deleted by hand. This module is so
that the next one deletes itself.

WHY A SEPARATE, LONGER WINDOW. A terminal job is finished: 72 hours is a
grace period for reading the evidence. A held job is UNFINISHED WORK, and the
question is a different one -- how long before we call it abandoned. Wiping a
hold at 72 h would destroy a Friday-evening hold before Monday. One working
week is the answer here: long enough that a person who means to act can, short
enough that a client's records are never sitting a month later. It is a
`SMD_JOBS_HELD_WIPE_HOURS` env away from being something else.

WHY THE QUEUE FILE GOES WITH IT. A hard-stopped seat records a job `held` and
leaves it QUEUED, on purpose, so it runs when the stop clears. Removing the
workdir and leaving the envelope in `queue/` would hand the daemon a job it
can claim and cannot run. Non-terminal wipes take both.

WHAT SURVIVES. The ledger row -- the record of what was asked for and what
happened. Only the working copy goes. A chronology hold's row moves to
``failed``, its reason saying it expired unattended (``close_wiped_hold``),
since a hold with no workdir can never resume and must not read as waiting;
SMD is paged once and the firm is sent nothing. A resume
request for a wiped job already refuses cleanly and says so (`resume.py`):
the artifacts are gone and the honest answer is the one the ledger gives.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from typing import Any

logger = logging.getLogger("jobs.daemon")

HELD_WIPE_HOURS_ENV = "SMD_JOBS_HELD_WIPE_HOURS"
DEFAULT_HELD_WIPE_HOURS = 24 * 7

#: Stopped, waiting for a person. Not terminal, and until 2026-09-24 not wiped.
PARKED = frozenset({"held", "refused"})


def _window_hours(d: Any, state: str, terminal: frozenset[str]) -> float | None:
    if state in terminal:
        return float(d.wipe_hours)
    if state in PARKED:
        return float(getattr(d, "held_wipe_hours", DEFAULT_HELD_WIPE_HOURS))
    return None


#: The folders a lane sets refused envelopes aside in, inside its queue: an
#: envelope with no ledger row (``quarantine``) and one whose job had ended
#: (``smd_stop.ENDED_DIR``). Both hold a firm's request text.
SET_ASIDE_DIRS = ("quarantine", "ended")
#: The lane-hold markers set aside with them, under ``lane-holds/``.
HOLDS_SET_ASIDE = "lane-holds/ended"


def mark_set_aside(path: Any) -> None:
    """Stamp a just-set-aside file so its window starts now, not when the
    broker first wrote it (a rename keeps the old mtime)."""
    try:
        os.utime(path)
    except OSError as exc:
        logger.warning("could not stamp %s: %s", path, exc)


def prune_set_aside(d: Any) -> list[str]:
    """Delete set-aside envelopes and holds older than the terminal window
    (``d.wipe_hours``, the 72 h a finished job's workdir is kept). They carry a
    firm's request text and are never read again by any lane: gone means gone.
    Never raises; returns the names removed."""
    removed: list[str] = []
    cutoff = d.clock() - float(d.wipe_hours) * 3600
    folders = [(d.queue / name, "") for name in SET_ASIDE_DIRS]
    folders.append((d.run_dir / HOLDS_SET_ASIDE, f"{d.LANE}-"))
    for folder, prefix in folders:
        try:
            entries = list(folder.glob(f"{prefix}*.json")) if folder.is_dir() else []
            for p in entries:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed.append(p.name)
        except OSError as exc:
            logger.warning("could not prune %s: %s", folder, exc)
    if removed:
        logger.info("%s lane: pruned %d set-aside file(s) past %.0f h", d.LANE, len(removed), float(d.wipe_hours))
    return removed


def wipe_expired(d: Any, terminal: frozenset[str]) -> list[str]:
    """Delete every job workdir past its window. Returns the ids wiped.

    `terminal` is passed in rather than imported so there is exactly one
    definition of it on the daemon side; a second copy here is how the two
    would drift into disagreeing about what "finished" means.
    """
    wiped: list[str] = []
    if not d.jobs.is_dir():
        return wiped
    now = d.clock()
    for path in list(d.jobs.iterdir()):
        st = d._daemon_state(path.name)
        state, done = str(st.get("state") or ""), st.get("finished_at")
        hours = _window_hours(d, state, terminal)
        if not done or hours is None or now - float(done) < hours * 3600:
            continue
        if state not in terminal:
            # Worth a line: a job nobody cleared for a week is a fact about us,
            # not about the job, and the workdir going is the only trace left.
            logger.warning(
                "wiping %s: %s for %.1f days with nobody clearing it", path.name, state, (now - float(done)) / 86400
            )
            (d.queue / f"{path.name}.json").unlink(missing_ok=True)
        shutil.rmtree(path, ignore_errors=True)
        wiped.append(path.name)
        if state in PARKED:
            close_wiped_hold(d, path.name)
    close_dead_holds(d)
    return wiped


#: The lanes whose ledger row a wiped hold closes. The chronology's ledger
#: allows ``held -> failed`` and sends the firm nothing for a failed job (it
#: hears only of a delivered one); other lanes' ledgers and notes differ.
CLOSES_WIPED_HOLDS = frozenset({"medchron"})
#: The reason a closed dead hold carries, followed by the hold's own reason.
WIPED_HOLD_REASON = "expired: held past the retention window with nobody clearing it; workdir wiped"
#: How often the lane looks for holds wiped before their rows could close.
DEAD_HOLD_SWEEP_SECONDS = 3600


def close_wiped_hold(d: Any, job_id: str, row: dict[str, Any] | None = None) -> bool:
    """Close the ledger row of a hold whose workdir is gone, and page SMD once.

    A wiped hold can never resume (``resume.py`` refuses it), yet its row
    stayed ``held`` for good: a chronology hold on a client seat sat held for
    three weeks until SMD closed it by hand. Only a row the broker still has
    ``held`` moves. The record goes straight to the broker, so no DELIVER turn
    wakes and the firm hears nothing. The page uses the code the hold paged
    under (``proof/holds.classify``), keyed to this close. Returns whether the
    row closed; never raises, the wipe has happened either way."""
    if d.LANE not in CLOSES_WIPED_HOLDS:
        return False
    from .proof import holds, watch

    if watch.in_proof_run(d):
        return False
    try:
        row = row if row is not None else d.broker.status(job_id)
        if not row or row.get("state") != "held":
            return False
        was = str(row.get("reason") or "no reason recorded")
        d.broker.record(job_id, "failed", {"reason": f"{WIPED_HOLD_REASON} (held: {was})"[:500]})
    except Exception as exc:  # noqa: BLE001 - a broker error must not stop the wipe loop
        logger.warning("could not close the wiped hold %s: %s", job_id, exc)
        return False
    code = holds.classify(d.LANE, was)[1] or "gate_held"
    watch.alert(d, job_id, code, f"{WIPED_HOLD_REASON}; row closed failed; nothing was sent", unit=f"wiped:{job_id}")
    return True


def _iso_epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def close_dead_holds(d: Any) -> list[str]:
    """Close every ``held`` row whose workdir and queue entry are already gone
    and whose hold is older than the parked window: holds wiped before
    ``close_wiped_hold`` existed, or whose close the broker missed. Runs at
    most once an hour per lane. Returns the ids closed; never raises."""
    if d.LANE not in CLOSES_WIPED_HOLDS:
        return []
    now = d.clock()
    if now - float(getattr(d, "_dead_holds_swept_at", 0.0)) < DEAD_HOLD_SWEEP_SECONDS:
        return []
    d._dead_holds_swept_at = now
    from .proof import watch

    try:
        rows = watch._client(d)._request({"action": "medchron_job_list"}).get("jobs") or []
    except Exception as exc:  # noqa: BLE001 - a broker error must not stop the tick
        logger.warning("could not list chronology jobs for dead holds: %s", exc)
        return []
    cutoff = now - float(getattr(d, "held_wipe_hours", DEFAULT_HELD_WIPE_HOURS)) * 3600
    closed: list[str] = []
    for row in rows:
        job_id = str(row.get("id") or "")
        held_at = _iso_epoch(row.get("updated_at"))
        if not job_id or row.get("state") != "held" or held_at is None or held_at >= cutoff:
            continue
        if (d.jobs / job_id).exists() or (d.queue / f"{job_id}.json").exists():
            continue
        if close_wiped_hold(d, job_id, row):
            closed.append(job_id)
    return closed
