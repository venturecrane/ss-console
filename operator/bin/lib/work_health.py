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

from bin.lib import job_lanes, seat_purpose, work_health_drafting

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "work-health.yaml"

#: The audit action_type prefix of each job lane, as the broker writes it
#: (``operator/workspace_broker/<lane>_ledger.py`` AUDIT_TYPE); the
#: ``audit_prefix`` column of operator/contracts/job-lanes.yaml.
LANE_PREFIXES = dict(job_lanes.AUDIT_PREFIX_LANE)
#: Leading reason tokens a person writes when ending a job on purpose.
#: ``stopped_by_smd`` is written only by SMD's stop command
#: (``operator/runners/jobs/jobs/smd_stop.py``), which records who stopped the
#: job and why: decided work, not open work (triage, 2026-11-06).
_CLOSED_REASONS = frozenset({"superseded", "closed", "cancelled", "canceled", "stopped_by_smd"})
IN_FLIGHT = {"SUBMITTED": "queued", "QUEUED": "queued", "RUNNING": "running"}
#: A running job's progress row: not a state, the instant it last moved (``Job.moved``).
PROGRESS = "PROGRESS"
ENDED_BAD = {"HELD": "held", "FAILED": "failed"}
#: A state that is neither in flight nor ended: the records lane's reply waiting
#: on SMD's review (``RECORDS_JOB_REVIEW``). Not graded as stuck; the lane's own
#: four-hour review alert reports a review left waiting.
WAITING = frozenset({"REVIEW"})
#: Every lane's audit rows, from the one declaration (job-lanes.yaml audit_prefix).
_JOB_TYPE = re.compile(rf"^({'|'.join(sorted(LANE_PREFIXES))})_JOB_([A-Z_]+)$")
_REASON_HEAD = re.compile(r"^[a-z0-9_.-]+")
_ULID = re.compile(r"\A[0-9A-HJKMNP-TV-Z]{26}\Z")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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
    no_reply_minutes_by_purpose: dict[str, int]
    # Seat slug -> purpose (ADR 0107, operator/fleet/seats.yaml). A seat the
    # file does not name takes the default threshold.
    seat_purposes: dict[str, str] = field(default_factory=dict)
    #: How many of a lane's latest page-reading jobs the page store's repeat
    #: misses are read over (``work_health_page_store``).
    page_store_cold_jobs: int = 3

    def no_reply_for(self, seat: str) -> int:
        purpose = self.seat_purposes.get(seat)
        return self.no_reply_minutes_by_purpose.get(purpose or "", self.no_reply_minutes)


def load_config(path: Path = CONTRACT_PATH, seats: Optional[dict[str, dict[str, Any]]] = None) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("lanes"), dict):
        raise ValueError(f"{path}: not a work-health contract")
    lanes = {}
    for name, spec in raw["lanes"].items():
        lanes[name] = Lane(name, str(spec["label"]), int(spec["queue_minutes"]), int(spec["run_minutes"]))
    if set(lanes) != set(LANE_PREFIXES.values()):
        raise ValueError(f"{path}: lanes must be exactly {sorted(LANE_PREFIXES.values())}")
    skip = ("lanes", "no_reply_minutes_by_purpose", "seat_purposes", "page_store_cold_jobs")
    keys = [k for k in Config.__dataclass_fields__ if k not in skip]
    by_purpose = {str(k): int(v) for k, v in (raw.get("no_reply_minutes_by_purpose") or {}).items()}
    unknown = set(by_purpose) - set(seat_purpose.PURPOSES)
    if unknown:
        raise ValueError(f"{path}: no_reply_minutes_by_purpose names no purpose: {sorted(unknown)}")
    purposes = {slug: str(e["purpose"]) for slug, e in (seats or seat_purpose.load()).items()}
    return Config(
        **{k: int(raw[k]) for k in keys},
        lanes=lanes,
        no_reply_minutes_by_purpose=by_purpose,
        seat_purposes=purposes,
        page_store_cold_jobs=int(raw.get("page_store_cold_jobs", 3)),
    )


