"""End a HELD litigation status job that SMD finished by hand, as `failed`, once.

    python -m medchron.litigation_close <job_id> --reason "..." --source-ref <vfy_id|audit_id> [--dry-run]

Root only, on the seat, with the medchron venv's python: the same uid and
environment as the runner daemon (`SMD_WORKSPACE_BROKER_SOCKET`).

WHY THIS EXISTS (2026-10-14). A litigation job the runner held stays `held`
forever when SMD then filed the deliverable itself and told the requester: no
transition leaves `held` but `failed`, and nothing on the seat makes it. A held
job is still replyable (`reply_binding.py`), so a late completion turn could
answer the requester a second time, and the job reads as open work.

WHAT IT DOES. One call to the broker's root-only `litigation_job_close`:

  1. the ledger row moves `held -> failed` in one write transaction, its
     `reason` set to the token `closed`, SMD's sentence in `close_reason`, the
     evidence id in `close_ref`; a closed job never moves again (no resume);
  2. one `LITIGATION_JOB_FAILED` audit row, ids and tokens only:
     job_id, state, prev_state, trigger, reason=`closed`, closed_by=`smd`,
     source_ref. Work-health reads the `closed` token as decided work.

WHAT IT EMITS TO THE CLIENT: nothing. It writes no queue file, no resume marker
and no daemon state, so no wake is composed and no completion turn exists. If
anything later asks to answer the job, the reply binding refuses a `failed`
job ("the requester is told nothing"), and the agent's status tool tells the
turn to send the client nothing.

What it refuses: any state but `held` (so a second run is a refusal, never a
second row), an empty reason, a source ref that is not a `vfy_<ULID>` or an
audit row ULID. `--dry-run` runs every check and prints the audit row it would
write; it writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .broker_client import BrokerClient, BrokerError
from .daemon import SOCKET_ENV

VERB = "litigation_job_close"


def request(job_id: str, reason: str, source_ref: str, *, dry_run: bool) -> dict[str, Any]:
    return {"action": VERB, "job_id": job_id, "reason": reason, "source_ref": source_ref, "dry_run": dry_run}


def main(argv: list[str] | None = None, client: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.litigation_close", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--reason", required=True, help="what SMD did, and how the requester was told")
    ap.add_argument("--source-ref", required=True, help="the evidence: a crane_verify id or an audit row id")
    ap.add_argument("--dry-run", action="store_true", help="run every check; write nothing")
    a = ap.parse_args(argv)
    if client is None:
        sock = os.environ.get(SOCKET_ENV, "").strip()
        if not sock:
            print(f"REFUSED: {SOCKET_ENV} is not set; run this as root in the daemon's environment", file=sys.stderr)
            return 2
        client = BrokerClient(sock)
    try:
        out = client._request(request(a.job_id, a.reason, a.source_ref, dry_run=a.dry_run))
    except BrokerError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    if not a.dry_run:
        print(f"closed {a.job_id}: held -> failed; nothing was sent and nothing will answer it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
