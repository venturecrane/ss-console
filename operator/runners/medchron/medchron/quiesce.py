"""The quiesce file: ``<run_dir>/quiesce`` (``/run/smd-medchron/quiesce`` on a seat).

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

logger = logging.getLogger("medchron.daemon")

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


def _once(d: Any, key: str, msg: str, *args: Any) -> None:
    seen: set[str] = d._quiesce_logged
    if key in seen:
        return
    seen.add(key)
    logger.warning(msg, *args)


__all__ = ["NAME", "active", "read"]
