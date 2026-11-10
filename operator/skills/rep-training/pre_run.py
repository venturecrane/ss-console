#!/usr/bin/env python3
"""rep-training pre-run: render the weekly representative progress report.

Class N for the report itself (ADR 0050): the words are rendered by code
(``shared.training_report``, the installed overlay package), from the guide
seat's progress store only, never from notes or leads, and dispatched by the
platform before the model is asked anything. When nobody is enrolled or no
administrator is authored, the turn is not woken at all.

WHAT STDOUT CARRIES. The verdict, a status word and counts. Never a name or
an address.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it (``shared`` is the
installed overlay package, not a sibling file).

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        from shared import training_report
    except Exception as exc:  # noqa: BLE001 - no overlay on this interpreter: say so, wake nothing
        sys.stderr.write("[pre_run] rep-training: overlay unavailable (" + type(exc).__name__ + ")\n")
        print(json.dumps({"wakeAgent": False, "status": "overlay_unavailable"}, sort_keys=True))
        return 0
    return training_report.main()


if __name__ == "__main__":
    raise SystemExit(main())
