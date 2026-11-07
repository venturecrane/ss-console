"""The roster one job reads, read once per job.

A drafting job asks who the attorney is three times (before anything is paid,
the caption's attorney block, the library file), and a demand once per stage
that signs; each asks :func:`roster_for` with the job's RUN, so every read in
one job sees the same roster even if the firm-inputs applier swaps a new one in
mid-job. The run, never the seat: the roster is a file on disk, and asking a
run for its seat builds the case-system client, which a render-only resume with
nothing accepted never needed (the library resolver defers it behind a LazyStore).
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.library import roster as roster_mod

_KEPT = "_attorney_roster"


def roster_for(holder: Any) -> roster_mod.Roster:
    """The seat's installed roster (``ok``, ``absent`` or ``unusable``), kept on
    ``holder`` (the job's run) after the first read when it can hold it."""
    kept = getattr(holder, _KEPT, None)
    if isinstance(kept, roster_mod.Roster):
        return kept
    read = roster_mod.load()
    try:
        setattr(holder, _KEPT, read)
    except (AttributeError, TypeError):  # a holder that cannot keep it (None, a frozen object) reads again next time
        pass
    return read


__all__ = ["roster_for"]
