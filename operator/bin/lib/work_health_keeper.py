"""The mailbox keeper and the one-machine rule, in SMD's hourly work triage.

THE KEEPER. Every run of the mailbox keeper's pre_run leaves a SUPPRESSED_WAKE
row on the seat (``operator/skills/mailbox-keeper/pre_run.py``), counts and
status words only: ``decision_basis`` ``mailbox_keeper_ran`` or
``mailbox_keeper_failed``, ``status``, ``mode``, and ``classify`` when the day's
list ran. This module turns those rows into items:

* the newest run of the last day failed (``mailbox_keeper_failed``: a failed
  read, ``send_check_failed``, a failed send, unsaved state, an unreachable
  broker);
* the day's reading stopped (``classify`` ``failed`` or ``fence``);
* no list went out today: no run with mode ``daily`` that ended ``done`` or
  ``already_sent_today`` since the seat-local ``send_after_hour``, checked from
  two hours after it.

THE MACHINES. A seat runs ONE machine. On 2026-11-16 a second machine came back
after a host failure, and the office seat sent its morning list twice
(destroyed, vfy_01M7JHGS6DWDENGD1V1T02Q5MY). More than one ``started`` machine
is an item, unless the newest started in the last ``MACHINE_GRACE`` (a deploy
or release tick replacing the machine runs two for a moment; Fly's machine list
carries no release marker, so the age is the only signal). Read through the
fleet's Fly client (``bin/lib/fleet/fly.py``), only when work-health runs with
``--machines`` on a host with a Fly credential (the smd-ops triage); elsewhere it
is a named gap, never an all-clear.

Pure except ``read_machines``.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from bin.lib.work_health import Item, hhmm, item, meta, parse_ts, stamped

SKILL = "mailbox-keeper"
WINDOW = timedelta(hours=26)
MACHINE_GRACE = timedelta(minutes=15)
LIST_DUE_AFTER = timedelta(hours=2)
FAILED = "mailbox_keeper_failed"
OK_LIST = ("done", "already_sent_today")
WORDS = {
    "read_failed": "could not read the mailbox, so nothing was filed or sent",
    "send_check_failed": "could not ask AgentMail whether today's list was sent, so it did not send",
    "digest_failed": "could not send the morning list",
    "sent_state_unsaved": "sent the list but could not save what it listed",
    "state_unsaved": "could not save its record, so nothing left the inbox",
    "state_unreadable": "could not read its own state",
    "settings_invalid": "has settings the broker refuses",
    "broker_unreachable": "could not reach the seat's broker",
    "broker_refused": "was refused by the seat's broker",
}


def keeper_settings(config: dict) -> Optional[dict]:
    """The seat's mailbox-keeper settings when it lists the skill, else None."""
    for persona in (config or {}).get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if isinstance(skill, dict) and skill.get("name") == SKILL and skill.get("enabled") is not False:
                found = skill.get("settings")
                return found if isinstance(found, dict) else {}
    return None


def _keeper_rows(rows: list[dict], now: datetime) -> list[tuple[datetime, dict]]:
    return [
        (ts, meta(row))
        for ts, row in stamped(rows)
        if row.get("action_type") == "SUPPRESSED_WAKE" and row.get("skill_name") == SKILL and now - ts <= WINDOW
    ]


def _missed_list(seat: str, runs: list[tuple[datetime, dict]], now: datetime, settings: dict) -> list[Item]:
    zone = ZoneInfo(str(settings.get("timezone") or "America/Phoenix"))
    try:
        hour = int(settings.get("send_after_hour", 6))
    except (TypeError, ValueError):
        hour = 6
    local = now.astimezone(zone)
    due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local < due + LIST_DUE_AFTER:
        return []
    sent = any(ts >= due and m.get("mode") == "daily" and m.get("status") in OK_LIST for ts, m in runs)
    if sent:
        return []
    return [
        item(
            seat,
            "keeper_missed",
            f"keeper:missed:{local.date().isoformat()}",
            due.astimezone(now.tzinfo),
            f"The mailbox keeper sent no morning list today (due from {hhmm(due)}).",
            "Read the seat's mailbox-keeper heartbeat rows; a run that never fired means the cron row or the broker.",
        )
    ]


def keeper_items(seat: str, rows: list[dict], now: datetime, config: dict) -> list[Item]:
    settings = keeper_settings(config)
    if settings is None:
        return []
    runs = _keeper_rows(rows, now)
    items: list[Item] = []
    if runs:
        ts, newest = runs[-1]
        status = str(newest.get("status") or "unknown")
        if newest.get("decision_basis") == FAILED:
            words = WORDS.get(status, f"ended {status}")
            items.append(
                item(
                    seat,
                    "keeper_failed",
                    f"keeper:{status}:{ts.date().isoformat()}",
                    ts,
                    f"The mailbox keeper {words} (run at {hhmm(ts)}).",
                    "Read the seat's mailbox-keeper heartbeat row for the reason; the next hourly run retries.",
                )
            )
        for ts, m in runs:
            if m.get("classify") in ("failed", "fence"):
                items.append(
                    item(
                        seat,
                        "keeper_classify",
                        f"keeper:classify:{m.get('classify')}:{ts.date().isoformat()}",
                        ts,
                        f"The mailbox keeper's reading stopped ({m.get('classify')}) at {hhmm(ts)}; "
                        "the threads it did not read are listed as could not read.",
                        "A fence: compare the run's cost_cents with cost_fence_cents. Failed: the model key or the model.",
                    )
                )
                break
    return items + _missed_list(seat, runs, now, settings)


# ---------------------------------------------------------------- machines


def machine_items(seat: str, machines: list[dict[str, Any]], now: datetime) -> list[Item]:
    started = [m for m in machines if str(m.get("state") or "") == "started"]
    if len(started) <= 1:
        return []
    created = [parse_ts(m.get("created_at")) for m in started]
    stamps = [c for c in created if c is not None]
    newest = max(stamps) if stamps else now
    if now - newest < MACHINE_GRACE:
        return []
    return [
        item(
            seat,
            "machines",
            f"machines:{len(started)}",
            newest,
            f"{len(started)} machines are started for this seat; it must run one (two run every schedule twice: "
            "the 2026-11-16 double morning list).",
            "List them (fly machines list) and destroy the extra one, keeping the one the newest release deployed.",
        )
    ]


def read_machines(seat: str, repo: Path) -> tuple[Optional[list[dict[str, Any]]], str]:
    """(machines, "") or (None, why it could not be checked here)."""
    if shutil.which("fly") is None:
        return None, "no fly CLI on this host"
    from bin.lib.fleet import ids
    from bin.lib.fleet.fly import Fly, FlyTokens
    from bin.lib.fleet.runner import CommandRunner, ToolingError

    try:
        return Fly(CommandRunner(), str(repo), FlyTokens.from_environ()).machines(ids.app_name(seat)), ""
    except ToolingError as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"


__all__ = ["keeper_items", "keeper_settings", "machine_items", "read_machines"]
