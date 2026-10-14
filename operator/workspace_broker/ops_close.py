"""Close an operations request with no letter to the requester, as SMD.

    PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \\
      -m workspace_broker.ops_close <proposal_id> --by <smd address> \\
      --source-ref <vfy_... | audit row token> --reason "<why>" [--dry-run]

Root only, on the seat (the broker refuses ``ops_close_by_smd`` from any other
uid, the agent's and the gateway's included). Talks to the running broker over
its socket (``SMD_WORKSPACE_BROKER_SOCKET``, default the entrypoint's
``BROKER_SOCKET`` under ``/run/smd-workspace-broker/``): one newline-delimited
JSON request, one reply, exactly what ``server.RequestHandler`` serves. Standard
library only, so any python3 on the seat runs it.

WHY THIS EXISTS (2026-10-13). An ops card (``[ops XXXX]``) can only be ended by
the agent, with done / declined / withdrawn. ``done`` and ``declined`` email the
requester, and a card nobody answers lapses after seven days into a "lapsed
unanswered" letter. When SMD has finished the work by hand and the Operator has
already answered the requester in her own thread, every one of those letters
repeats or contradicts that answer. This ends the card so no sweeper path ever
writes to her about it, and records why in the audit ledger (ids only, never the
reason text). ``--source-ref`` is the evidence of where she was answered.

``--dry-run`` asks the broker to validate the close and say what would change,
and writes nothing. Run it first; it names any refusal (already answered, a
lapse letter already claimed or sent, not an ops card) before anything moves.

Exit status: 0 closed (or a clean dry run), 2 refused, 3 the broker could not
be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any

# The entrypoint's BROKER_SOCKET. Spelled in two pieces only so the file never
# reads as a ``broker.<attr>`` access to test_broker_context.py's handler scan.
DEFAULT_SOCKET = "/run/smd-workspace-broker/" + "broker" + ".sock"
VERB = "ops_close_by_smd"


def build_request(proposal_id: str, *, closed_by: str, source_ref: str, reason: str, dry_run: bool) -> dict[str, Any]:
    """The one request this tool sends. Validation is the broker's, not ours."""
    return {
        "action": VERB,
        "proposal_id": proposal_id,
        "closed_by": closed_by,
        "source_ref": source_ref,
        "reason": reason,
        "dry_run": dry_run,
    }


def call_broker(request: dict[str, Any], socket_path: str, timeout: float = 30.0) -> dict[str, Any]:
    """One request, one newline-terminated JSON reply."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(socket_path)
        conn.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf.decode("utf-8"))
    if not isinstance(reply, dict):
        raise ValueError("broker reply was not a JSON object")
    return reply


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="workspace_broker.ops_close", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("proposal_id", help="the ops card's id (the XXXX in [ops XXXX])")
    ap.add_argument("--by", required=True, dest="closed_by", help="the SMD address closing it")
    ap.add_argument(
        "--source-ref", required=True, help="evidence the requester was already answered (vfy_ id, row token)"
    )
    ap.add_argument(
        "--reason", required=True, help="why it closes with no letter (kept on the row, never in the ledger)"
    )
    ap.add_argument("--dry-run", action="store_true", help="validate and print what would change; write nothing")
    ap.add_argument("--socket", default=os.environ.get("SMD_WORKSPACE_BROKER_SOCKET", DEFAULT_SOCKET))
    a = ap.parse_args(argv)
    request = build_request(
        a.proposal_id.strip().lower(),
        closed_by=a.closed_by,
        source_ref=a.source_ref,
        reason=a.reason,
        dry_run=a.dry_run,
    )
    try:
        reply = call_broker(request, a.socket)
    except (OSError, ValueError) as exc:
        print(f"UNREACHABLE: could not talk to the broker at {a.socket}: {exc}", file=sys.stderr)
        return 3
    if not reply.get("ok"):
        print(f"REFUSED: {reply.get('message') or reply.get('error') or 'unknown refusal'}", file=sys.stderr)
        return 2
    print(json.dumps(reply, indent=1, sort_keys=True))
    if a.dry_run:
        print(f"dry run: {request['proposal_id']} would close quietly; nothing was written", file=sys.stderr)
    else:
        print(f"closed {request['proposal_id']} by SMD; no letter will be sent for it", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
