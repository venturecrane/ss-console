"""A scheduled turn in the guide rehearsal, the way the seat's scheduler runs one.

Shared by every slice that adds a scheduled job to the guide rehearsal
(``test_guide_rehearsal_run.py``): the training slice's weekly report and the
desk slice's digest. Two steps, each the seat's own code:

1. :func:`run_pre_run` runs the skill's ``pre_run.py`` as its own process, as
   Hermes' scheduler does, with the persona home as its Hermes home and the
   seat's environment. Its last stdout line is the verdict (``wakeAgent``); a
   pre-rendered send is written as an envelope under the persona home.
2. :func:`cron_turn` runs the woken turn in the rehearsal's agent with a cron
   session id (``cron_<job id>_<date>_<time>``) whose job id is the MANAGED job
   ``bootstrap.translate`` materialized from the authored ``personas[].cron``
   entry into the persona's ``cron/jobs.json``. So ``cron_attribution`` resolves
   the routine from the scheduler's own store, and the trust plugin's
   ``pre_llm_call`` dispatches the envelope (``shared.prerendered_dispatch``)
   through the full send gate before the model is asked anything.

Departure from a seat, recorded: Hermes' scheduler is not running; the turn is
started by this harness with the id the legacy scheduler stamped, which
``cron_attribution.parse_cron_session`` reads first.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

OVERLAY = Path(__file__).resolve().parents[1]
SKILLS = OVERLAY.parent / "skills"


def managed_job(profile: Path, persona: str, skill: str) -> dict[str, Any]:
    """The managed job ``op-managed:<persona>:<skill>`` in the persona's cron store."""
    store = json.loads((profile / "cron" / "jobs.json").read_text(encoding="utf-8"))
    jobs = store.get("jobs") if isinstance(store, dict) else store
    prefix = f"op-managed:{persona}:{skill}"
    found = [
        j for j in jobs or [] if isinstance(j, dict) and str(j.get("name", "")).startswith(prefix)
    ]
    if len(found) != 1:
        raise AssertionError(
            f"expected one managed job {prefix!r}, found {[j.get('name') for j in jobs or []]}"
        )
    return found[0]


#: Runs a pre_run with ``shared.record_store.VOLUME_ROOT`` pointed at the
#: rehearsal's temp volume, the one departure the rehearsal process makes too
#: (its record stores live under the temp volume, not the seat's volume).
_WITH_VOLUME = (
    "import runpy, sys\n"
    "from pathlib import Path\n"
    "from shared import record_store\n"
    "record_store.VOLUME_ROOT = Path(sys.argv[1])\n"
    "sys.argv = sys.argv[2:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def run_pre_run(
    profile: Path, skill: str, *, volume: Path | None = None, timeout: float = 60
) -> dict[str, Any]:
    """Run ``skills/<skill>/pre_run.py`` as the scheduler does and return its
    verdict, with its stdout and stderr for the evidence."""
    # The rehearsal process already points the persona home at ``profile``
    # (``Rehearsal.build``), which is the home the scheduler gives a pre_run;
    # the child inherits it. (Not restated here: the variable's name is a
    # literal the sprawl ratchet counts per file, tests/sprawl-ratchet.test.ts.)
    env = dict(os.environ)
    if str(profile) not in env.values():
        raise AssertionError(f"the rehearsal process does not point its persona home at {profile}")
    # The installed overlay package on a seat; here, the tree under test.
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(OVERLAY), env.get("PYTHONPATH", "")) if p)
    script = str(SKILLS / skill / "pre_run.py")
    argv = (
        [sys.executable, "-c", _WITH_VOLUME, str(volume), script]
        if volume is not None
        else [sys.executable, script]
    )
    proc = subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    try:
        verdict = json.loads(lines[-1]) if lines else {}
    except ValueError:
        verdict = {}
    return {
        "verdict": verdict,
        "stdout": proc.stdout,
        "stderr": proc.stderr[-4000:],
        "code": proc.returncode,
    }


def cron_session(agent: Any, job_id: str, when: datetime) -> str:
    """Start a new session on ``agent`` whose id is the scheduler's cron id for
    ``job_id`` at ``when``, the way ``_drive.fresh_session`` starts any session."""
    old = str(getattr(agent, "session_id", "") or "")
    session_db = getattr(agent, "_session_db", None)
    if session_db is not None and old:
        try:
            session_db.end_session(old, "new_session")
        except Exception:  # noqa: BLE001 - /new suppresses this too
            pass
    agent.session_id = f"cron_{job_id}_{when:%Y%m%d}_{when:%H%M%S}"
    agent.reset_session_state(old_session_id=old)
    agent._last_flushed_db_idx = 0
    agent._session_db_created = False
    agent._ensure_db_session()
    return str(agent.session_id)


def cron_turn(
    r: Any,
    label: str,
    *,
    skill: str,
    script_output: str,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    persona: str = "guide",
) -> dict[str, Any]:
    """One scheduled turn of ``skill`` on the rehearsal ``r``. Returns the same
    record shape as ``Rehearsal.turn`` plus what reached the broker."""
    from tests_e2e._drive import fresh_ladder
    from tests_e2e._model_stub import ScriptedTurn

    fresh_ladder()
    job = managed_job(r.profile, persona, skill)
    session = cron_session(r.agent, str(job["id"]), datetime.now())
    marker = f"[e2e-turn {uuid.uuid4().hex}]"
    r.script.reset(marker)
    r.script.push(ScriptedTurn(tool_calls=list(calls or [])))
    mark = r.dispatches.mark()
    broker_mark = r.broker.mark()
    prompt = f"Run the {skill} skill on its schedule.\n\n[Script Output]\n{script_output}\n{marker}"
    result = r.agent.run_conversation(prompt)
    record = {
        "label": label,
        "session": session,
        "sender": None,
        "job_name": job.get("name"),
        "router_verdict": None,
        "exit": str((result or {}).get("turn_exit_reason") or ""),
        "pending": r.script.pending(),
        "offered": sorted({t for req in r.script.requests for t in req.tools}),
        "calls": [
            {
                "tool": c.tool,
                "args": c.args,
                "result": c.result
                if isinstance(c.result, str)
                else json.dumps(c.result, default=str),
            }
            for c in r.dispatches.since(mark)
        ],
        "broker": r.broker.since(broker_mark),
        "model_requests": len(r.script.requests),
    }
    for req in [*r.script.requests, *r.script.foreign]:
        r.model_log.append({"turn": label, "tools": req.tools, "body": req.body})
    r.turns.append(record)
    return record


__all__ = ["cron_session", "cron_turn", "managed_job", "run_pre_run"]
