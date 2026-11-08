"""The drafting lane's second kind of job: "set up our ..." (``library_setup/``).

WHY INSIDE THE DRAFTING LANE. It already runs as the jobs uid with the case
system's client and the library interface, has a queue, a heartbeat, request
cards and a DELIVER wake. A lane of its own would need all of those again. A
setup envelope says ``"kind": "library_setup"``; ``DraftingLane.run_job`` hands
it here before anything a drafting job needs (the drafting firm inputs, the
demand child's wait, the allowance read).

WHAT IS DIFFERENT FROM A DRAFT:

* The child is ``jobs library-setup``: no model, no spend, no file filed. Its
  row's cents stay 0.
* Its verdict is recorded as the job's state directly. A draft's
  ``proof/watch.settle`` (the filed files' read-back) does not apply: nothing
  is filed.
* ``observed`` (the switch is off): recorded, no wake, nothing sent. The
  broker never replies on an observed row (it is not a replyable state).
* ``delivered`` (live): the document-drafter DELIVER wake, marked
  ``Mode: library setup``; the reply is the broker's words from the row.
* ``filed`` (an administrator's choice, ``mode: accept``): the made file is in
  the firm's library; ``library_setup_handoff`` hands it to the root writer and
  records what the writer answered.
* ``held`` / ``failed``: SMD is paged (``job_notes.tell``) and the firm is told
  nothing, as for every drafting stop.

Only code-authored text rides the wake: ids, the kind, codes. Never a name.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import job_notes, library_setup_handoff, verdict as verdict_mod
from .child import run_child
from .daemon import CHILD_ENV_PASS
from .firm_inputs import pin_env
from .skill_load import load_line

logger = logging.getLogger("jobs.drafting")

KIND = "library_setup"
SKILL = "document-drafter"
CHILD = "library-setup"
INPUTS_ENV = "JOBS_DRAFTING_INPUTS"
#: What a verdict may record, and the ledger state each is.
STATES = {"observed": "observed", "delivered": "delivered", "held": "held"}
_FIELDS = (
    "setup_outcome",
    "hold",
    "counts",
    "candidates",
    "dropped_files",
    "chart_donors",
    "chart_source",
    "attorney_key",
    "attorney_name",
    "unusable",
)


def envelope(lane: Any, job_id: str) -> dict[str, Any]:
    try:
        env = json.loads((lane.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return env if isinstance(env, dict) else {}


def is_setup(lane: Any, job_id: str) -> bool:
    return envelope(lane, job_id).get("kind") == KIND


def child_cmd(runner_cmd: list[str]) -> list[str]:
    """The lane's runner command with ``draft`` swapped for ``library-setup``."""
    return [*runner_cmd[:-1], CHILD] if runner_cmd and runner_cmd[-1] == "draft" else [*runner_cmd, CHILD]


def _write_job(lane: Any, job_id: str) -> Path:
    jd = lane.job_dir(job_id)
    doc = {**envelope(lane, job_id), "job_id": job_id, "slug": lane.customer_slug}
    (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    lane._chown_child(jd)
    return jd


def fields_of(v: dict[str, Any]) -> dict[str, Any]:
    """What the row records from a verdict: the report and, for a hold, its reason."""
    report = {k: v[k] for k in _FIELDS if k in v}
    out: dict[str, Any] = {"cents": 0, "report": report}
    if v.get("reason"):
        out["reason"] = str(v["reason"])[:500]
    return out


def compose_wake(job_id: str, env: dict[str, Any]) -> str:
    cls = str(env.get("setup_kind") or "")
    return "\n".join(
        [
            f"Run the {SKILL} skill's DELIVER mode for drafting job {job_id}.",
            load_line(SKILL),
            "Kind: drafting.",
            f"Document class: {cls if re.fullmatch(r'[a-z_]{1,40}', cls) else 'unknown'}.",
            "Outcome: delivered.",
            "Spend cents: 0.",
            f"Matter number: {env.get('library_matter_number', '')}.",
            "Folder id: none.",
            "Files: none.",
            f"Requested by: {env.get('requester') or ''}.",
            "Caption discrepancies: 0.",
            "Caption corrections: 0.",
            "Mode: library setup (nothing was filed; the reply is the job record's own words).",
        ]
    )


def _child(lane: Any, job_id: str, jd: Path) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS}
    env.update(lane.child_env, JOBS_DAEMON_JOB_ID=str(job_id))
    if lane.inputs_dir:
        env[INPUTS_ENV] = str(pin_env(lane.inputs_dir))
    env.setdefault("JOBS_SEAT", "client")
    env.setdefault("PATH", "/usr/bin:/bin")
    env.setdefault("HOME", str(jd))
    verdict_mod.clear(jd)
    with (jd / "daemon.log").open("a", encoding="utf-8") as log:
        return run_child(
            lane,
            job_id,
            [*child_cmd(lane.runner_cmd), str(jd)],
            cwd=jd,
            env=env,
            log=log,
            pidfile=lane.run_dir / "drafting-child.pid",
            preexec=None,
        )


def run(lane: Any, job_id: str) -> str:
    """One setup job to a recorded state. Returns the state, or ``deferred``."""
    from .broker_client import BrokerError

    try:
        jd = _write_job(lane, job_id)
        lane.broker.record(job_id, "running", {})
    except BrokerError as exc:
        logger.warning("broker not ready for library setup %s, deferring: %s", job_id, exc)
        return "deferred"
    code, out = _child(lane, job_id, jd)
    lane.jobs_run += 1
    outcomes = verdict_mod.read(jd, out)
    v = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else None
    if v is not None and v.get("outcome") == "filed":
        state, fields = library_setup_handoff.hand_over(lane, job_id, jd, v)
    elif v is None or str(v.get("outcome")) not in STATES:
        state, fields = "failed", {"cents": 0, "reason": f"no_verdict: the runner exited {code} without a verdict"}
    else:
        state, fields = STATES[str(v["outcome"])], fields_of(v)
    try:
        lane.broker.record(job_id, state, fields)
    except BrokerError as exc:
        logger.error("could not record %s for library setup %s: %s", state, job_id, exc)
    lane._write_state(job_id, state=state, finished_at=lane.clock(), reason=fields.get("reason"))
    if state in ("held", "failed"):
        job_notes.tell(lane, job_id, why=f"the library setup job ended {state}")
    elif state == "delivered":
        lane._write_state(
            job_id, wake={"pending": True, "attempts": 0, "task": compose_wake(job_id, envelope(lane, job_id))}
        )
    return state


__all__ = ["KIND", "child_cmd", "compose_wake", "envelope", "fields_of", "is_setup", "run"]
