#!/usr/bin/env python3
"""product-guide pre-run: the library-change email, one email per person.

One managed cron row (``op-managed:<persona>:product-guide``, ``wake_policy:
pre_run_decides``, daily) runs this script. All of the work is the overlay's
``shared.library_notice.pre_run``, in code: it reads the authored entries in
this skill's ``changes.yaml``, the roster, and each person's OWN mark
(``library-seen.json`` in their partition of the progress store), decides who
has not been sent the newest change (at most ten a day; the rest wait for the
next day), renders one email per person to that person only from the authored
lines, and writes the consume-once envelope the overlay sends out of turn
through the full gate (``shared.prerendered_dispatch``).

NOTHING NEW FOR ANYONE: ``wakeAgent: false``, so no model call and no send.

EVERY RUN LEAVES A ROW: an EMITTED_WAKE when the turn wakes, a SUPPRESSED_WAKE
otherwise, with the status and counts. The hourly work-health triage grades the
slots from these rows, so a run that never happened, a notice whose send was
refused, or a run that crashed (it wakes the turn with nothing to send) is an
item there.

WHAT STDOUT AND THE ROWS CARRY: the verdict, the status and counts. Never an
address or a line of the email.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it (``shared`` is the
installed overlay package).

DRY RUN: ``SMD_LIBRARY_NOTICE_DRY=1`` prints the verdict and counts (people,
due, chunk, deferred, reset, unreadable, the newest id) and writes nothing.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    dry = os.environ.get("SMD_LIBRARY_NOTICE_DRY", "").strip() == "1"
    try:
        from shared import library_notice

        verdict = library_notice.pre_run(dry=dry)
    except Exception as exc:  # noqa: BLE001 - no overlay to call: still a verdict, still a wake
        sys.stderr.write("[pre_run] product-guide failed: " + type(exc).__name__ + "\n")
        verdict = {"wakeAgent": not dry, "status": "pre_run_crashed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
