#!/usr/bin/env python3
"""Seat check: each lane's own attorney list agrees with the firm's attorney roster.

Run with the jobs venv python (it imports the runner's ``jobs.attorneys.agree``).
The boot smoke sends this file's text to the Machine over ssh; the in-image
self-test runs the image's copy at /app/attorney-roster-agrees-probe.py. One
program, two transports (operator/templates/seat-checks.sh, check id
``attorney-roster-agrees``).

WHY. Once the firm's attorney roster is installed it wins over the drafting
inputs' caption blocks, the demand inputs' signer map and customer.yaml's
form-letter signers. A list that still says something else is a choice SMD
must settle, never a silent difference: a failing self-test pages SMD. No
roster installed passes (nothing to compare; every reader uses its own list).

FALSIFIER: install a roster that lists a demand signer with another title, or
leaves out an email the drafting inputs carry, and this exits non-zero with the
count.

SMD_SEAT_ROOT prefixes every seat path; it is unset on a seat and set by the
tests to a fixture tree.
"""

from __future__ import annotations

import os
import sys

ROOT = os.environ.get("SMD_SEAT_ROOT", "")
PATHS = {
    "SMD_ATTORNEY_ROSTER_DIR": "/var/lib/smd-config/attorneys",
    "JOBS_DRAFTING_INPUTS": "/var/lib/smd-config/drafting",
    "JOBS_DEMAND_INPUTS": "/var/lib/smd-config/demand",
    "SMD_CUSTOMER_YAML_PATH": "/var/lib/smd-config/customer.yaml",
}


def main() -> int:
    if ROOT:
        for env, path in PATHS.items():
            os.environ[env] = f"{ROOT}{path}"
    try:
        from jobs.attorneys.agree import main as agree
    except ModuleNotFoundError as exc:
        # A runner from before the roster: no reader on this seat reads one, so
        # there is nothing a roster could disagree with.
        print(f"this seat's jobs runner has no attorney roster reader ({exc.name}); nothing to compare")
        return 0
    return agree()


if __name__ == "__main__":
    sys.exit(main())
