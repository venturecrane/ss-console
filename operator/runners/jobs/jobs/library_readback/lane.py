"""The read-back, run by the root daemon after a delivered drafting or demand job.

THE HOOK. Each of the two lanes calls :func:`after` once its ``_report`` has
recorded the job, beside piece 4's ``library_edit.handoff.hand_over``: the
least invasive place, because the child has finished, the filed bytes and the
record are on disk, and nothing here can change what the job delivered. It never
raises (a lane must not lose a job to its own measurement), and a lane proof
run's twin is never scored, because its broker proxies to the real ledger.

THE SWITCH, ``logic_in_code.library_readback`` (ADR 0104), read fresh on each job:

* ``observe`` (absent: every seat today): the score is written beside the record
  (``library-score.json``) and appended to the seat's read-back log; SMD is
  paged nothing;
* ``enforce``: the same, and a failed test pages SMD once for the job
  (``library_readback_failed``, the failed tests' names only).

Nothing is ever sent to the firm, in either mode.

THE LOG, ``<run dir>/library-readback.jsonl``: one line per scored job (job id,
lane, kind, mode, each test's verdict). Ids and verdicts only, never a name or a
word of a document. It is what ``jobs library-readback status`` reads for a job
whose folder the daemon has wiped (72 hours after it ends), so the five tests'
state per seat survives the job folders it was measured in.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from smokeball_connector import logic_in_code

from . import CODE, TESTS
from .score import score_job, write_score

logger = logging.getLogger("jobs.daemon")

#: The seat switch.
SWITCH = logic_in_code.LIBRARY_READBACK
#: The seat's read-back log, under the daemon's run dir.
LOG = "library-readback.jsonl"
LANES = ("drafting", "demand")


def _spec_dir() -> Path:
    from ..drafting.firm_rules import _spec_dir as spec

    return spec()


def log_line(lane: str, score: dict[str, Any]) -> dict[str, Any]:
    """The log's line for one score: ids and verdicts only."""
    return {
        "job_id": score["job_id"],
        "lane": lane,
        "kind": score.get("kind"),
        "mode": score.get("mode"),
        "scored_at": score["scored_at"],
        "verdicts": {t: score["tests"][t]["verdict"] for t in TESTS},
    }


def _append(run_dir: Path, line: dict[str, Any]) -> None:
    path = Path(run_dir) / LOG
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")
    os.chmod(path, 0o644)


def score_delivered(d: Any, job_id: str) -> str:
    """Score one delivered job, write it, log it, and page when enforcing."""
    from ..proof import watch

    switch = logic_in_code.load_switch(SWITCH)
    inputs = getattr(d, "inputs_dir", None)
    lane = str(getattr(d, "LANE", ""))
    score = score_job(
        d.job_dir(job_id),
        spec_dir=_spec_dir(),
        drafting_inputs=inputs if lane == "drafting" else None,
        demand_inputs=inputs if lane == "demand" else None,
        clock=d.clock,
    )
    if score is None:
        return "nothing_filed"
    score["mode"] = switch.mode
    write_score(d.job_dir(job_id), score)
    _append(d.run_dir, log_line(lane, score))
    if score["failed"] and switch.enforcing:
        watch.alert(d, job_id, CODE, "the read-back failed: " + ", ".join(score["failed"]))
        return "paged"
    return "failed_observed" if score["failed"] else "scored"


def after(d: Any, job_id: str, state: str) -> str:
    """After the lane recorded ``state``: score a delivered job. Never raises."""
    from ..proof import watch

    if state != "delivered" or getattr(d, "LANE", "") not in LANES:
        return ""
    if watch.in_proof_run(d):
        return "proof_run"
    try:
        return score_delivered(d, job_id)
    except Exception:  # logged with its trace; the delivered job stands
        logger.exception("library read-back for %s could not be scored", job_id)
        return "error"


__all__ = ["LOG", "SWITCH", "after", "log_line", "score_delivered"]
