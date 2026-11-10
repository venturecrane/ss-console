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
* ``remove ADDRESS``: the address is no longer listed for this seat, AND its
  data is purged (``person_purge``): the person's directory in every store the
  seat walls per person, their per-sender memory, request-index, held-reply,
  help-pointer and sent-line rows, and any scheduled-send envelope naming them.
  Ends with a read-only probe; exits 1 if the address was not enrolled (its
  data is purged all the same) and 3 if anything of theirs is still there or
  any place could not be examined.
* ``purge ADDRESS``: the same, for an address that may no longer be (or never
  was) enrolled, so data a removal left behind can be cleared. Exits 0 only
  when the probe examined every place and found nothing.
* ``orphans``: read-only. The directories in the seat's walled stores that
  belong to nobody listed now, as counts and slugs (never content); exits 3
  when there are any, 2 when it could not examine a store or who is listed.
* ``purge --probe ADDRESS``: read-only. Lists what of theirs is on the volume
  (and whether they are still enrolled) and exits 3 if anything is, or if any
  place could not be examined ("could not examine ..."; never "found nothing").

``remove`` and ``purge`` run as ROOT on a seat, and only as root. Before
anything is removed every place is examined; if one cannot be (run as the
broker or agent uid, a directory it may not read, a planted link), nothing is
removed, the enrollment row included, and the command exits 3 naming each
place. Then a child process drops to the broker uid to remove the enrollment
row (the broker's store), and the parent, still root, deletes the files on the
agent's volume, which the broker uid cannot reach, and hands each database to a
child running as that file's owner (``person_purge``). The row goes first, so a
turn of theirs still in flight is refused by the store's roster check and
cannot recreate what the purge deletes.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import sys
from pathlib import Path

import yaml

from . import person_orphans, person_purge
from .logic_in_code import DEFAULT_PATH as DEFAULT_CONFIG
from .logic_in_code import PATH_ENVS as CONFIG_ENVS
from .sender_enrollment import EnrollmentError, EnrollmentStore

BROKER_USER = "workspace-broker"

#: The seat's volume, where the agent keeps everything the purge clears.
DATA_ROOT = "/opt/data"

#: Exit codes for remove and purge.
EXIT_NOT_ENROLLED = 1
EXIT_REFUSED = 2
EXIT_REMAINS = 3


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
    remove = sub.add_parser("remove", help="remove one enrolled address and purge its data")
    remove.add_argument("address")
    purge = sub.add_parser("purge", help="purge one address's data, enrolled or not")
    purge.add_argument("address")
    purge.add_argument("--probe", action="store_true", help="read-only: list what remains")
    orphans = sub.add_parser("orphans", help="read-only: walled-store directories that belong to nobody listed")
    for command in (remove, purge, orphans):
        command.add_argument("--data-root", default=DATA_ROOT)
        command.add_argument("--config", default=None)
    return parser


def _config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for name in CONFIG_ENVS:
        if os.environ.get(name):
            return Path(os.environ[name])
    return Path(DEFAULT_CONFIG)


def partition_roots(config: Path) -> tuple[list[Path], str]:
    """``(roots, error)``: the path of every store the seat authors with
    ``partition: sender``. A config that cannot be read is an error, never an
    empty list: a probe that could not look must not report clean."""
    try:
        doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [], f"{config}: {type(exc).__name__}"
    stores = doc.get("record_stores") if isinstance(doc, dict) else None
    if stores is not None and not isinstance(stores, list):
        return [], f"{config}: record_stores is not a list"
    roots = [
        Path(entry["path"])
        for entry in stores or []
        if isinstance(entry, dict)
        and entry.get("partition") == "sender"
        and isinstance(entry.get("path"), str)
        and entry["path"].startswith("/")
    ]
    return roots, ""


def listed_addresses(config: Path, store: EnrollmentStore | None) -> tuple[list[str], bool]:
    """``(addresses, has_domain_grant)``: the seat's people now, its authored
    roster's exact addresses, its administrators and its enrolled list. Raises
    when the config or the enrolled list cannot be read: an orphan count that
    could not look must not read as none."""
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    scope = doc.get("scope") if isinstance(doc, dict) else None
    scope = scope if isinstance(scope, dict) else {}
    entries = [e for e in (scope.get("inbound_allow_from") or []) if isinstance(e, str)]
    entries += [e for e in (scope.get("admins") or []) if isinstance(e, str)]
    exact = [e.strip().lower() for e in entries if not e.strip().startswith("@")]
    domain_grant = any(e.strip().startswith("@") for e in entries)
    exact += sorted((store or EnrollmentStore()).enrolled_addresses())
    return exact, domain_grant


def _orphans_command(args: argparse.Namespace, store: EnrollmentStore | None) -> int:
    """Counts and slugs of directories in the seat's walled stores that belong
    to nobody listed (``person_orphans``). Exits 3 when there are any, 2 when
    it could not look."""
    targets = targets_for(args.data_root, args.config)
    try:
        listed, domain_grant = listed_addresses(_config_path(args.config), store)
    except (OSError, yaml.YAMLError, EnrollmentError) as exc:
        print(f"refused: could not read who is listed ({type(exc).__name__})", file=sys.stderr)
        return EXIT_REFUSED
    result = person_orphans.orphan_partitions(targets, listed)
    if result.problems:
        for problem in result.problems:
            print(f"refused: could not examine {problem}", file=sys.stderr)
        return EXIT_REFUSED
    if not result.found:
        print("no walled-store directory belongs to nobody listed")
        return 0
    for root, slugs in result.found.items():
        print(f"{root}: {len(slugs)} with no listed owner: {', '.join(slugs)}")
    if domain_grant:
        print("note: this seat's roster grants a whole domain; a person at it has no slug here")
    return EXIT_REMAINS


def targets_for(data_root: str, config: str | None) -> person_purge.PurgeTargets:
    roots, error = partition_roots(_config_path(config))
    return person_purge.PurgeTargets(data_root=Path(data_root), partition_roots=roots, config_error=error)


def _remove_row(address: str, store: EnrollmentStore | None, isolate: bool) -> dict:
    """Remove the enrollment row as the broker uid: ``{"removed": bool}`` or
    ``{"error": str}``.

    ``isolate`` (the default when run as root): a forked child drops to the
    broker uid and removes the row, so every file it touches stays the
    broker's, while this process keeps root for the data. Without it (a test,
    or a command already running as the broker) the row is removed in-process.
    """
    if not isolate:
        try:
            return {"removed": (store or EnrollmentStore()).remove(address)}
        except (EnrollmentError, OSError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: broker uid, the row only, then exit at once
        os.close(read_fd)
        try:
            _drop_to_broker()
            out: dict = {"removed": (store or EnrollmentStore()).remove(address)}
        except BaseException as exc:  # noqa: BLE001 - reported to the parent, never raised
            out = {"error": f"{type(exc).__name__}: {exc}"}
        os.write(write_fd, json.dumps(out).encode("utf-8"))
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    chunks = []
    while True:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    _, status = os.waitpid(pid, 0)
    try:
        result = json.loads(b"".join(chunks).decode("utf-8"))
    except ValueError:
        result = {"error": f"the broker-uid child exited ({status}) without a result"}
    return result if isinstance(result, dict) else {"error": "malformed child result"}


def _enrollment_finding(address: str, store: EnrollmentStore | None) -> person_purge.Finding | None:
    """A finding when the address is still enrolled, or the list cannot be read."""
    try:
        listed = (store or EnrollmentStore()).enrolled_addresses()
    except (EnrollmentError, OSError) as exc:
        return person_purge.Finding("the enrollment list", "enrolled.json", 1, problem=type(exc).__name__)
    if person_purge.normalize_person_address(address) in listed:
        return person_purge.Finding("enrollment", "enrolled.json", 1)
    return None


def _print_findings(title: str, findings: list[person_purge.Finding]) -> None:
    print(title)
    for finding in findings:
        print(f"  {finding.line()}")


def _report(address: str, findings: list[person_purge.Finding], after: str) -> bool:
    """Print what could not be examined, then what is still there. True when
    either list is non-empty: the result is then never clean."""
    problems = [f for f in findings if f.problem]
    found = [f for f in findings if not f.problem]
    if problems:
        _print_findings(
            f"{address}: could not examine every place this seat keeps their data, so this is NOT a clean result",
            problems,
        )
    if found:
        _print_findings(f"{address}: STILL on this seat{after}", found)
    return bool(problems or found)


def _purge_command(
    args: argparse.Namespace,
    store: EnrollmentStore | None,
    targets: person_purge.PurgeTargets | None,
    isolate: bool | None,
) -> int:
    address = args.address
    if person_purge.normalize_person_address(address) is None:
        print(f"refused: {address!r} is not a person's address", file=sys.stderr)
        return EXIT_REFUSED
    if targets is None:
        targets = targets_for(args.data_root, args.config)
    if args.command == "purge" and args.probe:
        remaining = person_purge.probe(address, targets)
        enrolled = _enrollment_finding(address, store)
        if enrolled is not None:
            remaining.append(enrolled)
        if _report(address, remaining, ""):
            return EXIT_REMAINS
        print(f"{address}: nothing of theirs found on this seat")
        return 0
    # Every location is examined BEFORE the row goes: a run that cannot look
    # everywhere (the wrong uid above all) removes nothing, the row included.
    blocked = person_purge.preflight(address, targets)
    if blocked:
        _report(address, blocked, "")
        print(f"{address}: nothing removed; the enrollment row and every file are untouched")
        return EXIT_REMAINS
    row = _remove_row(address, store, os.geteuid() == 0 if isolate is None else isolate)
    if "error" in row:
        print(f"refused: the enrollment row could not be removed ({row['error']}); nothing purged", file=sys.stderr)
        return EXIT_REFUSED
    print(f"removed {address}" if row.get("removed") else f"{address} was not enrolled on this seat")
    report = person_purge.purge(address, targets)
    if report.removed:
        _print_findings("purged:", report.removed)
    remaining = list(report.remaining)
    enrolled = _enrollment_finding(address, store)
    if enrolled is not None:
        remaining.append(enrolled)
    if _report(address, remaining, " after the purge"):
        return EXIT_REMAINS
    print(f"{address}: probe found nothing of theirs on this seat")
    if args.command == "remove" and not row.get("removed"):
        return EXIT_NOT_ENROLLED
    return 0


def main(
    argv: list[str] | None = None,
    store: EnrollmentStore | None = None,
    *,
    purge_targets: person_purge.PurgeTargets | None = None,
    isolate: bool | None = None,
) -> int:
    """``store``, ``purge_targets`` and ``isolate`` are test seams. A caller that
    injects a ``store`` and no ``purge_targets`` gets ``remove`` as it was
    before the purge existed (the row only); a real invocation injects none of
    them, and ``remove`` purges."""
    args = _parser().parse_args(argv)
    purging = args.command == "purge" or (args.command == "remove" and (store is None or purge_targets is not None))
    if args.command == "orphans":
        return _orphans_command(args, store)
    if purging:
        return _purge_command(args, store, purge_targets, isolate)
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
