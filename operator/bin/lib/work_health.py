"""The state of the WORK on one seat, read from its audit ledger (work-health.py).

WHY THIS EXISTS (Captain, 2026-10-13). Machine health pages within minutes,
but nothing watched the work: a job stuck in ``running`` read as work in
progress forever; the seat's own NO REPLY card switches off the moment any job
row exists (overlay ``shared/request_cards.py``) and had never fired in
production; the daily reconcilers' findings lived only in GitHub issues; and an
idle dead Smokeball token was silent for 42 hours. This module turns one seat's
ledger rows into ITEMS, each one a piece of work someone must look at.

CONSOLE-SIDE, like the reconcilers it folds in: rows come through the ADR 0043
runtime-read seam, because a seat cannot be the auditor of its own silence.

WHAT AN ITEM CARRIES. Only what team@ alert mail already carries: the lane, a
matter number, the leading token of a job's reason (``review_held``, never the
sentence after it, which can name a document), times, and the request's card id
(the first 12 hex of sha256 of its vendor message id: the same prefix as the
``Card:`` line of that request's team@ card). Never a sender, a subject, or a
body; the ledger rows this reads do not hold those anyway.

Pure: every function here takes rows and a clock and returns items. All I/O
(seam, D1, Graph, mail) lives in ``work-health.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "work-health.yaml"

#: The audit action_type prefix of each job lane, as the broker writes it
#: (``operator/workspace_broker/<lane>_ledger.py`` AUDIT_TYPE).
LANE_PREFIXES = {
    "DEMAND": "demand",
    "DRAFTING": "drafting",
    "LITIGATION": "litigation",
    "NEGOTIATION": "negotiation",
    "MEDCHRON": "medchron",
}
IN_FLIGHT = {"SUBMITTED": "queued", "QUEUED": "queued", "RUNNING": "running"}
ENDED_BAD = {"HELD": "held", "FAILED": "failed"}
_JOB_TYPE = re.compile(r"^(DEMAND|DRAFTING|LITIGATION|NEGOTIATION|MEDCHRON)_JOB_([A-Z_]+)$")
_REASON_HEAD = re.compile(r"^[a-z0-9_.-]+")


@dataclass(frozen=True)
class Lane:
    name: str
    label: str
    queue_minutes: int
    run_minutes: int


@dataclass(frozen=True)
class Config:
    no_reply_minutes: int
    request_horizon_hours: int
    ledger_lookback_hours: int
    ended_job_hours: int
    reconcile_window_hours: int
    event_window_hours: int
    heartbeat_stale_minutes: int
    missed_run_after_minutes: int
    auth_probe_stale_minutes: int
    all_clear_hour_utc: int
    lanes: dict[str, Lane]
    no_reply_minutes_by_seat: dict[str, int]

    def no_reply_for(self, seat: str) -> int:
        return self.no_reply_minutes_by_seat.get(seat, self.no_reply_minutes)


def load_config(path: Path = CONTRACT_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("lanes"), dict):
        raise ValueError(f"{path}: not a work-health contract")
    lanes = {}
    for name, spec in raw["lanes"].items():
        lanes[name] = Lane(name, str(spec["label"]), int(spec["queue_minutes"]), int(spec["run_minutes"]))
    if set(lanes) != set(LANE_PREFIXES.values()):
        raise ValueError(f"{path}: lanes must be exactly {sorted(LANE_PREFIXES.values())}")
    keys = [k for k in Config.__dataclass_fields__ if k not in ("lanes", "no_reply_minutes_by_seat")]
    by_seat = {str(k): int(v) for k, v in (raw.get("no_reply_minutes_by_seat") or {}).items()}
    return Config(**{k: int(raw[k]) for k in keys}, lanes=lanes, no_reply_minutes_by_seat=by_seat)


@dataclass
class Item:
    """One piece of work someone must look at. ``key`` is stable across runs
    (it is how "new" and "still open" are told apart); ``onset`` is when the
    problem began, which is what makes the dedupe stateless."""

    seat: str
    kind: str
    key: str
    onset: datetime
    what: str
    todo: str
    rank: int = 50
    #: Set on an item that must PAGE (its own ACTION email every run while it
    #: stands), not only sit in the digest: a dead sign-in stops all work.
    page: Optional[str] = None

    def age_text(self, now: datetime) -> str:
        return span(now - self.onset)


# Ordering in the email: the work a firm person is waiting on first.
RANK = {
    "monitor_gap": -1,
    "unreadable": 0,
    "no_reply": 10,
    "reply_held": 12,
    "job_stuck": 20,
    "job_held": 22,
    "job_failed": 24,
    "auth_failed": 30,
    "connector_alert": 31,
    "heartbeat_stale": 32,
    "auth_unchecked": 34,
    "missed_slot": 40,
    "schedule_drift": 44,
    "silent_run": 42,
    "shortfall": 50,
    "sentry": 60,
}


def item(seat: str, kind: str, key: str, onset: datetime, what: str, todo: str, page: Optional[str] = None) -> Item:
    return Item(seat, kind, f"{seat}|{kind}|{key}", onset, what, todo, RANK.get(kind, 50), page)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def meta(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def reason_token(value: Any) -> Optional[str]:
    """The leading word of a reason, never the free text after it."""
    if not isinstance(value, str):
        return None
    found = _REASON_HEAD.match(value.strip().lower())
    return found.group(0)[:64] if found else None


def card_id(vendor_message_id: str) -> str:
    """The request's card id: the prefix of its team@ request card's Card line."""
    return hashlib.sha256(vendor_message_id.encode("utf-8")).hexdigest()[:12]


def span(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h" + (f"{minutes % 60:02d}m" if hours < 6 and minutes % 60 else "")
    return f"{hours // 24}d"


def hhmm(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%m-%d %H:%MZ")


def stamped(rows: list[dict]) -> list[tuple[datetime, dict]]:
    out = []
    for row in rows:
        ts = parse_ts(row.get("ts"))
        if ts is not None:
            out.append((ts, row))
    out.sort(key=lambda pair: (pair[0], str(pair[1].get("id") or "")))
    return out


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


@dataclass
class Job:
    lane: str
    job_id: str
    events: list[tuple[datetime, str, Optional[str]]] = field(default_factory=list)
    matter: Optional[str] = None
    scheduled: bool = False

    @property
    def submitted_at(self) -> datetime:
        return self.events[0][0]

    @property
    def last(self) -> tuple[datetime, str, Optional[str]]:
        return self.events[-1]


def jobs_from_rows(rows: list[dict]) -> list[Job]:
    """Every job the window shows, keyed (lane, job_id), events in ledger order.
    A resume note (``*_JOB_RESUME_REQUESTED``) is not a state and is skipped."""
    jobs: dict[tuple[str, str], Job] = {}
    for ts, row in stamped(rows):
        match = _JOB_TYPE.match(str(row.get("action_type") or ""))
        if not match:
            continue
        state = match.group(2)
        if state not in IN_FLIGHT and state not in ENDED_BAD and state != "DELIVERED":
            continue
        md = meta(row)
        job_id = md.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            continue
        lane = LANE_PREFIXES[match.group(1)]
        job = jobs.setdefault((lane, job_id), Job(lane=lane, job_id=job_id))
        job.events.append((ts, state, reason_token(md.get("reason"))))
        if isinstance(md.get("matter_number"), str) and md["matter_number"]:
            job.matter = md["matter_number"][:40]
        if md.get("trigger") == "scheduled" or str(md.get("request_ref") or "").startswith("scheduled:"):
            job.scheduled = True
    return list(jobs.values())


def _job_name(job: Job, cfg: Config) -> str:
    label = cfg.lanes[job.lane].label
    where = f" on matter {job.matter}" if job.matter else ""
    origin = "scheduled " if job.scheduled else ""
    return f"{origin}{label} job{where} (started {hhmm(job.submitted_at)}, job {job.job_id[-8:]})"


def _superseded(job: Job, jobs: list[Job]) -> bool:
    """A later job in the same lane on the same matter delivered: the bad
    ending was answered. Scheduled jobs carry no matter: any later delivered
    scheduled run of the lane answers them."""
    for other in jobs:
        if other is job or other.lane != job.lane or other.last[1] != "DELIVERED":
            continue
        if other.submitted_at <= job.submitted_at:
            continue
        if (job.matter and other.matter == job.matter) or (job.scheduled and other.scheduled):
            return True
    return False


CLOSED_PATH = CONTRACT_PATH.parent / "work-health-closed.yaml"


def load_closed(path: Path = CLOSED_PATH) -> set[tuple[str, str]]:
    """(seat, job id) pairs SMD closed with a written reason (see the file).
    An entry without a reason is refused: the reason is the record."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    out: set[tuple[str, str]] = set()
    for entry in (raw or {}).get("closed") or []:
        if not (
            isinstance(entry, dict)
            and entry.get("seat")
            and entry.get("job")
            and str(entry.get("reason") or "").strip()
        ):
            raise ValueError(f"{path}: every closed entry needs seat, job and a reason: {entry!r}")
        out.add((str(entry["seat"]), str(entry["job"])))
    return out


