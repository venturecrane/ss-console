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

from bin.lib import cron_slots
from bin.lib.work_health import Config, Item, hhmm, item, meta, parse_ts, reason_token, span, stamped

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

#: The sign-in check's skill (work_health_auth owns its lines).
AUTH_PROBE_SKILL = "connector-auth-check"

#: How long after its instant a slot's wake row may land (reconcile-wakes.py).
SLOT_TOLERANCE_S = 1800


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


def _held_then_sent(rows: list[dict], row_id: Optional[str]) -> bool:
    """A held output whose message later went out anyway (released, rewritten,
    or the duplicate of a reply already sent): history, not open work."""
    held = next((r for r in rows if r.get("id") == row_id), None) if row_id else None
    if held is None or held.get("action_type") != "REPLY_HELD":
        return False
    message_id = meta(held).get("message_id")
    held_at = parse_ts(held.get("ts"))
    if not message_id or held_at is None:
        return False
    return any(r.get("action_type") == "REPLY_SENT" and meta(r).get("in_reply_to") == message_id for r in rows)


def silent_run_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> tuple[list[Item], int]:
    """Scheduled and held runs that ended with no observable outcome
    (reconcile-outcomes' finding), opened inside the reconcile window. Inbound
    runs are left to the request items, which name the request."""
    outcomes = load_module("reconcile_outcomes", "reconcile-outcomes.py")
    contract = outcomes.load_contract()
    usable = [r for r in rows if r.get("ts")]
    since = now - timedelta(hours=cfg.reconcile_window_hours)
    out: list[Item] = []
    resolved = 0
    for ob in outcomes.analyze(contract, seat, usable, now=now):
        if not ob.is_finding or ob.trigger_kind == "inbound" or ob.opened_at < since:
            continue
        if _held_then_sent(usable, ob.row_id):
            resolved += 1
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
    return out, resolved


def deployed_cron(personas_json: Optional[str]) -> Optional[set[tuple[str, str]]]:
    """(skill, schedule) pairs the seat was PROVISIONED with: the
    ``customer_configs.personas_json`` projection written at provision. None
    when it cannot be read. This, not git, is what the Machine runs: on
    2026-10-13 a git-only grid expected an hourly check pilot-smokeball had
    never been rebuilt to run, and reported 24 "never ran" lines that were
    false (the live store ran its daily row on time)."""
    if not isinstance(personas_json, str) or not personas_json:
        return None
    try:
        personas = json.loads(personas_json)
    except ValueError:
        return None
    out: set[tuple[str, str]] = set()
    for persona in personas if isinstance(personas, list) else [personas]:
        for row in (persona or {}).get("cron") or [] if isinstance(persona, dict) else []:
            if isinstance(row, dict) and isinstance(row.get("skill"), str) and isinstance(row.get("schedule"), str):
                out.add((row["skill"], row["schedule"]))
    return out


def schedule_drift(git_config: dict, deployed: set[tuple[str, str]]) -> dict[str, tuple[list[str], list[str]]]:
    """skill -> (authored schedules, provisioned schedules), where they differ."""
    authored: dict[str, list[str]] = {}
    for row in cron_slots.authored_cron_rows(git_config):
        authored.setdefault(row.skill, []).append(row.schedule)
    seat: dict[str, list[str]] = {}
    for skill, schedule in deployed:
        seat.setdefault(skill, []).append(schedule)
    return {
        skill: (sorted(authored.get(skill, [])), sorted(seat.get(skill, [])))
        for skill in set(authored) | set(seat)
        if sorted(authored.get(skill, [])) != sorted(seat.get(skill, []))
    }


def schedule_adopted_at(synced_at: Optional[str], boot: Optional[dict]) -> Optional[datetime]:
    """Since when the Machine has run the provisioned schedule, or None when it
    has not adopted it yet.

    A merge publishes customer.yaml at once (deploy.yml), but the Machine's cron
    store changes only when it next boots. On 2026-10-13 A&P's new hourly row
    was published 20:35Z and adopted at the 21:12Z rebuild, and a grid that
    ignored the gap reported 23 "missed" runs from a schedule the seat had never
    held. Slots before adoption are not owed."""
    synced = parse_ts(synced_at)
    window = cron_slots.boot_window((boot or {}).get("last_heartbeat_ts"), (boot or {}).get("process_uptime_seconds"))
    booted = window[0] + timedelta(minutes=45) if window else None
    if synced is None:
        return booted
    if booted is None or booted < synced:
        return None
    return booted


