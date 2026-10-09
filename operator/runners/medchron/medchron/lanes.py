"""The daemon's lanes beyond the chronology and demand slots, started from one
table so ``daemon.py`` does not grow by a block per lane (it sits at its module
size ceiling). Each lane lives in its own module and exposes ``start_lane``; one
that fails to start is logged and stops nothing else."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("medchron.daemon")

#: The lanes, in start order. A name not in ``_module`` is refused.
LANES = ("drafting_lane", "litigation_lane", "negotiation_lane")


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
    raise ValueError(f"no lane module {name!r}")


def start_other_lanes(d: Any, *, stop: Callable[[], bool], poll_seconds: float) -> None:
    for lane in LANES:
        try:
            _module(lane).start_lane(d, stop=stop, poll_seconds=poll_seconds)
        except Exception:  # logged with its trace; the other lanes keep running
            logger.exception("%s not started", lane)


__all__ = ["LANES", "start_other_lanes"]
