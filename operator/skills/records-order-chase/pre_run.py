#!/usr/bin/env python3
"""records-order-chase pre-run: the weekly list of records orders that have not come in.

Class N (ADR 0050): no model composes anything. Letter 416 (#10, base fee)
committed: each week the Operator checks with the records vendor on records
ordered through the Operator that have not come in, and prepares the follow-up
for a person. Not included: chasing providers, deciding what is missing, or
judging whether records that arrived are complete. So this script:

1. reads every records order the Operator placed (the confirmed act rows,
   through the broker's read-only ``records_orders_placed`` verb), grouped by
   matter;
2. for each matter, reads its number and client from Smokeball and the
   vendor's locations for it (``pull.py``, in the connector venv);
3. selects every location the vendor does not show as completed
   (``report.py``), and lists every matter the vendor would not answer for, and
   every order whose matter is not known, as "could not be checked";
4. renders the email, with a follow-up note per request addressed to the
   vendor that a person copies and sends, writes the provenance handoff and the
   dispatch envelope to the ONE recipient the firm authored
   (``settings.recipient``), and wakes the turn so the seat sends it out of turn
   through the full gate. It sends nothing to the vendor or to any provider.

QUIET WEEKS. No order placed through the Operator yet: nothing is sent, and a
SUPPRESSED_WAKE row records ``no_orders_placed``. Orders exist and every one is
in: a short "nothing outstanding" email, so a person can tell a checked week
from a run that did not happen.

FAILURE SENDS NOTHING. An unauthored recipient, a ledger that cannot be read, a
vendor that is not connected on the seat, a read that could not run, an
envelope that cannot be written: each prints ``wakeAgent: false`` with a status
naming it and records a SUPPRESSED_WAKE row. Those are SMD's to fix; the firm is
not sent a failure note.

WHAT STDOUT CARRIES: the verdict, a status and counts. Never a matter number,
a name or a facility.

DRY RUN: ``SMD_RECORDS_CHASE_DRY=1`` runs the reads and prints counts only. It
writes nothing under ``.smd/`` and nothing to the broker. A run by hand must set
``HERMES_HOME=/opt/data/profiles/operator`` (the scheduler's value) or the cron
turn never finds the envelope.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import date, datetime, timezone

SKILL = "records-order-chase"
_SKILL_DIRNAME = "records-order-chase"
_BROKER_TIMEOUT_SECONDS = 10
_READ_VERB = "records_orders_placed"
ROUTING_LEG = "central"
#: The most matters one run reads; the rest are counted and come first next week.
MAX_MATTERS_PER_RUN = 60


def _load_skill_helpers():
    """The shared helpers vendored beside this file (canonical: operator/templates/skill_helpers.py)."""
    import importlib.util
    import sys as _sys
    from pathlib import Path as _Path

    candidates = [_Path(__file__).resolve().parent]
    for base in ("/opt/data/skills", "/app/skills"):
        candidates.append(_Path(base) / _SKILL_DIRNAME)
    for cand in candidates:
        module_path = cand / "skill_helpers.py"
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("skill_helpers_" + _SKILL_DIRNAME.replace("-", "_"), module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("skill_helpers.py is missing beside pre_run.py for " + _SKILL_DIRNAME)


_H = _load_skill_helpers()
_REPORT = _H.load_sibling(_SKILL_DIRNAME, __file__, "report.py", "records_order_chase_report")
_PULL = _H.load_sibling(_SKILL_DIRNAME, __file__, "pull.py", "records_order_chase_pull")


# ---------------------------------------------------------------------------
# Configuration and the clock
# ---------------------------------------------------------------------------


def chase_recipient(cfg: dict) -> str | None:
    """``settings.recipient`` on this skill: one address, or None (no imposed default)."""
    value = _H.find_skill_settings(cfg, SKILL).get("recipient")
    if isinstance(value, str) and "@" in value.strip() and " " not in value.strip() and "," not in value:
        return value.strip()
    return None


def chase_zone(cfg: dict) -> str:
    """``HERMES_TIMEZONE`` (what the cron runs in), else ``business_hours.timezone``, else UTC."""
    configured = os.environ.get("HERMES_TIMEZONE", "").strip()
    if configured:
        return configured
    hours = cfg.get("business_hours") if isinstance(cfg, dict) else None
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    return zone.strip() if isinstance(zone, str) and zone.strip() else "UTC"


def seat_day(moment: datetime, zone: str) -> date:
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(zone)).date()
    except Exception:  # noqa: BLE001 - an unknown zone reads as the UTC day
        return moment.astimezone(timezone.utc).date()


def _committed_day(stamp: object, zone: str) -> date | None:
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        moment = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return seat_day(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc), zone)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# The broker: the orders placed, and the wake rows
# ---------------------------------------------------------------------------


class LedgerUnreadable(RuntimeError):
    """The orders placed could not be read: no list rather than an empty one."""


def _broker_call(request: dict) -> dict:
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        raise OSError("no broker socket on this seat")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_BROKER_TIMEOUT_SECONDS)
        sock.connect(socket_path)
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    reply = json.loads(raw.decode("utf-8"))
    return reply if isinstance(reply, dict) else {}


def read_placed() -> list[dict]:
    """Every committed records-order act. Raises ``LedgerUnreadable`` on any failure."""
    try:
        reply = _broker_call({"action": _READ_VERB})
    except Exception as exc:
        raise LedgerUnreadable(type(exc).__name__) from exc
    orders = reply.get("orders")
    if reply.get("ok") is not True or not isinstance(orders, list):
        raise LedgerUnreadable("refused")
    return [o for o in orders if isinstance(o, dict)]


def _row(verb: str, action_type: str, basis: str, extra: dict) -> bool:
    """One wake row through the broker's agent-uid verb. Counts only. True on ack."""
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    metadata.update(extra)
    row = {"action_type": action_type, "actor": "agent", "actor_role": "agent", "skill_name": SKILL}
    row["metadata"] = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    try:
        return _broker_call({"action": verb, "row": row}).get("ok") is True
    except Exception as exc:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        _H.warn_observability_failure(exc, "records-order-chase heartbeat")
        return False


