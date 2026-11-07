"""The roster one job reads, read once per job.

A drafting job asks who the attorney is twice (the library file and the
caption's attorney block), and a demand once per stage that signs; each asks
:func:`roster_for` with the job's seat, so every read in one job sees the same
roster even if the firm-inputs applier swaps a new one in mid-job.
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.library import roster as roster_mod

_KEPT = "_attorney_roster"


def roster_for(seat: Any) -> roster_mod.Roster:
    """The seat's installed roster (``ok``, ``absent`` or ``unusable``), kept on
    ``seat`` after the first read when the seat object can hold it."""
    kept = getattr(seat, _KEPT, None)
    if isinstance(kept, roster_mod.Roster):
        return kept
    read = roster_mod.load()
    try:
        setattr(seat, _KEPT, read)
    except (AttributeError, TypeError):  # a seat that cannot hold it (None, a frozen object) reads again next time
        pass
    return read


__all__ = ["roster_for"]
