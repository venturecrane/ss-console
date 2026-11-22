"""The quiesce file: ``<run_dir>/quiesce`` (``/run/smd-jobs/quiesce`` on a seat).

A JSON object ``{"until": <epoch seconds>, "by": str, "reason": str}``, written
root-owned by whoever needs the seat to stop taking work: the entrypoint's
supervisor on a Machine stop (``by: "machine-stop"``), and the fleet rollout's
busy gate before it deploys. While it is present and ``until`` is in the
future, the daemon and every lane claim nothing: no queued envelope, no
resume of a claimed job, no lane proof. Work already running finishes and
keeps its heartbeat; deliver wakes still go out (finishing work is not new
work).

THE EXPIRY IS THE SAFETY. A quiesce whose writer died (a crashed laptop in the
middle of a rollout) must never starve the queue, so an expired file is
ignored, and so is one that cannot be read or parsed: "cannot evaluate" reads
as "not quiesced" here because the cost of the opposite is a seat that silently
stops working. Each such file is logged once per lane (per content), not every
tick.

The run dir is on the volume (a bind mount), so the file survives a reboot.
The entrypoint removes a ``machine-stop`` one at boot (its job ended with the
Machine); any other writer's file stands until it expires or its writer
removes it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("jobs.daemon")

NAME = "quiesce"


def read(d: Any) -> dict[str, Any] | None:
    """The live quiesce record, or None when absent, expired, or unreadable."""
    p = d.run_dir / NAME
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        _once(d, f"unreadable:{exc}", "quiesce file %s unreadable (%s); ignored", p, exc)
        return None
    try:
        doc = json.loads(raw)
        until = float(doc["until"])
    except (ValueError, TypeError, KeyError) as exc:
        _once(d, f"bad:{raw[:200]}", "quiesce file %s is not {until, by, reason} (%s); ignored", p, exc)
        return None
    if until <= d.clock():
        _once(d, f"expired:{until}", "quiesce file %s expired at %s (by %s); ignored", p, until, doc.get("by"))
        return None
    _once(
        d,
        f"live:{until}:{doc.get('by')}",
        "%s lane quiesced until %s by %s: %s",
        d.LANE,
        until,
        doc.get("by"),
        doc.get("reason"),
    )
    return doc


def active(d: Any) -> bool:
    return read(d) is not None


def stops_start(d: Any, job_id: str, fresh_claim: bool) -> bool:
    """Look again right before a job starts: a marker written after the claim's
    own look (a fleet release's gate, 2026-11-22 review) wins, and the job is
    not started. A claim made this tick goes back to the queue as if never
    made; a resumed job stays claimed and resumes once the marker is gone."""
    if not active(d):
        return False
    if fresh_claim:
        _unclaim(d, job_id)
    return True


def _unclaim(d: Any, job_id: str) -> None:
    """Return the envelope to the queue; the job dir goes too when the claim made it."""
    jd = d.job_dir(job_id)
    env = jd / "envelope.json"
    if env.is_file():
        env.replace(d.queue / f"{job_id}.json")
    if [p.name for p in jd.iterdir()] == ["daemon.json"]:
        (jd / "daemon.json").unlink()
        jd.rmdir()
    else:
        d._write_state(job_id, claimed=False)
    logger.warning("%s lane quiesced after claiming %s; returned it to the queue", d.LANE, job_id)


def _once(d: Any, key: str, msg: str, *args: Any) -> None:
    seen: set[str] = d._quiesce_logged
    if key in seen:
        return
    seen.add(key)
    logger.warning(msg, *args)


__all__ = ["NAME", "active", "read", "stops_start"]
