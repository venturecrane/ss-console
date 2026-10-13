"""Work-health items from the console's own records and the daily reconcilers.

The second half of ``work_health``: where that module reads one seat's ledger,
this one turns the console-side records (``fleet_status``, ``fleet_alert_state``,
``operator_shortfalls``, the Sentry rows on ``cost_anomaly_alerts``) and the two
daily reconcilers' graders into the same :class:`Item` shape, so their findings
reach team@ within the hour instead of living only in rolling GitHub issues
(#2593 silent runs, #2693 missed slots), which nobody read.

The reconcilers are IMPORTED, never re-implemented: ``reconcile-outcomes``
owns what "silent" means and ``reconcile-wakes`` owns what a missed slot is, so
the email and the daily issues can never disagree about a finding.

Pure: rows and a clock in, items out.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bin.lib.work_health import Config, Item, hhmm, item, parse_ts, span

_BIN = Path(__file__).resolve().parents[1]

#: Shortfall codes that only echo a job ending or a held reply; the job and
#: reply items already name that work, so listing them twice is noise.
_ECHO_CODE_SUFFIXES = ("_job_failed", "_job_held")
_ECHO_CODE_PREFIXES = ("reply_held:",)

_CLASS_WORDS = {
    "not_allowed": "was refused by a safety rule",
    "limit": "hit a size or count limit",
    "failed": "failed",
    "partial": "did only part of the work",
}

#: The connector whose seat-side poller calls it every few seconds; a stale
#: last success there means the mailbox is not being read, idle or not.
POLLED_CONNECTORS = {"msgraph_mail": 900}


def load_module(name: str, filename: str):
    """A dashed-filename sibling script, loaded once (reconcile-wakes does the same)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _BIN / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _words(tool: str) -> str:
    """A tool name a person can read: ``mcp_smokeball_list_matters`` ->
    ``smokeball list matters``."""
    text = tool[4:] if tool.startswith("mcp_") else tool
    return text.replace("_", " ").strip() or "a tool"


# ---------------------------------------------------------------------------
# reconcilers
# ---------------------------------------------------------------------------


def silent_run_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> list[Item]:
    """Scheduled and held runs that ended with no observable outcome
    (reconcile-outcomes' finding), opened inside the reconcile window. Inbound
    runs are left to the request items, which name the request."""
    outcomes = load_module("reconcile_outcomes", "reconcile-outcomes.py")
    contract = outcomes.load_contract()
    usable = [r for r in rows if r.get("ts")]
    since = now - timedelta(hours=cfg.reconcile_window_hours)
    out: list[Item] = []
    for ob in outcomes.analyze(contract, seat, usable, now=now):
        if not ob.is_finding or ob.trigger_kind == "inbound" or ob.opened_at < since:
            continue
        routine = f"{ob.skill_name} run" if ob.skill_name else "unnamed run (no skill recorded)"
        if ob.shape == outcomes.SHAPE_HELD_WITHOUT_NOTICE:
            what = f"The {routine} at {hhmm(ob.opened_at)} had its output held and nobody was told."
        else:
            what = f"The {routine} at {hhmm(ob.opened_at)} ended with no outcome: nothing delivered, refused, or escalated."
        out.append(
            item(
                seat,
                "silent_run",
                outcomes.finding_key(seat, ob),
                ob.opened_at,
                what,
                "Check whether the work it owed was done; a silent run looks the same as success from the firm's side.",
            )
        )
    return out


def missed_slot_items(seat: str, rows: list[dict], cfg: Config, now: datetime, boot: Optional[dict]) -> list[Item]:
    """Authored schedule slots that passed with neither wake row (reconcile-wakes)."""
    wakes = load_module("reconcile_wakes", "reconcile-wakes.py")
    outcomes = load_module("reconcile_outcomes", "reconcile-outcomes.py")
    since = now - timedelta(hours=cfg.reconcile_window_hours)
    report = wakes.reconcile_seat(seat, now=now, since=since, boot_info=boot, outcomes=outcomes, rows=rows)
    if report.held:
        return [
            item(
                seat,
                "unreadable",
                "schedule",
                now - timedelta(minutes=1),
                f"Could not check this seat's scheduled runs: {report.held}.",
                "Fix the read; until then a missed scheduled run would not show here.",
            )
        ]
    out = []
    for verdict in report.missing:
        slot = verdict.slot
        out.append(
            item(
                seat,
                "missed_slot",
                f"{slot.skill}|{slot.fires_at.isoformat()}",
                slot.fires_at,
                f"The scheduled {slot.skill} run due {hhmm(slot.fires_at)} never ran (no run and no skip recorded).",
                "Check the seat's scheduler; work the firm switched on did not happen.",
            )
        )
    return out


# ---------------------------------------------------------------------------
# console records
# ---------------------------------------------------------------------------


def heartbeat_items(seat: str, status: Optional[dict], cfg: Config, now: datetime) -> list[Item]:
    beat = parse_ts((status or {}).get("last_heartbeat_ts"))
    limit = timedelta(minutes=cfg.heartbeat_stale_minutes)
    if beat is not None and now - beat <= limit:
        return []
    onset = (beat + limit) if beat else now
    seen = f"last reported {hhmm(beat)}" if beat else "has never reported"
    return [
        item(
            seat,
            "heartbeat_stale",
            "heartbeat",
            onset,
            f"The seat {seen}; nothing below can be vouched for while it is silent.",
            "Check the Machine (fly status); the heartbeat-red page should already have fired.",
        )
    ]


