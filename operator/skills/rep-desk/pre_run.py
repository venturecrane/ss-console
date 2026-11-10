#!/usr/bin/env python3
"""rep-desk pre-run: the hourly digest of what is due, one email per person.

ss-console #3946. One managed cron row (``op-managed:<persona>:rep-desk``,
``wake_policy: pre_run_decides``, hourly) runs this script. All of the work is
the overlay's ``shared.person_digest.run``, in code: it reads the roster and
each person's OWN partition of the reminder store, decides who is due this hour
(at most ten; the rest wait for the next hour), renders one email per person to
that person only, and writes the consume-once envelope the overlay sends out of
turn through the full gate (``shared.prerendered_dispatch``), plus the dated
handoff that lets the gate verify each date the code wrote.

NOTHING DUE FOR ANYONE: ``wakeAgent: false``, so no model call and no send.

EVERY RUN LEAVES A ROW. An EMITTED_WAKE row when a digest is ready, a
SUPPRESSED_WAKE row otherwise, through the broker's agent-uid verb, with the
status and the counts. The hourly work-health triage grades the slots from
these rows, so a run that never happened, or a digest whose send was refused,
is an item there.

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
import socket
import sys

SKILL = "rep-desk"
_TIMEOUT_SECONDS = 10


def _desk_row(verb: str, action_type: str, basis: str, extra: dict) -> bool:
    """One wake row through the broker's agent-uid verb. Counts only. True on ack."""
    path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not path:
        return False
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    metadata.update(extra)
    row = {
        "action_type": action_type,
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_TIMEOUT_SECONDS)
            sock.connect(path)
            sock.sendall(json.dumps({"action": verb, "row": row}).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw.decode("utf-8")).get("ok") is True
    except Exception as exc:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        sys.stderr.write("[pre_run] rep-desk heartbeat failed (" + type(exc).__name__ + ")\n")
        return False


def _desk_counts(verdict: dict) -> dict:
    return {k: v for k, v in verdict.items() if isinstance(v, int) and not isinstance(v, bool)}


def desk_run(dry: bool = False) -> int:
    from shared import person_digest
    from shared.customer_config import CustomerConfig

    verdict = person_digest.run(CustomerConfig.from_volume(), dry=dry)
    counts = _desk_counts(verdict)
    if not dry:
        if verdict.get("wakeAgent"):
            _desk_row(
                "emitted_wake_append", "EMITTED_WAKE", str(verdict["status"]), {**counts, "render_mode": "templated"}
            )
        else:
            _desk_row("suppressed_wake_append", "SUPPRESSED_WAKE", str(verdict["status"]), counts)
    print(json.dumps(verdict, sort_keys=True))
    return 0


def main() -> int:
    dry = os.environ.get("SMD_REP_DESK_DRY", "").strip() == "1"
    try:
        return desk_run(dry=dry)
    except Exception as exc:  # noqa: BLE001 - a crashed run sends nothing and says so
        # It WAKES the turn (which sends nothing): an EMITTED_WAKE row no
        # delivery closes is what the triage reports as a silent run. A
        # SUPPRESSED_WAKE here would be a recorded no-op, and nobody would know.
        sys.stderr.write("[pre_run] rep-desk failed: " + type(exc).__name__ + "\n")
        if not dry:
            _desk_row("emitted_wake_append", "EMITTED_WAKE", "pre_run_crashed", {})
        print(json.dumps({"wakeAgent": not dry, "status": "pre_run_crashed"}, sort_keys=True))
        return 0


if __name__ == "__main__":
    sys.exit(main())
