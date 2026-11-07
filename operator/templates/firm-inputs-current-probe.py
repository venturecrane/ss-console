#!/usr/bin/env python3
"""Seat check: each lane's firm inputs are the version the applier says they are.

Run with the hermes venv python (stdlib only). The boot smoke sends this file's
text to the Machine over ssh; the in-image self-test runs the image's copy at
/app/firm-inputs-current-probe.py. One program, two transports
(operator/templates/seat-checks.sh, check id ``jobs-firm-inputs-current``).

WHY. A lane's live path (``/var/lib/smd-config/<lane>``, and the chronology's
``medchron-firm.yaml``) is a symlink to a versioned copy, swapped live by the
root firm-inputs applier (overlay firm_inputs_applier), which records what it
committed in ``/run/smd-firm-inputs/state.json``. The heartbeat reports that
file as ``firm_inputs``, and the release tick reads it back. If the symlink and
the state disagree, or the symlink names a version that is gone, the fleet table
says one thing and the lane runs another (or nothing): this check is what makes
that disagreement fail out loud.

FALSIFIER: point a lane's symlink at another version, delete the version it
names, or delete the state file, and this exits non-zero naming the lane.

SMD_SEAT_ROOT prefixes every seat path; it is unset on a seat and set by the
tests to a fixture tree.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = os.environ.get("SMD_SEAT_ROOT", "")
STATE = Path(f"{ROOT}/run/smd-firm-inputs/state.json")
CONFIG = Path(f"{ROOT}/var/lib/smd-config")
DIR_LANES = ("demand", "drafting", "litigation", "casefile", "records", "attorneys")


def live_and_version(lane: str, label: str) -> tuple[Path, str]:
    if lane == "medchron":
        return CONFIG / "medchron-firm.yaml", f"medchron-firm.v{label}.yaml"
    return CONFIG / lane, f"{lane}.v{label}"


def main() -> int:
    links = [lane for lane in (*DIR_LANES, "medchron") if os.path.islink(live_and_version(lane, "x")[0])]
    if not STATE.exists() and not links:
        # An image from before versioned inputs (a rollback re-smoke): nothing to compare.
        print("no versioned firm inputs on this seat (an image before the firm-inputs applier)")
        return 0
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{STATE}: unreadable ({type(exc).__name__}) while {', '.join(links)} are versioned")
        return 1
    if not isinstance(state, dict):
        print(f"{STATE}: not a JSON object")
        return 1
    problems: list[str] = []
    for lane, entry in sorted(state.items()):
        if lane not in (*DIR_LANES, "medchron") or not isinstance(entry, dict):
            problems.append(f"{lane}: not a lane entry")
            continue
        label = entry.get("etag8")
        live, _ = live_and_version(lane, "x")
        if label is None:
            if os.path.lexists(live):
                problems.append(f"{lane}: the state records no version but {live} exists")
            continue
        _, want = live_and_version(lane, str(label))
        if not os.path.islink(live):
            problems.append(f"{lane}: {live} is not a symlink to a version")
        elif os.path.basename(os.readlink(live)) != want:
            problems.append(f"{lane}: {live} -> {os.readlink(live)}, the state says {want}")
        elif not (CONFIG / want).exists():
            problems.append(f"{lane}: {want} is missing")
    problems += [f"{lane}: versioned on the seat but not in the state" for lane in links if lane not in state]
    if problems:
        print("; ".join(problems))
        return 1
    print(f"{len(state)} lane(s) on the version the state records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
