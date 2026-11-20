"""The stage a running chronology has reached, reported to the broker (2026-11-19).

The ledger row's counts move only when the job's state does, so a running row
read 0 documents, 0 pages and 0 cents for hours and the status read had
nothing true to say about it. The driver already records each stage's start in
its run state file (``state.py``: ``status: running``, ``started``); the
daemon, which is root and owns the job dir, reads that file on its heartbeat
(every 30 seconds while the child runs) and reports a stage it has not
reported yet with ``medchron_job_record`` in ``running`` (a stage note:
``workspace_broker/medchron_progress.py``). The driver calls nothing.

``expected_finish`` rides along when a run state file carries one
(``state.expected_finish["at"]``, ISO-8601 UTC, written by ``eta.stamp``): the
broker stores it and shows it to no one yet.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..broker_client import BrokerError

logger = logging.getLogger("jobs.daemon")

#: The broker's own spellings (``medchron_progress``); anything else is not sent.
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_FINISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def current(job_dir: Path) -> dict[str, str]:
    """``{"stage": ..., "expected_finish": ...}`` from the job's run state
    files: the stage started last among those now running, and the latest
    finish any unit expects. Empty when nothing is running or nothing reads."""
    started, stage, finish = "", "", ""
    for path in sorted(job_dir.glob("data/*/runs/*/state.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        eta = data.get("expected_finish")
        f = eta.get("at") if isinstance(eta, dict) else None
        if isinstance(f, str) and _FINISH_RE.match(f) and f > finish:
            finish = f
        stages = data.get("stages")
        for name, rec in (stages if isinstance(stages, dict) else {}).items():
            if not isinstance(rec, dict):
                continue
            at = str(rec.get("started") or "")
            if rec.get("status") == "running" and _STAGE_RE.match(str(name)) and at >= started:
                started, stage = at, str(name)
    out = {"stage": stage} if stage else {}
    if stage and finish:
        out["expected_finish"] = finish
    return out


def report(lane: Any, job_id: str, sent: dict[str, str]) -> None:
    """Send the job's stage when it differs from the last one ``sent`` holds.
    A refusal (the job ended meanwhile, the broker away) is logged and the
    next heartbeat tries again; a heartbeat never raises."""
    note = current(lane.job_dir(job_id))
    if not note or sent.get(job_id) == note["stage"]:
        return
    try:
        lane.broker.record(job_id, "running", note)
    except BrokerError as exc:
        logger.info("stage %s for %s not recorded: %s", note["stage"], job_id, exc)
        return
    sent[job_id] = note["stage"]