def _verdict(payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def _silent(status: str, counts: dict, *, dry: bool) -> int:
    if not dry and not _row("suppressed_wake_append", "SUPPRESSED_WAKE", status, counts):
        sys.stderr.write("[pre_run] records-order-chase: no email this run (" + status + "); row not recorded\n")
    return _verdict({"wakeAgent": False, "status": status, "dry_run": dry, **counts})


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def group_orders(orders: list[dict], zone: str) -> tuple[dict[str, list[date]], list[date]]:
    """Order days by matter id, and the days of orders whose matter is not known.
    An order with no readable commit time is dated by nothing and skipped only
    from the date list, never from the count: it is kept under its matter with
    the matter's other days, or counted as unknown at the epoch of the ledger."""
    by_matter: dict[str, list[date]] = {}
    unknown: list[date] = []
    for order in orders:
        day = _committed_day(order.get("committed_at"), zone) or date(1970, 1, 1)
        matter_id = order.get("matter_id")
        if isinstance(matter_id, str) and matter_id:
            by_matter.setdefault(matter_id, []).append(day)
        else:
            unknown.append(day)
    return by_matter, unknown


def _cap(by_matter: dict[str, list[date]]) -> tuple[dict[str, list[date]], int]:
    """The matters this run reads (longest-waiting first), and how many orders wait."""
    ordered = sorted(by_matter.items(), key=lambda kv: (min(kv[1]), kv[0]))
    kept = dict(ordered[:MAX_MATTERS_PER_RUN])
    waiting = sum(len(days) for _, days in ordered[MAX_MATTERS_PER_RUN:])
    return kept, waiting


def build_envelope(recipient: str, subject_line: str, body: str, skeleton_body: str, started_at: str) -> dict:
    dispatch = {
        "recipients": [recipient],
        "cc": [],
        "routing_leg": ROUTING_LEG,
        "subject": subject_line,
        "full_body": body,
        "skeleton_body": skeleton_body,
        "body_sha256_full": _H.canonical_body_sha256(body),
        "body_sha256_skeleton": _H.canonical_body_sha256(skeleton_body),
        "appends": [],
    }
    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [dispatch],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "records_chase_skeleton", "template": skeleton_body, "slots": {}}],
        "in_turn_enforce": True,
    }