def _missing_by_skill(
    graded: list, git_config: dict, rows: list[dict], start: datetime, now: datetime, boot: Optional[dict]
) -> dict[str, list[datetime]]:
    """Owed slots with neither wake row, grouped by routine."""
    slots = cron_slots.expand_slots(graded, cron_slots.seat_timezone(git_config), start, now)
    gradable = [x for x in slots if x.fires_at + timedelta(seconds=SLOT_TOLERANCE_S) <= now]
    verdicts = cron_slots.match_slots(gradable, rows, tolerance_s=SLOT_TOLERANCE_S)
    if boot:
        window = cron_slots.boot_window(boot.get("last_heartbeat_ts"), boot.get("process_uptime_seconds"))
        cron_slots.apply_boot_suppression(verdicts, window)
    by_skill: dict[str, list[datetime]] = {}
    for v in verdicts:
        if v.is_missing:
            by_skill.setdefault(v.slot.skill, []).append(v.slot.fires_at)
    return by_skill


def _missed_lines(seat: str, by_skill: dict[str, list[datetime]]) -> list[Item]:
    out = []
    for skill, times in sorted(by_skill.items()):
        times.sort()
        count = f"{len(times)} scheduled runs missed" if len(times) > 1 else "1 scheduled run missed"
        out.append(
            item(
                seat,
                "missed_slot",
                f"{skill}|{times[0].isoformat()}",
                times[0],
                f"The scheduled {skill} has not run since {hhmm(times[0])}: {count}, last due {hhmm(times[-1])} "
                "(no run and no skip recorded).",
                "Check the seat's scheduler; work the firm switched on did not happen.",
            )
        )
    return out


def _drift_lines(seat: str, drift: dict[str, tuple[list[str], list[str]]], now: datetime) -> list[Item]:
    out = []
    for skill, (authored, provisioned) in sorted(drift.items()):
        if skill == AUTH_PROBE_SKILL:
            continue  # the sign-in check's own "not scheduled" ACTION line already says this
        out.append(
            item(
                seat,
                "schedule_drift",
                f"{skill}|{authored}|{provisioned}",
                now,
                f"Schedule drift for {skill}: authored {', '.join(authored) or 'none'}, the seat runs "
                f"{', '.join(provisioned) or 'none'}. Its runs are not graded until the two agree.",
                "Rebuild the seat to adopt the authored schedule, or revert the authoring.",
            )
        )
    return out


def missed_slot_items(
    seat: str,
    rows: list[dict],
    cfg: Config,
    now: datetime,
    boot: Optional[dict],
    git_config: dict,
    deployed: Optional[set[tuple[str, str]]],
    adopted_at: Optional[datetime] = None,
    *,
    adoption_known: bool = True,
) -> list[Item]:
    """Scheduled runs that did not happen, ONE line per skill (never one per
    slot), graded only where git and the seat's provisioned schedule AGREE and
    only after the Machine booted into it. Disagreement is one drift line."""
    if deployed is None:
        return [
            item(
                seat,
                "unreadable",
                "schedule",
                now,
                "Could not read which schedule this seat was provisioned with; missed scheduled runs are unchecked.",
                "Check the customer_configs row for this seat.",
            )
        ]
    if adoption_known and adopted_at is None:
        # Published but not yet booted into: nothing on the Machine to grade.
        return [
            item(
                seat,
                "schedule_drift",
                "not-adopted",
                now,
                "This seat's published schedule is newer than its running Machine (it has not been rebuilt since "
                "the config changed); scheduled runs are not graded until it is.",
                "Rebuild the seat so it runs what is published.",
            )
        ]
    drift = schedule_drift(git_config, deployed)
    graded = [
        r
        for r in cron_slots.authored_cron_rows(git_config)
        if r.wake_policy == "pre_run_decides" and r.skill not in drift and (r.skill, r.schedule) in deployed
    ]
    start = now - timedelta(hours=cfg.reconcile_window_hours)
    if adopted_at is not None:
        start = max(start, adopted_at)
    by_skill = _missing_by_skill(graded, git_config, rows, start, now, boot)
    return _missed_lines(seat, by_skill) + _drift_lines(seat, drift, now)


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


def answered_after(ledger: Optional[list[dict]], ts: datetime) -> bool:
    """Did the seat deliver anything after ``ts`` (a reply sent, a job
    delivered)? A refusal or failure followed by delivered work is history,
    not open work (review 2026-10-13: the email lists only OPEN work)."""
    for row in ledger or []:
        when = parse_ts(row.get("ts"))
        action = str(row.get("action_type") or "")
        if when and when > ts and (action == "REPLY_SENT" or action.endswith("_JOB_DELIVERED")):
            return True
    return False