@dataclass
class Page:
    """The email an urgent item sends (Captain, 2026-11-02: an ops email must be
    something he can consume): same shape as a request email. The item's ``what``
    and ``todo`` stay as the agent's half, below the fold."""

    subject: str
    happened: str
    todo: str


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
    #: Set on an item that must PAGE (its own email every run while it stands),
    #: not only sit in the digest: a dead sign-in stops all work.
    page: Optional[Page] = None
    #: The job this item is about, when it is about one: how "one job, one
    #: line" finds the other lines about the same job (triage, 2026-11-04).
    job: Optional[str] = None
    #: The newest occurrence a repeating item counts (a call-failure line's last
    #: hit). An item already reported is NEW again only when this moves past
    #: the newest one reported (``work_health_mail.split_new``).
    latest: Optional[datetime] = None


# Ordering in the email: the work a firm person is waiting on first.
RANK = {
    "monitor_gap": -1,
    "unreadable": 0,
    "no_reply": 10,
    "could_not": 11,
    "reply_held": 12,
    "job_stuck": 20,
    "job_held": 22,
    "job_failed": 24,
    "job_hand_step": 26,
    "auth_failed": 30,
    "connector_alert": 31,
    "heartbeat_stale": 32,
    "auth_unchecked": 34,
    "missed_slot": 40,
    "schedule_paused": 41,
    "schedule_drift": 44,
    "page_store_off": 45,
    "page_store_cold": 46,
    "silent_run": 42,
    "shortfall": 50,
    "sentry": 60,
}


def item(
    seat: str,
    kind: str,
    key: str,
    onset: datetime,
    what: str,
    todo: str,
    page: Optional[Page] = None,
    *,
    job: Optional[str] = None,
    latest: Optional[datetime] = None,
) -> Item:
    return Item(seat, kind, f"{seat}|{kind}|{key}", onset, what, todo, RANK.get(kind, 50), page, job, latest)


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


#: Phoenix keeps no daylight saving, so a fixed offset is exact all year.
PHOENIX = timezone(timedelta(hours=-7), "MST")


def hhmm(ts: datetime) -> str:
    """A time a person reads without converting (Captain, 2026-10-16): Phoenix,
    labelled, e.g. ``Oct 16 7:51am MST``."""
    local = ts.astimezone(PHOENIX)
    clock = local.strftime("%I:%M%p").lstrip("0").lower()
    return f"{local.strftime('%b')} {local.day} {clock} MST"


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
    #: When a person asked the lane to resume this job (``*_JOB_RESUME_REQUESTED``).
    #: Not a state, so it never enters ``events``; it is how a delivered job is
    #: known to have needed a hand.
    resumes: list[datetime] = field(default_factory=list)
    #: When a running job last MOVED (``*_JOB_PROGRESS``: its stage or its
    #: files-pulled count changed, 2026-11-24). A long pull that keeps moving is
    #: not stuck; one whose count froze is, from the last move. Not a state.
    moved: Optional[datetime] = None

    @property
    def submitted_at(self) -> datetime:
        """The job's first row in the read window (its ordering instant)."""
        return self.events[0][0]

    @property
    def started_at(self) -> Optional[datetime]:
        """When the job was really submitted, for a person to read: the instant
        inside its ULID, else its first row when that row IS the submission.
        None when neither is known: an old job closed today shows only its
        close in the window, and calling that its start read "started Nov 6"
        for a job from August (triage, 2026-11-06)."""
        minted = ulid_time(self.job_id)
        if minted is not None:
            return minted
        return self.events[0][0] if self.events[0][1] in IN_FLIGHT and self.events[0][1] != "RUNNING" else None

    @property
    def last(self) -> tuple[datetime, str, Optional[str]]:
        return self.events[-1]


