"""SMD's command line for invite codes and enrolled senders (ADR 0106).

Runs ON THE SEAT, in an ssh session, against the broker-owned store::

    fly ssh console -a hermes-<slug> -C "/opt/workspace-broker/.venv/bin/python \\
        -m workspace_broker.sender_enrollment_cli issue --label rep-west --days 14"

Run as root it drops to the broker uid before touching the store, so every
file it creates stays the broker's (a root-owned codes.db would lock the broker
out of its own store). The store is reached through the broker's bind path
(``/run/smd-sender-enrollment``), because the agent's home ``/opt/data`` is
0700 to every other uid.

Commands:

* ``issue --label L --days D [--max-uses N] [--name NAME]``: mint one code.
  The code is printed ONCE, here; only its salted hash is kept. Default one
  use. With ``--name`` the welcome greets the person by that name. (The usual
  way is an administrator emailing the guide "I need a code for <name>"; this
  command is the fallback.)
* ``codes`` (or ``list``) ``[--label L]``: every code by id, label and name,
  with expiry, uses and whether revoked. Never the code.
* ``revoke CODE_ID``: the code stops working at once.
* ``enrolled``: every enrolled address, with the label of the code it used.
* ``remove ADDRESS``: the address is no longer listed for this seat.
"""

from __future__ import annotations

import argparse
import os
import pwd
import sys

from .sender_enrollment import EnrollmentError, EnrollmentStore

BROKER_USER = "workspace-broker"


def _drop_to_broker() -> None:
    if os.geteuid() != 0:
        return
    try:
        entry = pwd.getpwnam(BROKER_USER)
    except KeyError:
        return
    os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sender_enrollment_cli", description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="mint one invite code (printed once)")
    issue.add_argument("--label", required=True)
    issue.add_argument("--days", type=int, required=True)
    issue.add_argument("--max-uses", type=int, default=1)
    issue.add_argument("--name")
    codes = sub.add_parser("codes", aliases=["list"], help="list codes by id, label and name, never the code")
    codes.add_argument("--label")
    revoke = sub.add_parser("revoke", help="revoke one code by its id")
    revoke.add_argument("code_id")
    sub.add_parser("enrolled", help="list enrolled addresses")
    remove = sub.add_parser("remove", help="remove one enrolled address")
    remove.add_argument("address")
    return parser


def main(argv: list[str] | None = None, store: EnrollmentStore | None = None) -> int:
    args = _parser().parse_args(argv)
    if store is None:
        _drop_to_broker()
        store = EnrollmentStore()
    try:
        if args.command == "issue":
            code, code_id = store.issue(label=args.label, days=args.days, max_uses=args.max_uses, name=args.name)
            print(f"code: {code}")
            print(f"id: {code_id}  label: {args.label}  days: {args.days}  max uses: {args.max_uses}")
            print("This is the only time the code is shown. Give it to the person; SMD keeps only its hash.")
        elif args.command in ("codes", "list"):
            for row in store.codes(args.label):
                state = "revoked" if row.revoked_at else "active"
                print(
                    f"{row.code_id}  {row.label}  name {row.name or '-'}  created {row.created_at}  "
                    f"expires {row.expires_at}  uses {row.uses}/{row.max_uses}  {state}"
                )
        elif args.command == "revoke":
            if not store.revoke(args.code_id):
                print(f"no active code with id {args.code_id}", file=sys.stderr)
                return 1
            print(f"revoked {args.code_id}")
        elif args.command == "enrolled":
            for entry in store.enrolled():
                print(
                    f"{entry['address']}  name {entry.get('name') or '-'}  code {entry.get('code_id', '')}  "
                    f"label {entry.get('label', '')}  since {entry.get('enrolled_at', '')}"
                )
        elif args.command == "remove":
            if not store.remove(args.address):
                print(f"{args.address} is not enrolled on this seat", file=sys.stderr)
                return 1
            print(f"removed {args.address}")
    except EnrollmentError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
