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
`SMD_MEDCHRON_HELD_WIPE_HOURS` env away from being something else.

WHY THE QUEUE FILE GOES WITH IT. A hard-stopped seat records a job `held` and
leaves it QUEUED, on purpose, so it runs when the stop clears. Removing the
workdir and leaving the envelope in `queue/` would hand the daemon a job it
can claim and cannot run. Non-terminal wipes take both.

WHAT SURVIVES. The ledger row, with its state and its reason -- the record of
what was asked for and what happened. Only the working copy goes. A resume
request for a wiped job already refuses cleanly and says so (`resume.py`):
the artifacts are gone and the honest answer is the one the ledger gives.
"""

from __future__ import annotations

import logging
import shutil
from typing import Any

logger = logging.getLogger("medchron.daemon")

HELD_WIPE_HOURS_ENV = "SMD_MEDCHRON_HELD_WIPE_HOURS"
DEFAULT_HELD_WIPE_HOURS = 24 * 7

#: Stopped, waiting for a person. Not terminal, and until 2026-09-24 not wiped.
PARKED = frozenset({"held", "refused"})


def _window_hours(d: Any, state: str, terminal: frozenset[str]) -> float | None:
    if state in terminal:
        return float(d.wipe_hours)
    if state in PARKED:
        return float(getattr(d, "held_wipe_hours", DEFAULT_HELD_WIPE_HOURS))
    return None


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
    return wiped
