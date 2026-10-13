"""A PROOF RUN: the lane's real pipeline on a real matter, stopped before the
one stage that writes to the firm's system.

WHY (2026-10-13). About 45 of the 52 defects A&P met between 09-01 and 10-13
happened in a lane's first one to three LIVE uses: the firm's first real
request of each new kind was the test. Wherever a dry run ran first, nothing
reached the firm. So a kind of job that has not passed a full run on this
seat's own data waits (``lane_proof.py``) until SMD runs it this way
(``proof_run.py``).

The daemon sets ``MEDCHRON_PROOF_RUN=1`` in the child's environment and
nothing else does: the broker's envelope cannot, so a firm request can never
turn itself into a proof run, nor a proof run into a delivery. Each runner
reads :func:`active` at its write stage and returns a ``dry_run`` verdict
naming what it would have filed. The Smokeball connector refuses every
non-read request while it is set, so a write anywhere else is the runner's
defect, and the proof run FAILS on it instead of reaching the client's file.
"""

from __future__ import annotations

import os

PROOF_ENV = "MEDCHRON_PROOF_RUN"
#: The outcome a proof run ends with when it reached the write stage clean.
OUTCOME = "dry_run"


def active() -> bool:
    return os.environ.get(PROOF_ENV, "").strip() == "1"


__all__ = ["OUTCOME", "PROOF_ENV", "active"]
