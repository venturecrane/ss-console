#!/usr/bin/env python3
"""statute-watch pre-run: build the monthly statute report and hand it to the seat.

Class N (ADR 0050): no model composes anything. This script reads the firm's
Smokeball records, selects every open or pending case whose Statute of
Limitation date falls in the next three months with no Filed date and no Case
number, renders the whole email (``render.py``), writes the provenance handoff
(``handoff_writer.py``) and the dispatch envelope (``dispatch_envelope.py``)
with the workbook attached (``workbook.py``),
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

SINCE LAST MONTH AND THE WORKBOOK. Last month's case set is read from the
state file (``changes.py``); every case that left the list is read again
(``pull.pull_details``) and classified by what its record shows now. The
workbook is built in the connector venv (``workbook.py``) and rides the
envelope as the pinned ``attachments`` entry, with ``body_without_attachment``
as the overlay's middle rung. A workbook that cannot be built costs only the
attachment: the body then says so. The state file is written after the
envelope, and never when ``SMD_STATUTE_WATCH_NO_STATE=1`` (a proof copy).

DRY RUN. ``SMD_STATUTE_WATCH_DRY=1`` runs the reads, the comparison and the
workbook build and prints counts only. It writes nothing under ``.smd/`` and
nothing to the broker, so it can be run on a seat to check the set without
sending.

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
_CLIENTS = _H.load_sibling(SKILL, __file__, "clients.py", "statute_watch_clients")
_CHANGES = _H.load_sibling(SKILL, __file__, "changes.py", "statute_watch_changes")
_WORKBOOK = _H.load_sibling(SKILL, __file__, "workbook.py", "statute_watch_workbook")


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


def handoff_payload(cases, change_list=()) -> dict:
    """What the send gate may treat as read: each case's number paired with
    every statute date the body or the workbook prints for it
    (``handoff_writer`` seeds the pairs). The changes come FIRST: the overlay
    seeds at most 100 records, and only a "since last month" line pairs a
    number with a date in the body."""
    return {
        "changes": _CHANGES.handoff_entries(list(change_list)),
        "cases": [
            {"matter_id": c.matter_id, "matter_number": c.matter_number, "statute_date": c.statute.isoformat()}
            for c in cases
        ],
    }


def _departure_names(departures: dict) -> dict:
    """Each departure read with the client names its title carries."""
    out = {}
    for matter_id, record in (departures or {}).items():
        if isinstance(record, dict):
            workbook_name, body_name = _CLIENTS.names_for(record.get("title"), None)
            record = {**record, "client": body_name, "client_workbook": workbook_name}
        out[str(matter_id)] = record
    return out


def _workbook_spec(rows: list, change_list: list | None) -> dict:
    case_rows = [_WORKBOOK.case_cells(r, _RENDER.attorney_cell(r)) for r in rows]
    change_rows = [
        [
            _RENDER.CHANGE_LABELS[c.kind],
            c.matter_number or _WORKBOOK.NO_NUMBER,
            c.client_workbook or _WORKBOOK.NO_CLIENT,
            c.statute.isoformat() if c.statute else None,
            _RENDER.change_detail(c),
        ]
        for c in change_list or []
    ]
    note = None
    if change_list is None:
        note = _RENDER.NO_PREVIOUS_LINE
    elif not change_list:
        note = _RENDER.NO_CHANGES_LINE
    return _WORKBOOK.spec(case_rows, change_rows, note)


def _compose(selection, today: date, *, pull_details, build_workbook) -> dict:
    """Everything the run sends, read and rendered; no writes."""
    previous = _CHANGES.load_state()
    departed = _CHANGES.departed(previous, selection.cases) if previous is not None else []
    cases = selection.cases
    details = pull_details(cases, [e["matter_id"] for e in departed]) if (cases or departed) else {}
    rows = _REPORT.rows(cases, details, names_for=_CLIENTS.names_for)
    change_list = None
    if previous is not None:
        raw_departed = details.get("departed")
        departures = _departure_names(raw_departed if isinstance(raw_departed, dict) else {})
        change_list = _CHANGES.build(previous, cases, {r.matter_id: r for r in rows}, departures, today)
    built = build_workbook(_workbook_spec(rows, change_list)) if build_workbook is not None else None
    week = _RENDER.due_this_week(rows)
    full = _RENDER.render_full(
        rows[: _REPORT.RENDER_CAP],
        total=len(cases),
        unreadable=selection.unreadable,
        week=week,
        changes=change_list,
        attached=built is not None,
    )
    attachment = None
    if built is not None:
        month = _RENDER.MONTHS[today.month - 1]
        attachment = {**built, "name": _ENVELOPE.attachment_name(month, today.year)}
    return {
        "cases": cases,
        "changes": change_list,
        "full": full,
        "without": _RENDER.without_attachment(full) if attachment else None,
        "attachment": attachment,
        "subject": _RENDER.subject(today, len(cases), week),
        "skeleton": _RENDER.render_skeleton(len(cases)),
    }


def run(cfg: dict, *, pull_matters, pull_details, build_workbook=None, clock=_utc_now, dry: bool = False) -> int:
    """Read, select, compare, render, write, wake. Every failure is a quiet verdict.

    ``clock`` is read twice: once for today's date before the pull, and once
    after it for the handoff and envelope stamp, so the overlay's twenty-minute
    binding window opens when the pull ends, not when it began. The state file
    is written last, and only when the envelope was."""
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
    out = _compose(selection, today, pull_details=pull_details, build_workbook=build_workbook)
    counts = {
        "open_matters": selection.open_matters,
        "cases": len(selection.cases),
        "listed": min(len(selection.cases), _REPORT.RENDER_CAP),
        "unreadable": selection.unreadable,
        "changes": None if out["changes"] is None else len(out["changes"]),
        "attached": out["attachment"] is not None,
    }
    if dry or recipient is None:
        return _verdict({"wakeAgent": False, "status": "dry_run", "dry_run": True, **counts})
    stamp = _stamp(clock())
    _HANDOFF.write_pre_run_handoff(handoff_payload(out["cases"], out["changes"] or ()), skill=SKILL, started_at=stamp)
    envelope = _ENVELOPE.build(
        recipient=recipient,
        subject=out["subject"],
        full_body=out["full"],
        skeleton_body=out["skeleton"],
        started_at=stamp,
        body_hash=_H.canonical_body_sha256,
        attachment=out["attachment"],
        body_without_attachment=out["without"],
    )
    if not _H.write_dispatch_envelope(SKILL, envelope):
        return _quiet("envelope_write_failed", counts, dry=dry)
    if not _CHANGES.state_disabled():
        _CHANGES.write_state(today, out["cases"])
    stamps = {"render_mode": "templated", "body_sha256": _ENVELOPE.wake_stamps(envelope), "dispatch_count": 1}
    _heartbeat("emitted_wake_append", "EMITTED_WAKE", "report_ready", {**counts, **stamps})
    return _verdict({"wakeAgent": True, "dispatch_expected": True, "status": "report_ready", **counts})


def main() -> int:
    dry = os.environ.get("SMD_STATUTE_WATCH_DRY", "").strip() == "1"
    try:
        cfg = _H.load_customer_yaml(None)
        return run(
            cfg,
            pull_matters=_PULL.pull_matters,
            pull_details=lambda cases, departed: _PULL.pull_details(cases, departed, title_names=_CLIENTS.parse_title),
            build_workbook=lambda spec: _PULL.build_workbook(_WORKBOOK.SNIPPET, spec),
            dry=dry,
        )
    except Exception as exc:  # noqa: BLE001 - any fault is a quiet, recorded verdict; a crashed report sends nothing
        sys.stderr.write("[pre_run] statute-watch failed: " + type(exc).__name__ + "\n")
        return _quiet("pre_run_crashed", {}, dry=dry)


if __name__ == "__main__":
    sys.exit(main())
