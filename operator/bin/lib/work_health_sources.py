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
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bin.lib import cron_slots
from bin.lib.work_health import Config, Item, Page, card_id, hhmm, item, meta, parse_ts, span, stamped
from bin.lib.work_health_proof import job_holds

_BIN = Path(__file__).resolve().parents[1]

#: Shortfall codes that only echo a job ending, a held reply or a failed
#: delivery proof; the job, reply and proof items already name that work, so
#: listing them twice is noise. ``proof:<code>`` is the heartbeat's copy of a
#: DELIVERY_PROOF_FAILED row (overlay ``shared/heartbeat.py`` count_shortfalls),
#: which ``delivery_proof_items`` reads from the ledger itself, with its job:
#: one SMD-rejected reply was a proof line AND a "drafting job call failed"
#: line (triage, 2026-11-04). ``reply_could_not:`` is a reply that told the
#: person the Operator could not do it, which ``could_not_items`` reads from
#: the ledger itself and keeps listed (2026-11-19).
_ECHO_CODE_SUFFIXES = ("_job_failed", "_job_held")
_ECHO_CODE_PREFIXES = ("reply_held:", "proof:", "reply_could_not:")

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


#: What a page says when an agent looks first and the client may be next.
SIGN_IN_FOR_AGENT = (
    "Give this email to an agent to check it. If the client has to sign in again, the agent will say so "
    "and that email is yours to send."
)


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


#: Broker rows that close an outcome letter's request: written by the overlay
#: once the person who asked has been told (the contract: "written when the note
#: is away"), or by SMD through ``establish_lapse_notified`` when SMD closes it
#: by hand because she was answered elsewhere. Either way the sweeper stops
#: retrying and nothing is left owed. The out-of-turn sends the outbound scan
#: refuses carry no message id, so this row is the only join available.
_CLOSING_ROWS = frozenset({"RULE_LAPSED", "OPS_REQUEST_LAPSED"})
#: The sweeper retries every tick, so the row that ends the run lands within
#: seconds of the last refusal (2026-10-16: 13 s after the 5,246th).
_CLOSED_WITHIN = timedelta(seconds=60)


def _refusals_then_closed(usable: list[dict], attempts: list[datetime]) -> bool:
    """A run of refused out-of-turn sends that stopped because its request was
    closed: a closing row within a minute of the last refusal (so none came
    after it). On 2026-10-16 SMD closed the A&P lapse letter's request by hand
    (Christa had been answered on 10-14) and the email kept reading "whoever it
    was for has not been told"."""
    if not attempts:
        return False
    last = attempts[-1]
    for ts, r in stamped(usable):
        if str(r.get("action_type") or "") in _CLOSING_ROWS and last <= ts <= last + _CLOSED_WITHIN:
            return True
    return False


