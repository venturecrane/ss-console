"""The daemon's lanes beyond the chronology and demand slots, started from one
table so ``daemon.py`` does not grow by a block per lane (it sits at its module
size ceiling). Each lane lives in its own module and exposes ``start_lane``; one
that fails to start is logged and stops nothing else."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("medchron.daemon")

#: The lanes, in start order. A name not in ``_module`` is refused.
LANES = ("drafting_lane", "litigation_lane", "negotiation_lane", "note_lane", "casefile_lane")


def _module(name: str) -> Any:
    """A lane module by name, from a fixed table (imported only when started)."""
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
    raise ValueError(f"no lane module {name!r}")


def start_other_lanes(d: Any, *, stop: Callable[[], bool], poll_seconds: float) -> list[Any]:
    """Start each lane; returns the threads that started, which the daemon
    joins on a Machine stop so a lane's child drains instead of being cut."""
    threads: list[Any] = []
    for lane in LANES:
        try:
            t = _module(lane).start_lane(d, stop=stop, poll_seconds=poll_seconds)
        except Exception:  # logged with its trace; the other lanes keep running
            logger.exception("%s not started", lane)
            continue
        if t is not None:
            threads.append(t)
    return threads


__all__ = ["LANES", "start_other_lanes"]
