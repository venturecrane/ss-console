"""SMD's two acts on a ledger brief held before it filed (``review_first.py``).

    python -m jobs.drafting.release <job_id> --by <who> --why <what was read>
    python -m jobs.drafting.release <job_id> --by <who> --why <reason> --discard

Root, on the seat, through the broker's socket (``$SMD_WORKSPACE_BROKER_SOCKET``):

* **release** asks the broker's root-only ``drafting_job_resume`` with
  ``release_held``: the lane re-runs the job, which files exactly the held
  bytes (sha256-checked against what it held), proves the read-back, and sends
  the normal reply. Nothing is written into the attorney notes.
* **discard** records the job's reason as discarded (``drafting_job_record``,
  the same ``failed`` state, so it can never be released afterwards): it stays
  unfiled, and no reply goes to the firm.

Both refuse a job that is not held for SMD.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from ..broker_client import BrokerClient, BrokerError

SOCKET_ENV = "SMD_WORKSPACE_BROKER_SOCKET"
HELD = "held_for_smd:"
DISCARDED = "held_for_smd_discarded:"


def _held(client: Any, job_id: str) -> dict[str, Any]:
    row = client._request({"action": "drafting_job_status", "job_id": job_id}).get("job")
    if not row:
        raise SystemExit(f"release: no drafting job {job_id}")
    if row.get("state") != "failed" or not str(row.get("reason") or "").startswith(HELD):
        raise SystemExit(f"release: job {job_id} is {row.get('state')}, not held for SMD before filing")
    return row


def release(client: Any, job_id: str, by: str, why: str) -> dict[str, Any]:
    _held(client, job_id)
    return client._request(
        {"action": "drafting_job_resume", "job_id": job_id, "reason": why, "release_held": True, "by": by}
    )


def discard(client: Any, job_id: str, by: str, why: str) -> dict[str, Any]:
    _held(client, job_id)
    reason = f"{DISCARDED} by {by}: {why}"[:500]
    return client._request(
        {"action": "drafting_job_record", "job_id": job_id, "state": "failed", "fields": {"reason": reason}}
    )


def main(argv: list[str] | None = None, client: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.release", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--by", required=True, help="who read the brief")
    ap.add_argument("--why", required=True, help="what was read, or why it is discarded")
    ap.add_argument("--discard", action="store_true", help="leave it unfiled instead")
    a = ap.parse_args(argv)
    if not a.by.strip() or not a.why.strip():
        ap.error("--by and --why must say something")
    client = client or BrokerClient(os.environ.get(SOCKET_ENV) or "")
    try:
        out = (discard if a.discard else release)(client, a.job_id, a.by.strip(), a.why.strip())
    except BrokerError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