def shortfall_items(
    seat: str, rows: list[dict], cfg: Config, now: datetime, ledger: Optional[list[dict]] = None
) -> tuple[list[Item], int]:
    """Refused / limit / failed / partial tool results in the window, ONE line
    per (class, tool) with its count and reasons, and only while nothing was
    delivered after the last one. Returns (items, how many were resolved)."""
    since = now - timedelta(hours=cfg.event_window_hours)
    groups: dict[tuple[str, str], list[tuple[datetime, str]]] = {}
    for row in rows:
        ts = parse_ts(row.get("ts"))
        code = str(row.get("code") or "")
        if ts is None or ts < since or _is_echo(code):
            continue
        groups.setdefault((str(row.get("class") or ""), str(row.get("tool") or "")), []).append((ts, code))
    out, resolved = [], 0
    for (klass, tool), hits in sorted(groups.items()):
        hits.sort()
        if ledger is not None and answered_after(ledger, hits[-1][0]):
            resolved += len(hits)
            continue
        codes = ", ".join(sorted({c for _t, c in hits}))
        count = f"{len(hits)} times, " if len(hits) > 1 else ""
        out.append(
            item(
                seat,
                "shortfall",
                f"shortfall:{klass}:{tool}:{hits[0][0].isoformat()}",
                hits[0][0],
                f"A {_words(tool)} call {_CLASS_WORDS.get(klass, klass)} ({codes}; {count}last {hhmm(hits[-1][0])}) "
                "and nothing was delivered after it.",
                "Check whether the person or routine it served got what it needed; if it repeats, fix the tool or the rule.",
            )
        )
    return out, resolved


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


# ---------------------------------------------------------------------------
# delivery proof (PR #3152)
# ---------------------------------------------------------------------------

_PROOF_WORDS = {
    "proof_missing": "no proof of delivery was recorded",
    "readback_unproven": "the delivered files could not be read back",
    "readback_mismatch": "the files on the matter differ from what was built",
    "review_missing": "the pre-delivery review never ran",
    "review_held": "the pre-delivery review held it",
    "dry_run_reported": "a dry run was reported as delivered",
    "wake_lost": "the delivery wake was lost",
    "reply_unproven": "the requester's reply is not in Sent Items",
    "reply_not_in_sent_items": "the requester's reply is not in Sent Items",
    "gate_held": "a gate or our own seed data held it; the firm was told SMD is finishing it",
    "update_held": "the update into the delivered chronology could not go on",
    "upload_held": "filing or retiring the document on the matter did not finish",
    "stage_refused": "a stage refused its own input",
    "limit_held": "a cost cap, budget or page allowance held it",
    "identity_held": "a record in the file names another person; the firm was asked",
}


def delivery_proof_items(seat: str, rows: list[dict], cfg: Config, now: datetime) -> list[Item]:
    """A DELIVERY_PROOF_FAILED row (the seat refused to call a job delivered,
    or its requester reply never reached Sent Items) is open work until a
    DELIVERY_PROVEN row for the same job lands after it."""
    proven: dict[str, datetime] = {}
    failures: dict[str, tuple[datetime, dict]] = {}
    for ts, row in stamped(rows):
        md = meta(row)
        job_id = str(md.get("job_id") or "")
        if not job_id:
            continue
        if row.get("action_type") == "DELIVERY_PROVEN":
            proven[job_id] = ts
        elif row.get("action_type") == "DELIVERY_PROOF_FAILED":
            failures[job_id] = (ts, md)
    out: list[Item] = []
    for job_id, (ts, md) in failures.items():
        if (job_id in proven and proven[job_id] > ts) or now - ts > timedelta(hours=cfg.ended_job_hours):
            continue
        code = reason_token(md.get("code")) or "unknown"
        lane = cfg.lanes[md["lane"]].label if md.get("lane") in cfg.lanes else str(md.get("lane") or "a")
        out.append(
            item(
                seat,
                "job_failed",
                f"proof:{job_id}:{code}",
                ts,
                f"The {lane} job {job_id[-8:]} is NOT proven delivered: {_PROOF_WORDS.get(code, code)} ({code}, {hhmm(ts)}).",
                "Check what the requester actually received before anyone says it was delivered.",
            )
        )
    return out