def write_handoff(seeds: dict, matter_ids: list[str], started_at: str) -> None:
    """The matter numbers and dates the body prints, so the send gate reads them as READ.
    Best-effort: without it the gate refuses the body and the skeleton goes."""
    try:
        from pathlib import Path

        record = {"skill": SKILL, "started_at": started_at, "matter_ids": matter_ids, **seeds}
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + SKILL + ".json.tmp")
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, directory / (SKILL + ".json"))
    except Exception as exc:  # noqa: BLE001 - never change stdout or the wake
        sys.stderr.write("[pre_run] records-order-chase: handoff write failed (" + type(exc).__name__ + ")\n")


def run(cfg: dict, *, read_orders=read_placed, read_matters=None, clock=None, dry: bool = False) -> int:
    """Read, select, render, write, wake. Every failure is a silent, recorded verdict."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    read_matters = read_matters or _PULL.read_matters
    zone = chase_zone(cfg)
    today = seat_day(clock(), zone)
    recipient = chase_recipient(cfg)
    if recipient is None and not dry:
        return _silent("recipient_unauthored", {}, dry=dry)
    try:
        orders = read_orders()
    except LedgerUnreadable:
        return _silent("ledger_unreadable", {}, dry=dry)
    if not orders:
        return _silent("no_orders_placed", {"orders": 0}, dry=dry)
    by_matter, unknown = group_orders(orders, zone)
    kept, waiting = _cap(by_matter)
    reads = read_matters(sorted(kept)) if kept else {"matters": {}}
    if reads is None:
        return _silent("read_failed", {"orders": len(orders)}, dry=dry)
    if reads.get("notConnected"):
        return _silent("vendor_not_connected", {"orders": len(orders)}, dry=dry)
    vendor = str(reads.get("vendorName") or "the records vendor")
    matters = reads.get("matters") if isinstance(reads.get("matters"), dict) else {}
    report = _REPORT.build(kept, unknown, matters, vendor, today, not_read=waiting)
    counts = {
        "orders": report.orders,
        "matters": report.matters,
        "outstanding": len(report.outstanding),
        "unchecked": len(report.unchecked),
        "complete": report.complete,
        "not_read": waiting,
    }
    if dry or recipient is None:
        return _verdict({"wakeAgent": False, "status": "dry_run", "dry_run": True, **counts})
    body, skeleton_body = _REPORT.render(report), _REPORT.skeleton(report)
    stamp = _stamp(clock())
    write_handoff(_REPORT.handoff_records(report, today), sorted(kept), stamp)
    envelope = build_envelope(recipient, _REPORT.subject(report), body, skeleton_body, stamp)
    if not _H.write_dispatch_envelope(SKILL, envelope):
        return _silent("envelope_write_failed", counts, dry=dry)
    dispatch = envelope["dispatches"][0]
    stamps = {
        "render_mode": "templated",
        "dispatch_count": 1,
        "body_sha256": [
            {
                "body_sha256_full": dispatch["body_sha256_full"],
                "body_sha256_skeleton": dispatch["body_sha256_skeleton"],
            }
        ],
    }
    _row("emitted_wake_append", "EMITTED_WAKE", "chase_ready", {**counts, **stamps})
    return _verdict({"wakeAgent": True, "dispatch_expected": True, "status": "chase_ready", **counts})


def main() -> int:
    dry = os.environ.get("SMD_RECORDS_CHASE_DRY", "").strip() == "1"
    try:
        return run(_H.load_customer_yaml(None), dry=dry)
    except Exception as exc:  # noqa: BLE001 - a crashed run sends nothing, recorded
        sys.stderr.write("[pre_run] records-order-chase failed: " + type(exc).__name__ + "\n")
        return _silent("pre_run_crashed", {}, dry=dry)


if __name__ == "__main__":
    sys.exit(main())