def silent_run_items(
    seat: str, rows: list[dict], cfg: Config, now: datetime, listed_jobs: frozenset[str] = frozenset()
) -> tuple[list[Item], int]:
    """Scheduled and held runs that ended with no observable outcome
    (reconcile-outcomes' finding), opened inside the reconcile window. Inbound
    runs are left to the request items, which name the request.

    ``listed_jobs`` are the jobs another line already names. A hold of a job's
    reply (its wake turn's, or SMD rejecting it in review) is that job's
    problem, said on the job's own line with its cause; a second "held and
    nobody was told" line about it pointed away from the cause (triage,
    2026-11-04)."""
    outcomes = load_module("reconcile_outcomes", "reconcile-outcomes.py")
    contract = outcomes.load_contract()
    usable = [r for r in rows if r.get("ts")]
    since = now - timedelta(hours=cfg.reconcile_window_hours)
    by_id = {str(r.get("id")): r for r in usable if r.get("id")}
    folded = [h for h in job_holds(usable) if h.job in listed_jobs]
    folded_ids = {h.row_id for h in folded}
    folded_at = {h.at for h in folded}
    groups: dict[tuple[str, str, str], list] = {}
    resolved = 0
    for ob in outcomes.analyze(contract, seat, usable, now=now):
        if not ob.is_finding or ob.trigger_kind == "inbound" or ob.opened_at < since:
            continue
        if str(ob.row_id) in folded_ids or ob.last_hold_at in folded_at:
            continue
        if _held_then_sent(usable, ob.row_id):
            resolved += 1
            continue
        held_by = str((by_id.get(str(ob.row_id)) or {}).get("action_type") or "")
        groups.setdefault((ob.shape, ob.skill_name or "", held_by), []).append(ob)
    # ONE ITEM PER PROBLEM, not per occurrence (Captain, 2026-10-16). The
    # reconciler opens a new finding for each half-hour of a repeating hold, so
    # one send refused every thirty seconds for a day read as 25 problems. The
    # group key carries no time, so the item stays "still open" for as long as
    # the problem repeats, and its age comes from the state file.
    out: list[Item] = []
    for (shape, skill, held_by), obs in groups.items():
        first = min(o.opened_at for o in obs)
        # The count a person means is attempts, not the reconciler's slices.
        attempts = [
            ts
            for ts, r in stamped(usable)
            if ts >= first and str(r.get("action_type") or "") == held_by and (r.get("skill_name") or "") == skill
        ]
        if held_by == "FABRICATION_FILTER_TRIGGERED" and not skill and _refusals_then_closed(usable, attempts):
            resolved += 1
            continue
        last = attempts[-1] if attempts else max(o.opened_at for o in obs)
        count = max(len(attempts), len(obs))
        routine = f"The {skill} run" if skill else "A run with no routine named"
        times = f"{count:,} times since {hhmm(first)}, last {hhmm(last)}" if count > 1 else f"at {hhmm(first)}"
        todo = "Check whether the work it owed was done; a silent run looks the same as success from the firm's side."
        if held_by == "FABRICATION_FILTER_TRIGGERED" and not skill:
            what = (
                f"A message the Operator tried to send on its own was blocked by the safety check, {times}. "
                "Whoever it was for has not been told."
            )
            todo = "Find what it was trying to send (the seat's agent.log names it) and tell that person by hand."
        elif shape == outcomes.SHAPE_HELD_WITHOUT_NOTICE:
            what = f"{routine} had its output held and nobody was told ({times})."
        else:
            what = f"{routine} ended with nothing delivered, refused, or escalated ({times})."
        out.append(item(seat, "silent_run", f"{shape}:{skill}:{held_by}", first, what, todo))
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


def schedule_adopted_at(schedule_changed_at: Optional[str], boot: Optional[dict]) -> Optional[datetime]:
    """Since when the Machine has run the provisioned schedule, or None when it
    has not adopted it yet.

    ``schedule_changed_at`` is when the SCHEDULE last changed
    (``customer_configs.schedule_changed_at``, migration 0130), never merely when
    the config was last published: ``synced_at`` moves on every publish, and on
    2026-10-19 #3332 (which only removed ``hermes_ref``) switched A&P's grading
    off until its next rebuild, so a missed run would have gone unreported.

    A merge publishes customer.yaml at once (deploy.yml), but the Machine's cron
    store changes only when it next boots. On 2026-10-13 A&P's new hourly row
    was published 20:35Z and adopted at the 21:12Z rebuild, and a grid that
    ignored the gap reported 23 "missed" runs from a schedule the seat had never
    held. Slots before adoption are not owed."""
    changed = parse_ts(schedule_changed_at)
    window = cron_slots.boot_window((boot or {}).get("last_heartbeat_ts"), (boot or {}).get("process_uptime_seconds"))
    booted = window[0] + timedelta(minutes=45) if window else None
    if changed is None:
        return booted
    if booted is None or booted < changed:
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
                page=Page(
                    f"[SMD Ops] {seat}: the {_words(server)} connection is failing",
                    f"The connection to {_words(server)} is failing, so work that needs it may have stopped.",
                    SIGN_IN_FOR_AGENT,
                ),
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


