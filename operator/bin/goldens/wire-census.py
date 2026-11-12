#!/usr/bin/env python3
"""Every broker verb the jobs runner names is in the frozen wire snapshot.

``runners/jobs/tests/test_wire_snapshot.py`` freezes the request and reply
shapes of the runner-to-broker verbs it can drive. This census closes the
other side: it lists every ``"action": "<name>"`` literal in the runner's
source whose name is a registered broker verb (``workspace_broker.verbs.TABLE``)
and fails when one is missing from ``runners/jobs/tests/goldens/wire.json``, so
a verb added to the runner cannot ride the wire unfrozen. A CI step, not a
test: it reads source to enumerate, and the snapshot test runs the code.

    python3 operator/bin/goldens/wire-census.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
RUNNER = OPERATOR / "runners" / "jobs" / "jobs"
WIRE = OPERATOR / "runners" / "jobs" / "tests" / "goldens" / "wire.json"
ACTION = re.compile(r"""["']action["']\s*:\s*["']([a-z_]+)["']""")


def runner_verbs() -> dict[str, list[str]]:
    """Registered broker verbs the runner names, each with where it is named."""
    if str(OPERATOR) not in sys.path:
        sys.path.insert(0, str(OPERATOR))
    from workspace_broker.verbs import TABLE

    out: dict[str, list[str]] = {}
    for path in sorted(RUNNER.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in ACTION.findall(line):
                if name in TABLE:
                    out.setdefault(name, []).append(f"{path.relative_to(OPERATOR)}:{n}")
    return out


def main() -> int:
    frozen = set(json.loads(WIRE.read_text(encoding="utf-8"))["verbs"])
    named = runner_verbs()
    missing = {v: where for v, where in sorted(named.items()) if v not in frozen}
    print(f"wire census: {len(named)} broker verbs named by the runner; {len(frozen)} frozen in wire.json")
    for verb, where in missing.items():
        print(f"  NOT FROZEN: {verb} ({', '.join(where)})")
    if missing:
        print("Add the verb to test_wire_snapshot.py (a lane cell or DIRECT) and regenerate with JOBS_WRITE_WIRE=1.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
