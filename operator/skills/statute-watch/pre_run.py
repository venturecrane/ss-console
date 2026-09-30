#!/usr/bin/env python3
"""statute-watch pre-run: build the monthly statute report and hand it to the seat.

Class N (ADR 0050): no model composes anything. This script reads the firm's
Smokeball records, selects every open or pending case whose Statute of
Limitation date falls in the next three months with no Filed date and no Case
number, renders the whole email (``render.py``), writes the provenance handoff
(``handoff_writer.py``) and the dispatch envelope (``dispatch_envelope.py``),
and wakes the agent ONLY so the overlay's ``pre_llm_call`` hook sends the
envelope out of turn through the full gate. The woken turn ends; it sees no
case list.

WHAT STDOUT CARRIES. Hermes injects this script's stdout into the woken turn's
prompt, so stdout carries the wake verdict, a status word and counts. Never a
matter number, a name or a date: the case list travels only in the 0600
envelope the model cannot read.

FAILURE SENDS NOTHING. A matter list that cannot be read, an unauthored
recipient, an envelope that cannot be written: each prints
``wakeAgent: false`` with a status naming it, and records a SUPPRESSED_WAKE
heartbeat row naming the same status. No email goes to anyone about a failed
run; the heartbeat row, the send reconcile (a scheduled report with no send)
and the seat log carry it.

DRY RUN. ``SMD_STATUTE_WATCH_DRY=1`` runs the reads and the selection and
prints counts only. It writes nothing under ``.smd/`` and nothing to the
broker, so it can be run on a seat to check the set without sending.

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import date, datetime, timezone

SKILL = "statute-watch"
_HEARTBEAT_TIMEOUT_SECONDS = 10


def _load_skill_helpers():
    """The shared helpers vendored beside this file (canonical: operator/templates/skill_helpers.py).

    Looked up beside pre_run.py first, then under /opt/data/skills and /app/skills,
    the two places the seat image and the volume seed put this skill's files.
    A missing copy is a packaging defect, not a runtime condition to tolerate.
    """
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


_SKILL_DIRNAME = "statute-watch"
_H = _load_skill_helpers()
_REPORT = _H.load_sibling(SKILL, __file__, "report.py", "statute_watch_report")
_RENDER = _H.load_sibling(SKILL, __file__, "render.py", "statute_watch_render")
_PULL = _H.load_sibling(SKILL, __file__, "pull.py", "statute_watch_pull")
_ENVELOPE = _H.load_sibling(SKILL, __file__, "dispatch_envelope.py", "statute_watch_dispatch_envelope")
_HANDOFF = _H.load_sibling(SKILL, __file__, "handoff_writer.py", "statute_watch_handoff_writer")


# ---------------------------------------------------------------------------
# Configuration: the authored recipient and the seat's clock
# ---------------------------------------------------------------------------


def report_recipient(cfg: dict) -> str | None:
    """``personas[].skills[statute-watch].settings.recipient``: one address, or
    None. Unauthored means no report is sent (ADR 0035, no imposed default)."""
    value = _H.find_skill_settings(cfg, SKILL).get("recipient")
    if isinstance(value, str) and "@" in value.strip() and " " not in value.strip():
        return value.strip()
    return None


def seat_timezone(cfg: dict) -> str:
    """The seat's IANA zone: ``HERMES_TIMEZONE`` (what the seat's cron runs
    in), else ``business_hours.timezone``, else UTC."""
    configured = os.environ.get("HERMES_TIMEZONE", "").strip()
    if configured:
        return configured
    hours = cfg.get("business_hours") if isinstance(cfg, dict) else None
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    return zone.strip() if isinstance(zone, str) and zone.strip() else "UTC"


def local_today(now: datetime, zone: str) -> date:
    """The calendar day it is at the seat. An unknown zone reads as UTC."""
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(zone)).date()
    except Exception:  # noqa: BLE001 - an unreadable zone name falls back to the UTC day, reported on stderr
        sys.stderr.write("[pre_run] statute-watch: unknown timezone, using UTC\n")
        return now.astimezone(timezone.utc).date()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Output: the heartbeat row and the verdict line
# ---------------------------------------------------------------------------


def _heartbeat(verb: str, action_type: str, basis: str, extra: dict) -> bool:
    """One wake row through the broker's uid-gated verb. True only on ack.
    Counts and hashes only: no row carries a case."""
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        return False
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    metadata.update(extra)
    row = {"action_type": action_type, "actor": "agent", "actor_role": "agent", "skill_name": SKILL}
    row["metadata"] = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HEARTBEAT_TIMEOUT_SECONDS)
            sock.connect(socket_path)
            sock.sendall(json.dumps({"action": verb, "row": row}).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw.decode("utf-8")).get("ok") is True
    except Exception as exc:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        _H.warn_observability_failure(exc, "statute-watch heartbeat")
        return False


def _verdict(payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def _quiet(status: str, counts: dict, *, dry: bool) -> int:
    """No email this run. Recorded (unless dry), then ``wakeAgent: false``.
    A heartbeat that did not land leaves one stderr line naming the status, so
    a suppressed run is never traceless."""
    if not dry and not _heartbeat("suppressed_wake_append", "SUPPRESSED_WAKE", status, counts):
        sys.stderr.write("[pre_run] statute-watch: no report this run (" + status + "); heartbeat not recorded\n")
    return _verdict({"wakeAgent": False, "status": status, "dry_run": dry, **counts})


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def handoff_payload(listed) -> dict:
    """What the send gate may treat as read: each listed case's number paired
    with its own statute date (``handoff_writer`` seeds the pairs)."""
    return {
        "cases": [
            {"matter_id": c.matter_id, "matter_number": c.matter_number, "statute_date": c.statute.isoformat()}
            for c in listed
        ]
    }


def run(cfg: dict, *, pull_matters, pull_details, clock=_utc_now, dry: bool = False) -> int:
    """Read, select, render, write, wake. Every failure is a quiet verdict.

    ``clock`` is read twice: once for today's date before the pull, and once
    after it for the handoff and envelope stamp, so the overlay's twenty-minute
    binding window opens when the pull ends, not when it began."""
    today = local_today(clock(), seat_timezone(cfg))
    recipient = report_recipient(cfg)
    if recipient is None and not dry:
        return _quiet("recipient_unauthored", {}, dry=dry)
    try:
        selection = _REPORT.select(pull_matters(), today)
    except _REPORT.StatuteFieldAbsent:
        return _quiet("statute_field_absent", {}, dry=dry)
    except _REPORT.PullFailed:
        return _quiet("matter_list_failed", {}, dry=dry)
    listed = selection.cases[: _REPORT.RENDER_CAP]
    details = pull_details(listed) if listed else {}
    counts = {
        "open_matters": selection.open_matters,
        "cases": len(selection.cases),
        "listed": len(listed),
        "unreadable": selection.unreadable,
    }
    rows = _REPORT.rows(listed, details)
    full = _RENDER.render_full(rows, total=len(selection.cases), unreadable=selection.unreadable)
    skeleton = _RENDER.render_skeleton(len(selection.cases))
    if dry or recipient is None:
        return _verdict({"wakeAgent": False, "status": "dry_run", "dry_run": True, **counts})
    stamp = _stamp(clock())
    _HANDOFF.write_pre_run_handoff(handoff_payload(listed), skill=SKILL, started_at=stamp)
    envelope = _ENVELOPE.build(
        recipient=recipient,
        subject=_RENDER.subject(today),
        full_body=full,
        skeleton_body=skeleton,
        started_at=stamp,
        body_hash=_H.canonical_body_sha256,
    )
    if not _H.write_dispatch_envelope(SKILL, envelope):
        return _quiet("envelope_write_failed", counts, dry=dry)
    stamps = {"render_mode": "templated", "body_sha256": _ENVELOPE.wake_stamps(envelope), "dispatch_count": 1}
    _heartbeat("emitted_wake_append", "EMITTED_WAKE", "report_ready", {**counts, **stamps})
    return _verdict({"wakeAgent": True, "dispatch_expected": True, "status": "report_ready", **counts})


def main() -> int:
    dry = os.environ.get("SMD_STATUTE_WATCH_DRY", "").strip() == "1"
    try:
        cfg = _H.load_customer_yaml(None)
        return run(cfg, pull_matters=_PULL.pull_matters, pull_details=_PULL.pull_details, dry=dry)
    except Exception as exc:  # noqa: BLE001 - any fault is a quiet, recorded verdict; a crashed report sends nothing
        sys.stderr.write("[pre_run] statute-watch failed: " + type(exc).__name__ + "\n")
        return _quiet("pre_run_crashed", {}, dry=dry)


if __name__ == "__main__":
    sys.exit(main())
