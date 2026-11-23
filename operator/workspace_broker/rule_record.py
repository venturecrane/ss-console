"""Record a firm rule a client asked for in writing, as SMD (2026-11-23).

    python -m workspace_broker.rule_record --sender <attorney> --message-id <graph id> \\
      --text "<the rule, verbatim from that email>" --doc-kinds discovery_response \\
      [--output-class work_product] [--property voice] [--attorney "<name>"] \\
      --by <smd address> --reason "<why>" --dry-run
    ... the same, with --confirm <digest> in place of --dry-run
    python -m workspace_broker.rule_record --probe <proposal_id> [--specs /var/lib/smd-config/specs]

Run the way ``ops_close`` is run (its docstring names the seat's interpreter):
root only, on the seat; the broker refuses ``rule_record_by_smd`` and
``rule_record_probe`` from any other uid. ``--text`` may be given as
``--text-file`` instead, which keeps an apostrophe out of the shell's way.

The rule's text must be the client's own words. The broker checks the email in
the mailbox itself (rostered sender, a received message from that sender in the
Inbox and not a forward, recorded arriving, the rule's text in what he wrote)
and records the rule through the path a confirmed "yes" takes. The dry run
prints what will be recorded and a digest; the live run repeats it with
``--confirm``. Duplicates are the broker's to decide.

``--probe`` prints the rule's state, its install run, ``installed_at``, and
whether ``classes/work_product/wording.json`` (the store the drafting and
demand lanes read) carries it under a manifest that vouches for the file.

Exit status: 0 recorded (or a clean dry run, or already recorded), 2 refused,
3 the broker could not be reached.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import ops_close, seat_layout

VERB = "rule_record_by_smd"
PROBE = "rule_record_probe"
WORDING_REL = "classes/work_product/wording.json"


def wording_rules(spec_dir: str | Path) -> tuple[list[dict[str, Any]] | None, str]:
    """The installed wording rules, or ``(None, why)`` when the store cannot be
    trusted. The lanes' reader's checks, stdlib only: the manifest names the
    file and its digest matches."""
    root = Path(spec_dir)
    try:
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
        blob = (root / WORDING_REL).read_bytes()
    except FileNotFoundError:
        return [], "no wording store yet"
    except (OSError, ValueError) as exc:
        return None, f"the wording store cannot be read: {type(exc).__name__}"
    entry = ((manifest.get("wordings") or {}) if isinstance(manifest, dict) else {}).get(WORDING_REL)
    if not isinstance(entry, dict) or entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return None, "wording.json does not match the digest its manifest names"
    try:
        rules = json.loads(blob.decode("utf-8")).get("rules")
    except (ValueError, AttributeError):
        return None, "wording.json is not valid JSON"
    return (rules if isinstance(rules, list) else []), "ok"


def _read_arg(value: str | None, path: str | None, name: str) -> str:
    if path:
        return Path(path).read_text("utf-8")
    if value is None:
        raise SystemExit(f"--{name} or --{name}-file is required")
    return value


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="workspace_broker.rule_record", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--probe", metavar="PROPOSAL_ID", help="print where a recorded rule stands, and exit")
    ap.add_argument("--specs", default=seat_layout.SPECS, help="the seat's spec store (read by --probe)")
    ap.add_argument("--sender", help="the attorney who asked, as the email's sender")
    ap.add_argument("--message-id", help="the email's Graph id (requests index vendor_message_id)")
    ap.add_argument("--text", help="the rule, verbatim from the email")
    ap.add_argument("--text-file")
    ap.add_argument("--output-class", default="work_product")
    ap.add_argument("--property", default="voice", choices=("voice", "format"))
    ap.add_argument("--doc-kinds", help="comma-separated document kinds, e.g. discovery_response")
    ap.add_argument("--attorney", help="only this attorney's documents (optional)")
    ap.add_argument("--by", dest="recorded_by", help="the SMD address recording it")
    ap.add_argument("--reason", help="why SMD records it (kept off the ledger)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm", help="the digest the dry run printed")
    ap.add_argument("--socket", default=os.environ.get(ops_close.SOCKET_ENV, ops_close.DEFAULT_SOCKET))
    return ap


def _call(request: dict[str, Any], socket_path: str) -> dict[str, Any] | None:
    try:
        return ops_close.call_broker(request, socket_path)
    except (OSError, ValueError) as exc:
        print(f"UNREACHABLE: could not talk to the broker at {socket_path}: {exc}", file=sys.stderr)
        return None


def _probe(a: argparse.Namespace) -> int:
    reply = _call({"action": PROBE, "proposal_id": a.probe.strip().lower()}, a.socket)
    if reply is None:
        return 3
    if not reply.get("ok"):
        print(f"REFUSED: {reply.get('message') or reply.get('error')}", file=sys.stderr)
        return 2
    rules, why = wording_rules(a.specs)
    in_store = None if rules is None else any(r.get("rule_id") == reply.get("proposal_id") for r in rules)
    print(json.dumps({**reply, "in_wording_store": in_store, "wording_store": why}, indent=1, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.probe:
        return _probe(a)
    missing = [f for f in ("sender", "message_id", "doc_kinds", "recorded_by", "reason") if not getattr(a, f)]
    if missing or not (a.dry_run or a.confirm):
        print("REFUSED: need --" + ", --".join(m.replace("_", "-") for m in missing) + " and --dry-run or --confirm")
        return 2
    text = _read_arg(a.text, a.text_file, "text")
    kinds = [k.strip() for k in a.doc_kinds.split(",") if k.strip()]
    subject: dict[str, Any] = {"output_class": a.output_class, "property": a.property, "doc_kinds": kinds}
    if a.attorney:
        subject["attorney"] = a.attorney
    request = {
        "action": VERB,
        "sender": a.sender,
        "message_id": a.message_id,
        "text": text,
        "subject": subject,
        "recorded_by": a.recorded_by,
        "reason": a.reason,
        "dry_run": bool(a.dry_run),
        "confirm": a.confirm or "",
    }
    reply = _call(request, a.socket)
    if reply is None:
        return 3
    if not reply.get("ok") or not reply.get("accepted"):
        print(f"REFUSED: {reply.get('reason') or reply.get('message') or reply.get('error')}", file=sys.stderr)
        return 2
    print(json.dumps(reply, indent=1, sort_keys=True))
    if a.dry_run:
        print(f"dry run: nothing was written; record it with --confirm {reply['digest']}", file=sys.stderr)
    elif reply.get("already_recorded"):
        print(f"already recorded as rule {reply['proposal_id']}; nothing was written", file=sys.stderr)
    else:
        print(f"recorded rule {reply['proposal_id']}, install run {reply['run_id']}; probe it with --probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
