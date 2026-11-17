"""The seats a fleet run may have changed, for whoever republishes their specs.

A seat's signed spec (ADR 0094 amendment) is compiled against what the seat
runs: its image, its skills release and its jobs bundle. Until 2026-11-16 a
spec was published only on a push to main, so a release that landed an image
with a newer lane schema left the seat's spec pinned to the older one, and the
seat reported "spec unverified" until some later push happened to republish it
(2026-11-04: six lanes on one client seat, three on the pilot, open for hours).

So every seat a run decided (done, rolled back, failed, or a tooling error) is
appended, one slug per line, to the file named by ``FLEET_LANDED_FILE``.
release-tick.yml reads it after the run and, when it names a seat, dispatches
Deploy, which then runs only its spec publisher (the one job that holds the
signing key) over an empty range at main's head. Over-reporting is harmless: the publisher writes nothing for a
spec that is already current. Unset, this is a no-op (a laptop run, a test).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from .rollout import LEDGER_STATES

ENV = "FLEET_LANDED_FILE"
#: The outcomes after which the seat may run something it did not run before:
#: the decided ones. waiting_idle, held, halted and pending changed nothing.
DECIDED = LEDGER_STATES

_lock = threading.Lock()


def note(seat: str, state: str, environ: Mapping[str, str] | None = None) -> None:
    """Record ``seat`` when ``state`` is a decided outcome and the sink is set."""
    path = (environ if environ is not None else os.environ).get(ENV, "")
    if not path or state not in DECIDED:
        return
    with _lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(seat + "\n")