def connector_items(seat: str, status: Optional[dict], now: datetime) -> list[Item]:
    """The seat's own per-connector counters (heartbeat ``connectors_json``)."""
    raw = (status or {}).get("connectors_json")
    try:
        servers = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        servers = {}
    out = []
    for server, entry in sorted((servers or {}).items()):
        if not isinstance(entry, dict):
            continue
        fails = entry.get("consecutive_failures")
        age = entry.get("last_ok_age_seconds")
        stale_after = POLLED_CONNECTORS.get(server)
        if isinstance(fails, int) and fails >= 2:
            what = f"The {_words(server)} connection has failed {fails} times in a row."
        elif stale_after and isinstance(age, (int, float)) and age > stale_after:
            what = f"The {_words(server)} connection has not succeeded for {span(timedelta(seconds=age))}; the mailbox is not being read."
        else:
            continue
        onset = now - timedelta(seconds=age) if isinstance(age, (int, float)) else now
        out.append(
            item(
                seat,
                "auth_failed",
                f"connector:{server}",
                onset,
                what,
                "Check the connection's credentials and the vendor's status; requests through it are failing.",
                page=f"ACTION: {seat} {_words(server)} connection is failing",
            )
        )
    return out


_CONDITION_WORDS = {
    "heartbeat_red": "the seat stopped reporting",
    "hard_stop": "the seat's spend breaker is at HARD STOP; it is doing no work",
    "scheduler_error": "the seat's scheduler cannot run scheduled work",
    "work_overdue": "a scheduled job is overdue",
    "connector_check_error": "the seat's connector self-check is broken",
    "edge_down": "smd.services itself is not answering",
}


def alert_items(seat: str, open_alerts: list[dict], now: datetime) -> list[Item]:
    """Conditions the pager already opened and nobody has closed."""
    out = []
    for row in open_alerts:
        condition = str(row.get("condition") or "")
        opened = parse_ts(row.get("opened_at")) or now
        head, _, tail = condition.partition(":")
        if head == "connector_down":
            what = f"The {_words(tail)} connection is down"
        elif head == "connector_token_expiring":
            what = f"The {_words(tail)} sign-in expires soon"
        else:
            what = _CONDITION_WORDS.get(condition, f"Open alert: {condition.replace('_', ' ')}")
            what = what[0].upper() + what[1:]
        out.append(
            item(
                seat,
                "connector_alert",
                f"alert:{condition}",
                opened,
                f"{what} (alert open since {hhmm(opened)}, {span(now - opened)}).",
                "Work the alert email's runbook; this line repeats until the alert closes.",
            )
        )
    return out


def _is_echo(code: str) -> bool:
    return code.endswith(_ECHO_CODE_SUFFIXES) or code.startswith(_ECHO_CODE_PREFIXES)


def shortfall_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> list[Item]:
    """Refused / limit / failed / partial tool results in the window, one item
    per (class, tool, code) with its count, so a retry loop is one line."""
    since = now - timedelta(hours=cfg.event_window_hours)
    groups: dict[tuple[str, str, str], list[datetime]] = {}
    for row in rows:
        ts = parse_ts(row.get("ts"))
        code = str(row.get("code") or "")
        if ts is None or ts < since or _is_echo(code):
            continue
        key = (str(row.get("class") or ""), str(row.get("tool") or ""), code)
        groups.setdefault(key, []).append(ts)
    out = []
    for (klass, tool, code), times in sorted(groups.items()):
        times.sort()
        count = f"{len(times)} times, " if len(times) > 1 else ""
        out.append(
            item(
                seat,
                "shortfall",
                f"shortfall:{klass}:{tool}:{code}:{times[0].isoformat()}",
                times[0],
                f"A {_words(tool)} call {_CLASS_WORDS.get(klass, klass)} ({code}; {count}last {hhmm(times[-1])}).",
                "If a person was waiting on it, check they got an answer; if it repeats, the tool or the rule needs fixing.",
            )
        )
    return out


def sentry_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> list[Item]:
    """New Operator errors Sentry reported for this seat (or the whole fleet)."""
    since = now - timedelta(hours=cfg.event_window_hours)
    out = []
    for row in rows:
        ts = parse_ts(row.get("detected_at"))
        if ts is None or ts < since:
            continue
        summary = str(row.get("summary") or "a new error").strip().splitlines()[0][:160]
        out.append(
            item(
                seat,
                "sentry",
                f"sentry:{row.get('driver')}:{row.get('alert_date')}",
                ts,
                f"New Operator error at {hhmm(ts)}: {summary}",
                "Triage it in Sentry (smd-operator): resolve, or fix the cause.",
            )
        )
    return out


def graph_probe_item(seat: str, mailbox: str, error: Optional[str], now: datetime) -> list[Item]:
    if error is None:
        return []
    return [
        item(
            seat,
            "auth_failed",
            "graph-probe",
            now - timedelta(minutes=1),
            f"SMD's hourly sign-in check of the {mailbox} mailbox failed ({error}).",
            "Check the mailbox's app credentials; if the seat's own connection is healthy, SMD's copy of the secret is stale.",
            page=f"ACTION: {seat} mailbox sign-in is dead",
        )
    ]