def _lane_hold(job: Job, holds: Optional[list[dict]]) -> Optional[str]:
    """The lane key a queued job is held on by the lane gate (PR #3148), or None.

    Medchron writes the hold as a second SUBMITTED row with reason
    "lane_unproven: <key>". The other lanes record it as a same-state ledger
    note, which writes NO audit row, so their signal is the seat's
    ``lane_unproven`` request card (D1 operator_request_cards, job_lane set)
    for the same lane at the job's submit time."""
    for _t, _s, reason in reversed(job.events):
        if reason and reason.startswith("lane_unproven"):
            return "unproven lane"
    for card in holds or []:
        at = parse_ts(card.get("event_at"))
        if card.get("job_lane") != job.lane or at is None:
            continue
        if job.submitted_at - timedelta(minutes=5) <= at <= job.submitted_at + timedelta(minutes=30):
            return str(card.get("lane_key") or "unproven lane")
    return None


def job_items(
    seat: str,
    rows: list[dict],
    cfg: Config,
    now: datetime,
    closed: Optional[set[tuple[str, str]]] = None,
    holds: Optional[list[dict]] = None,
) -> list[Item]:
    jobs = jobs_from_rows(rows)
    out: list[Item] = []
    for job in jobs:
        if closed and (seat, job.job_id) in closed:
            continue
        ts, state, reason = job.last
        lane = cfg.lanes[job.lane]
        name = _job_name(job, cfg)
        lane_key = _lane_hold(job, holds) if state in IN_FLIGHT else None
        if lane_key:
            # Held by the lane gate (PR #3148) until SMD's proof run: waiting,
            # not running and not stuck.
            out.append(
                item(
                    seat,
                    "job_held",
                    f"{job.lane}:{job.job_id}:lane_unproven",
                    ts,
                    f"The {name} is waiting on SMD's proof run: {lane_key} (held {span(now - ts)}); "
                    "nothing runs until the lane is proven.",
                    f"Run the lane's proof: operator/bin/lane-proof.sh {seat} status.",
                )
            )
            continue
        if state in IN_FLIGHT:
            phase = IN_FLIGHT[state]
            limit = lane.queue_minutes if phase == "queued" else lane.run_minutes
            if now - ts < timedelta(minutes=limit):
                continue
            out.append(
                item(
                    seat,
                    "job_stuck",
                    f"{job.lane}:{job.job_id}:{state}",
                    ts + timedelta(minutes=limit),
                    f"The {name} has been {phase} for {span(now - ts)}; it should take under {span(timedelta(minutes=limit))}.",
                    "Check the job on the seat: a hung runner looks like work in progress forever.",
                )
            )
        elif state in ENDED_BAD:
            if now - ts > timedelta(hours=cfg.ended_job_hours) or _superseded(job, jobs):
                continue
            why = f" ({reason})" if reason else ""
            kind = "job_held" if state == "HELD" else "job_failed"
            out.append(
                item(
                    seat,
                    kind,
                    f"{job.lane}:{job.job_id}:{state}:{len(job.events)}",
                    ts,
                    f"The {name} {ENDED_BAD[state]}{why} at {hhmm(ts)}.",
                    "Decide: rerun, release, or tell the requester why; nothing reaches them until someone does.",
                )
            )
    return out


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


