#!/usr/bin/env python3
"""rep-desk pre-run: the hourly digest of what is due, one email per person.

ss-console #3946. One managed cron row (``op-managed:<persona>:rep-desk``,
``wake_policy: pre_run_decides``, hourly) runs this script. All of the work is
the overlay's ``shared.person_digest.pre_run``, in code: it reads the roster and
each person's OWN partition of the reminder store, decides who is due this hour
(at most ten; the rest wait for the next hour), renders one email per person to
that person only, and writes the consume-once envelope the overlay sends out of
turn through the full gate (``shared.prerendered_dispatch``), plus the dated
handoff that lets the gate verify each date the code wrote.

NOTHING DUE FOR ANYONE: ``wakeAgent: false``, so no model call and no send.

EVERY RUN LEAVES A ROW (``shared.person_digest.wake_row``): an EMITTED_WAKE
when the turn wakes, a SUPPRESSED_WAKE otherwise, with the status and counts.
The hourly work-health triage grades the slots from these rows, so a run that
never happened, a digest whose send was refused, or a run that crashed (it
wakes the turn with nothing to send) is an item there.

WHAT STDOUT AND THE ROWS CARRY: the verdict, the status and counts. Never an
address, a name, a reminder or a date.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it (``shared`` is the
installed overlay package).

DRY RUN: ``SMD_REP_DESK_DRY=1`` prints the verdict and counts and writes nothing.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    dry = os.environ.get("SMD_REP_DESK_DRY", "").strip() == "1"
    try:
        from shared import person_digest

        verdict = person_digest.pre_run(dry=dry)
    except Exception as exc:  # noqa: BLE001 - no overlay to call: still a verdict, still a wake
        sys.stderr.write("[pre_run] rep-desk failed: " + type(exc).__name__ + "\n")
        verdict = {"wakeAgent": not dry, "status": "pre_run_crashed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