_CONNECTOR_KEY = "connector:"
_CONNECTOR_DOWN_KEY = "alert:connector_down:"


def _key_tail(it: Item) -> str:
    """The item's own key, without the ``seat|kind|`` prefix ``item`` adds."""
    return it.key.split("|", 2)[2]


def fold_connector_lines(items: list[Item]) -> list[Item]:
    """One failing connection, one line (triage, 2026-11-12: one mail blip was
    three NEW lines). The seat's failure count (``connector_items``) and the
    pager's ``connector_down`` alert for the same server are one problem: the
    alert line is folded into the count line, which keeps its page, and names
    since when the alert has been open. An alert with no count line stands."""
    counted = {
        _key_tail(it)[len(_CONNECTOR_KEY) :]: it
        for it in items
        if it.kind == "auth_failed" and _key_tail(it).startswith(_CONNECTOR_KEY)
    }
    out: list[Item] = []
    for it in items:
        tail = _key_tail(it)
        server = tail[len(_CONNECTOR_DOWN_KEY) :] if tail.startswith(_CONNECTOR_DOWN_KEY) else ""
        line = counted.get(server) if it.kind == "connector_alert" and server else None
        if line is None:
            out.append(it)
            continue
        line.what = f"{line.what} Its alert has been open since {hhmm(it.onset)}."
        line.onset = min(line.onset, it.onset)
    return out


def seat_mail_connection_ok(status: Optional[dict]) -> Optional[bool]:
    """The seat's own msgraph connections by its connector ledger (heartbeat
    ``connectors_json``): True when every one reports 0 failures in a row,
    False when any is failing, None when the seat reports none."""
    raw = (status or {}).get("connectors_json")
    try:
        servers = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        return None
    counts = [
        entry.get("consecutive_failures")
        for server, entry in (servers if isinstance(servers, dict) else {}).items()
        if str(server).startswith("msgraph") and isinstance(entry, dict)
    ]
    counts = [c for c in counts if isinstance(c, int)]
    if not counts:
        return None
    return all(c == 0 for c in counts)


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


def _is_smd_address(value: object) -> bool:
    """``shared.heartbeat._is_smd_address``, the one test of SMD's own addresses
    (its domains live in ``shared.customer_config``), read from the overlay in
    this repo when the overlay is not installed in this interpreter."""
    overlay = str(Path(__file__).resolve().parents[2] / "hermes-smd-overlay")
    if overlay not in sys.path:
        sys.path.append(overlay)  # appended: an installed overlay still wins
    from shared.heartbeat import _is_smd_address as test

    return test(value)


