"""The jobs runner's lanes, every one started from one table by ``start_lanes``.

Each lane lives in its own module and exposes ``start_lane(d, stop=,
poll_seconds=)``, which builds the lane from the seat's shared settings ``d``
(``daemon.build``) and returns its thread, or None when this seat has no queue
for it; one that fails to start is logged and stops nothing else.

The lane modules are imported only inside ``_module``, when a lane is started,
never at this module's import: the runner core imports no lane
(``tests/test_runner_core_imports_no_lane.py`` imports this module and checks).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger("jobs.daemon")

#: The lanes, in start order. A name not in ``_module`` is refused. The
#: chronology is last, where it started before it was a lane (it ran on the
#: main thread after the others were up).
LANES = (
    "demand_lane",
    "drafting_lane",
    "litigation_lane",
    "negotiation_lane",
    "note_lane",
    "casefile_lane",
    "records_lane",
    "medchron",
)

#: Lanes started whatever the seat authors, as they were before ADR 0100's
#: predicate existed, so its arrival changed nothing for them: the chronology
#: carries the daemon's own liveness (``tick``, ``heartbeat.json``, which the
#: self-test's daemon-ticking check reads), and the demand lane gates itself on
#: its queue env (``SMD_DEMAND_QUEUE_DIR``, empty unless the seat authors it).
UNGATED = frozenset({"medchron", "demand_lane"})


def _module(name: str) -> Any:
    """A lane module by name, from a fixed table (imported only when started)."""
    if name == "medchron":
        from .medchron import lane as medchron_lane

        return medchron_lane
    if name == "demand_lane":
        from . import demand_lane

        return demand_lane
    if name == "drafting_lane":
        from . import drafting_lane

        return drafting_lane
    if name == "litigation_lane":
        from . import litigation_lane

        return litigation_lane
    if name == "negotiation_lane":
        from . import negotiation_lane

        return negotiation_lane
    if name == "note_lane":
        from . import note_lane

        return note_lane
    if name == "casefile_lane":
        from . import casefile_lane

        return casefile_lane
    if name == "records_lane":
        from . import records_lane

        return records_lane
    raise ValueError(f"no lane module {name!r}")


#: The entrypoint's list of the lanes this seat authors (ADR 0100). Unset means
#: an entrypoint from before ADR 0100: every lane starts, as it did then.
AUTHORED_ENV = "SMD_AUTHORED_LANES"


def authored(lane: str) -> bool:
    """True when the seat authors ``lane`` (``note_lane`` -> ``note``)."""
    words = os.environ.get(AUTHORED_ENV)
    return words is None or lane.removesuffix("_lane") in words.replace(",", " ").split()


def _forget(d: Any, name: str) -> None:
    """Remove a lane's beat and tick left by an earlier boot that ran it: the
    files sit on the volume and would claim a lane this daemon does not run
    (2026-10-31, pilot-smokeball: four releases waited 3 h on them)."""
    run_dir = getattr(d, "run_dir", None)
    if run_dir is None:
        return
    for leftover in (f"{name}-heartbeat.json", f"{name}-tick"):
        try:
            (run_dir / leftover).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove %s: %s", leftover, exc)


def start_lanes(d: Any, *, stop: Callable[[], bool], poll_seconds: float) -> list[Any]:
    """Start each authored lane; returns the threads that started, which the daemon
    joins on a Machine stop so a lane's child drains instead of being cut."""
    threads: list[Any] = []
    for lane in LANES:
        if lane not in UNGATED and not authored(lane):
            logger.info("%s not started: this seat does not author it", lane)
            _forget(d, lane.removesuffix("_lane"))
            continue
        try:
            t = _module(lane).start_lane(d, stop=stop, poll_seconds=poll_seconds)
        except Exception:  # logged with its trace; the other lanes keep running
            logger.exception("%s not started", lane)
            continue
        if t is not None:
            threads.append(t)
    return threads


__all__ = ["LANES", "UNGATED", "start_lanes"]