def ulid_time(value: str) -> Optional[datetime]:
    """The millisecond instant a ULID carries in its first ten characters, or
    None when ``value`` is not a ULID."""
    if not isinstance(value, str) or not _ULID.match(value):
        return None
    ms = 0
    for char in value[:10]:
        ms = ms * 32 + _CROCKFORD.index(char)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def jobs_from_rows(rows: list[dict]) -> list[Job]:
    """Every job the window shows, keyed (lane, job_id), events in ledger order.
    A resume note (``*_JOB_RESUME_REQUESTED``) is not a state: it goes to
    ``resumes``, never ``events``."""
    jobs: dict[tuple[str, str], Job] = {}
    moved: dict[tuple[str, str], datetime] = {}
    for ts, row in stamped(rows):
        match = _JOB_TYPE.match(str(row.get("action_type") or ""))
        if not match:
            continue
        state = match.group(2)
        if state == PROGRESS:
            md = meta(row)
            if isinstance(md.get("job_id"), str) and md["job_id"]:
                key = (LANE_PREFIXES[match.group(1)], md["job_id"])
                moved[key] = max(ts, moved.get(key, ts))
            continue
        if (
            state not in IN_FLIGHT
            and state not in ENDED_BAD
            and state not in WAITING
            and state not in ("DELIVERED", "RESUME_REQUESTED")
        ):
            continue
        md = meta(row)
        job_id = md.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            continue
        lane = LANE_PREFIXES[match.group(1)]
        job = jobs.setdefault((lane, job_id), Job(lane=lane, job_id=job_id))
        if state == "RESUME_REQUESTED":
            job.resumes.append(ts)
            continue
        job.events.append((ts, state, reason_token(md.get("reason"))))
        if isinstance(md.get("matter_number"), str) and md["matter_number"]:
            job.matter = md["matter_number"][:40]
        if md.get("trigger") == "scheduled" or str(md.get("request_ref") or "").startswith("scheduled:"):
            job.scheduled = True
    for key, at in moved.items():
        if key in jobs:
            jobs[key].moved = at
    # A resume whose job's state rows fell outside the window has no state to judge.
    return [job for job in jobs.values() if job.events]


def hand_steps(job: Job) -> list[str]:
    """Each point a person had to step in before this job delivered, in ledger
    order: a hold or failure it came back from (only a person releases a held
    job or resumes a failed one), and every resume a person asked for. Empty for
    a job that ran straight through."""
    steps: list[tuple[datetime, str]] = []
    for ts, state, reason in job.events[:-1]:
        if state in ENDED_BAD:
            steps.append((ts, f"{ENDED_BAD[state]}{f' ({reason})' if reason else ''} {hhmm(ts)}"))
        elif reason and reason.startswith("lane_unproven"):
            steps.append((ts, f"held for SMD's lane proof {hhmm(ts)}"))
    steps += [(ts, f"resumed by hand {hhmm(ts)}") for ts in job.resumes]
    return [text for _ts, text in sorted(steps, key=lambda pair: pair[0])]


def _job_name(job: Job, cfg: Config) -> str:
    label = cfg.lanes[job.lane].label
    where = f" on matter {job.matter}" if job.matter else ""
    origin = "scheduled " if job.scheduled else ""
    started = job.started_at
    if started is not None:
        when = f"started {hhmm(started)}"
    elif job.last[1] in IN_FLIGHT or job.last[1] in WAITING:
        when = f"first seen {hhmm(job.submitted_at)}"
    else:
        when = f"ended {hhmm(job.last[0])}"
    return f"{origin}{label} job{where} ({when}, ref {job.job_id[-8:]})"


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