def could_not_items(
    seat: str, rows: list[dict], cfg: Config, now: datetime, closed: Optional[set] = None, owed: frozenset = frozenset()
) -> list[Item]:
    """A firm person's request whose reply told them the Operator could not do
    it (REPLY_SENT stamped ``could_not``, overlay ``shared/reply_could_not``):
    SMD owes the answer, so it stays listed until SMD closes its card in
    ``work-health-closed.yaml`` or it passes the request horizon. A LATER REPLY
    DOES NOT CLEAR IT (2026-11-19): the could-not reply is itself a reply, and
    the seat's next answer to anyone says nothing about this person. Work SMD
    owes after an our-side stop is NOT this line: the broker records it, and
    ``work_health_owed.smd_owed_items`` lists it (owner's ruling, 2026-11-19).
    Since 2026-11-25 the reply plugin records such a reply as an owed item
    (``reply_not_fulfilled``); a request in ``owed`` (the request emails of
    the seat's open owed items) is listed there, once, and not here. This line
    stays the fallback for a reply whose owed record did not land."""
    first_seen: dict[str, datetime] = {}
    declined: dict[str, tuple[datetime, str]] = {}
    for ts, row in stamped(rows):
        md = meta(row)
        vid = md.get("vendor_message_id") if row.get("action_type") == "INBOUND_RECEIVED" else None
        if isinstance(vid, str) and vid and md.get("trust_class") == "internal":
            first_seen.setdefault(vid, ts)
        to = md.get("in_reply_to")
        code = md.get("could_not")
        if row.get("action_type") == "REPLY_SENT" and isinstance(to, str) and isinstance(code, str) and code:
            # A reply to SMD's own address is SMD writing in, not a firm
            # person owed an answer (the heartbeat's own test of the address).
            if not _is_smd_address(md.get("recipient")):
                declined.setdefault(to, (ts, code))
    horizon = now - timedelta(hours=cfg.request_horizon_hours)
    out: list[Item] = []
    for vid, (said_at, code) in declined.items():
        received = first_seen.get(vid)
        if received is None or received < horizon or vid in owed:
            continue
        card = card_id(vid)
        if closed and (seat, card) in closed:
            continue
        out.append(
            item(
                seat,
                "could_not",
                card,
                said_at,
                f"A request received {hhmm(received)} (card {card}) was told at {hhmm(said_at)} that it could not "
                f"be done ({code}), and SMD owes the answer.",
                f"Answer the requester in that thread, then close card {card} in "
                "operator/contracts/work-health-closed.yaml.",
            )
        )
    return out


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
        # Identity is the class and the tool, never a time: with the earliest
        # hit in the key, a late row 10 ms older than the rest (and every hit
        # ageing past 24 hours) re-announced the same line as NEW. It is NEW
        # again only when a hit newer than the newest reported lands (``latest``;
        # triage, 2026-11-06).
        out.append(
            item(
                seat,
                "shortfall",
                f"shortfall:{klass}:{tool}",
                hits[0][0],
                f"A {_words(tool)} call {_CLASS_WORDS.get(klass, klass)} ({codes}; {count}last {hhmm(hits[-1][0])}) "
                "and nothing was delivered after it.",
                "Check whether the person or routine it served got what it needed; if it repeats, fix the tool or the rule.",
                latest=hits[-1][0],
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


_HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b")


def graph_probe_item(
    seat: str, mailbox: str, error: Optional[str], now: datetime, seat_connection_ok: Optional[bool] = None
) -> list[Item]:
    """SMD's hourly sign-in check of the seat's mailbox, when it failed.

    A server error (5xx, already retried by ``transient_http``) while the
    seat's own connector ledger shows 0 failures in a row is Microsoft having
    a bad minute, not a stale SMD secret: one line saying so, never the page
    (triage, 2026-11-12). A refusal (401, 403) or a failing seat connection is
    the sign-in problem, and pages."""
    if error is None:
        return []
    status = _HTTP_STATUS.search(error)
    if status and status.group(1).startswith("5") and seat_connection_ok is True:
        return [
            item(
                seat,
                "auth_failed",
                "graph-probe",
                now - timedelta(minutes=1),
                f"Microsoft answered SMD's hourly sign-in check of the {mailbox} mailbox with a server error "
                f"(HTTP {status.group(1)}) after a retry; the seat's own mailbox connection is healthy (0 failures "
                "in a row), so this is the vendor, not a stale secret.",
                "Nothing to do unless the next hour's check fails the same way.",
            )
        ]
    return [
        item(
            seat,
            "auth_failed",
            "graph-probe",
            now - timedelta(minutes=1),
            f"SMD's hourly sign-in check of the {mailbox} mailbox failed ({error}).",
            "Check the mailbox's app credentials; if the seat's own connection is healthy, SMD's copy of the secret is stale.",
            page=Page(
                f"[SMD Ops] {seat}: the mailbox sign-in check is failing",
                "SMD's hourly sign-in check of this seat's mailbox failed. Either the mailbox connection is broken "
                "or SMD's own copy of its credentials is out of date.",
                SIGN_IN_FOR_AGENT,
            ),
        )
    ]