def request_items(
    seat: str, rows: list[dict], cfg: Config, now: datetime, closed: Optional[set[tuple[str, str]]] = None
) -> list[Item]:
    """A firm person's request (INBOUND_RECEIVED, trust_class internal) with no
    REPLY_SENT threaded to it. Unlike the seat's own NO REPLY card, a job row
    does NOT excuse it: the acknowledgement is owed whether or not a job runs."""
    first_seen: dict[str, datetime] = {}
    replied: set[str] = set()
    held: dict[str, tuple[datetime, Optional[str]]] = {}
    for ts, row in stamped(rows):
        action = row.get("action_type")
        md = meta(row)
        if action == "INBOUND_RECEIVED" and md.get("trust_class") == "internal":
            vid = md.get("vendor_message_id")
            if isinstance(vid, str) and vid and vid not in first_seen:
                first_seen[vid] = ts
        elif action == "REPLY_SENT" and isinstance(md.get("in_reply_to"), str):
            replied.add(md["in_reply_to"])
        elif action == "REPLY_HELD" and isinstance(md.get("message_id"), str):
            if md.get("held_for_release") in (1, True):
                continue
            held[md["message_id"]] = (ts, reason_token(md.get("reason")))
    out: list[Item] = []
    horizon = now - timedelta(hours=cfg.request_horizon_hours)
    for vid, received in first_seen.items():
        if vid in replied or received < horizon:
            continue
        due = received + timedelta(minutes=cfg.no_reply_for(seat))
        if now < due:
            continue
        card = card_id(vid)
        if closed and (seat, card) in closed:
            continue
        if vid in held:
            held_at, why = held[vid]
            out.append(
                item(
                    seat,
                    "reply_held",
                    card,
                    max(due, held_at),
                    f"A request received {hhmm(received)} (card {card}) has had its reply held"
                    f"{f' ({why})' if why else ''} and nothing sent for {span(now - received)}.",
                    "Read the held reply and release or rewrite it; the requester has heard nothing.",
                )
            )
            continue
        out.append(
            item(
                seat,
                "no_reply",
                card,
                due,
                f"A request received {hhmm(received)} (card {card}) has had NO reply for {span(now - received)}.",
                "Find the request (team@ card with this id, or .claude/bin/requests) and answer it or find out why the Operator did not.",
            )
        )
    return out


#: The pseudo-seat for findings about this email itself.
MONITOR_SEAT = "work-health"


def missed_run_items(since: Optional[datetime], cfg: Config, now: datetime) -> list[Item]:
    """The previous successful run started too long ago: hourly slots were
    dropped or failed, and the hours between were unwatched."""
    if since is None or now - since <= timedelta(minutes=cfg.missed_run_after_minutes):
        return []
    return [
        item(
            MONITOR_SEAT,
            "monitor_gap",
            f"gap:{since.isoformat()}",
            since,
            f"Previous run missing: the last successful work-health run started {hhmm(since)}, "
            f"{span(now - since)} ago. Problems in between were reported late or not at all.",
            "Check the work-health workflow's runs (schedule dropped, or runs failing).",
        )
    ]