def _queue_clock(job: Job, jobs: list[Job], cfg: Config, now: datetime) -> Optional[datetime]:
    """When a queued job's wait began to count, or None while it is still
    rightly waiting.

    A lane does not start a job while a child of a lane it waits for is live
    (job-lanes.yaml ``waits_for``, the runner's ``BUSY_PIDS``). So a queued job
    behind a RUNNING job of such a lane, inside that job's own run limit, is
    waiting, not stuck: on 2026-11-04 a negotiation job queued behind a running
    demand job was listed as stuck after 15 minutes. A blocker that hangs is
    still caught, by its own run limit. Once the blockers end, the queue limit
    counts from the last of them, not from the submission.

    A lane also waits for ITSELF: the runner starts one job at a time per lane,
    oldest first (runners/jobs/jobs/daemon.py), so a job queued behind a running
    job of its own lane is waiting too. On 2026-11-17 one request queued two
    chronologies; the second, a few dozen pages, waited behind the first, a
    few thousand pages, and was listed stuck after 30 minutes while the lane
    was doing exactly what it should."""
    since = job.last[0]
    waits = (job.lane, *job_lanes.LANE_WAITS_FOR.get(job.lane, ()))
    for other in jobs:
        if other is job or other.lane not in waits:
            continue
        ts, state, _reason = other.last
        if state == "RUNNING":
            if now - ts < timedelta(minutes=cfg.lanes[other.lane].run_minutes):
                return None
            continue
        if ts > since and any(s == "RUNNING" for _t, s, _r in other.events):
            since = ts
    return since


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
                    job=job.job_id,
                )
            )
            continue
        if state in WAITING:
            continue
        if state in IN_FLIGHT:
            phase = IN_FLIGHT[state]
            limit = lane.queue_minutes if phase == "queued" else lane.run_minutes
            clock = _queue_clock(job, jobs, cfg, now) if phase == "queued" else max(ts, job.moved or ts)
            if clock is None or now - clock < timedelta(minutes=limit):
                continue
            out.append(
                item(
                    seat,
                    "job_stuck",
                    f"{job.lane}:{job.job_id}:{state}",
                    clock + timedelta(minutes=limit),
                    f"The {name} has been {phase} for {span(now - ts)}; it should take under {span(timedelta(minutes=limit))}."
                    + (f" It last moved {span(now - job.moved)} ago." if job.moved and phase == "running" else ""),
                    "Check the job on the seat: a hung runner looks like work in progress forever.",
                    job=job.job_id,
                )
            )
        elif state in ENDED_BAD:
            if now - ts > timedelta(hours=cfg.ended_job_hours) or _superseded(job, jobs):
                continue
            if reason in _CLOSED_REASONS:
                # Ended on purpose by a person ("superseded": a relaunch replaced
                # this attempt): decided work, not open work.
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
                    work_health_drafting.next_step(job.lane, reason)
                    or "Decide: rerun, release, or tell the requester why; nothing reaches them until someone does.",
                    job=job.job_id,
                )
            )
        elif state == "DELIVERED":
            hand = _hand_step_item(seat, job, name, cfg, now)
            if hand is not None:
                out.append(hand)
    return out


def _hand_step_item(seat: str, job: Job, name: str, cfg: Config, now: datetime) -> Optional[Item]:
    """Delivered, but only because a person stepped in: the requester got the
    work, and the cause of the hand step is still in the code. Every manual fix
    becomes code, so this is work too, not a success."""
    ts = job.last[0]
    if now - ts > timedelta(hours=cfg.ended_job_hours):
        return None
    steps = hand_steps(job)
    if not steps:
        return None
    return item(
        seat,
        "job_hand_step",
        f"{job.lane}:{job.job_id}:DELIVERED",
        ts,
        f"The {name} delivered at {hhmm(ts)}, but only after a person stepped in "
        f"{len(steps)} time{'s' if len(steps) != 1 else ''}: {'; '.join(steps)}.",
        "Find why each step was needed and make it code, proven at $0 on this job's saved inputs; "
        "the next job like it will stop the same way.",
        job=job.job_id,
    )


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
