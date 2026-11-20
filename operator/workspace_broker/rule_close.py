"""Close a stated rule with no letter to anyone, as SMD (2026-11-20).

    PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \\
      -m workspace_broker.rule_close <proposal_id> --by <smd address> \\
      --reason "<why>" [--dry-run]

Root only, on the seat (the broker refuses ``rule_close_by_smd`` from any other
uid). The rules-by-hand stopgap (``logic_in_code.rules_by_hand``): SMD records
a firm's rules by hand, so a rule row still open from before must end without a
lapse letter and without ever being confirmed by a "yes". This sets the row's
"ended, nobody is owed a note" columns, writes one audit row (ids only), sends
nothing, and refuses a row already committed, declined, ended or claimed. It
prints the row before and after; ``--dry-run`` prints what would change and
writes nothing. Modelled on ``ops_close``, whose transport it shares.

Exit status: 0 closed (or a clean dry run), 2 refused, 3 the broker could not
be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import ops_close

VERB = "rule_close_by_smd"


def build_request(proposal_id: str, *, closed_by: str, reason: str, dry_run: bool) -> dict[str, Any]:
    """The one request this tool sends. Validation is the broker's, not ours."""
    return {"action": VERB, "proposal_id": proposal_id, "closed_by": closed_by, "reason": reason, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="workspace_broker.rule_close", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("proposal_id", help="the rule's id (the XXXX in [rule XXXX])")
    ap.add_argument("--by", required=True, dest="closed_by", help="the SMD address closing it")
    ap.add_argument(
        "--reason", required=True, help="why it closes with no letter (kept on the row, never in the ledger)"
    )
    ap.add_argument("--dry-run", action="store_true", help="validate and print what would change; write nothing")
    ap.add_argument("--socket", default=os.environ.get("SMD_WORKSPACE_BROKER_SOCKET", ops_close.DEFAULT_SOCKET))
    a = ap.parse_args(argv)
    request = build_request(a.proposal_id.strip().lower(), closed_by=a.closed_by, reason=a.reason, dry_run=a.dry_run)
    try:
        reply = ops_close.call_broker(request, a.socket)
    except (OSError, ValueError) as exc:
        print(f"UNREACHABLE: could not talk to the broker at {a.socket}: {exc}", file=sys.stderr)
        return 3
    if not reply.get("ok"):
        print(f"REFUSED: {reply.get('message') or reply.get('error') or 'unknown refusal'}", file=sys.stderr)
        return 2
    print(json.dumps(reply, indent=1, sort_keys=True))
    if a.dry_run:
        print(f"dry run: rule {request['proposal_id']} would close quietly; nothing was written", file=sys.stderr)
    else:
        print(f"closed rule {request['proposal_id']} by SMD; no letter will be sent for it", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
