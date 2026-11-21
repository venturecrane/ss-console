"""Work-health items from delivery proof (PR #3152): a job the seat refused
to call delivered, or whose reply was not seen to reach the requester, is
open work until it is proven, answered, or said by the job's own line.

Split from ``work_health_sources`` (2026-11-16) when one job, one line and
the review-release binding took it past the module-size ceiling.

Pure: rows and a clock in, items out.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import NamedTuple, Optional

from bin.lib.work_health import Config, Item, hhmm, item, meta, parse_ts, reason_token, stamped


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
    "job_stopped": "it held, failed or is waiting on its lane's proof; the requester was told nothing, by design",
    "library_edit_refused": "the firm's own save of its library file was not used or not accepted; the firm was told nothing",
    "layout_rule_unplaced": "a layout rule could not be placed against the firm's own edit of its library file, so it stood; the firm was told nothing",
    "spend_unrecorded": "its usage shows spend while its record shows $0, so the firm's allowance and SMD's cost read low; the firm was told nothing",
}


def _found_reply_job(row: dict) -> str:
    """The job a sent reply answered, when the mailbox found it in Sent Items:
    a CONFIRM_SEND_DISPATCHED row with outcome sent, a lookup that succeeded,
    and a binding key shaped ``<kind>:<job id>:<attempt>:<state>``. "" otherwise."""
    if row.get("action_type") != "CONFIRM_SEND_DISPATCHED":
        return ""
    md = meta(row)
    if md.get("outcome") != "sent" or not str(md.get("lookup") or "").startswith("ok"):
        return ""
    parts = str(md.get("reply_binding") or "").split(":")
    return parts[1] if len(parts) == 4 else ""


#: Proof codes that mean "the requester's reply was not seen to go": a reply
#: bound to the job and later found in Sent Items answers each of them. A
#: ``wake_lost`` stayed listed 72 hours after its job's reply was found five
#: minutes later (triage, 2026-11-04).
_ANSWERED_BY_REPLY = frozenset({"reply_unproven", "reply_not_in_sent_items", "wake_lost"})
#: A job's own bad ending and its proof notice written together: the retention
#: cleanup records the failure, then sends the notice (``close_wiped_hold``), so
#: one close listed twice (triage, 2026-11-06).
_SAME_CLOSE = timedelta(seconds=60)
#: The REPLY_HELD reason the reply plugin writes when SMD rejects a held reply
#: in review (overlay ``hermes-smd-reply`` ``_review_one``; ``shared/heartbeat.py``
#: MATTER_FILES_REVIEW_REJECTED).
REVIEW_REJECTED = "matter_files_review_rejected"
#: A records lane review row's session: ``records:<job_id>``.
_LANE_SESSION_PREFIX = "records:"


class JobHold(NamedTuple):
    """A hold of a job's reply (or SMD's review rejecting it), and that job."""

    row_id: str
    at: datetime
    job: str


def _held_wake_jobs(rows: list[dict]) -> tuple[dict[str, str], dict[str, list[tuple[datetime, str, str]]]]:
    """From the seat's REPLY_HELD rows that name their wake's job (overlay
    ``_held``: ``wake_job_id``): review id -> job, and session id ->
    [(held at, recipient, job)]."""
    by_review: dict[str, str] = {}
    by_session: dict[str, list[tuple[datetime, str, str]]] = {}
    for ts, row in stamped(rows):
        if row.get("action_type") != "REPLY_HELD":
            continue
        md = meta(row)
        job = str(md.get("wake_job_id") or "")
        if not job:
            continue
        if md.get("review_id"):
            by_review[str(md["review_id"])] = job
        session = str(md.get("session_id") or "")
        if session:
            by_session.setdefault(session, []).append((ts, str(md.get("recipient") or "").strip().lower(), job))
    return by_review, by_session


def review_rejections(rows: list[dict]) -> list[JobHold]:
    """Each REPLY_HELD SMD's review rejection wrote, with the job its reply
    answered: by the review id the hold that queued it carried, or a records
    lane row's own session (``records:<job_id>``). A rejection whose job cannot
    be named is left out: it stays its own line."""
    by_review, _sessions = _held_wake_jobs(rows)
    out: list[JobHold] = []
    for ts, row in stamped(rows):
        md = meta(row)
        if row.get("action_type") != "REPLY_HELD" or reason_token(md.get("reason")) != REVIEW_REJECTED:
            continue
        session = str(md.get("session_id") or "")
        job = by_review.get(str(md.get("review_id") or ""), "")
        if not job and session.startswith(_LANE_SESSION_PREFIX):
            job = session[len(_LANE_SESSION_PREFIX) :]
        if job:
            out.append(JobHold(str(row.get("id") or ""), ts, job))
    return out


def job_holds(rows: list[dict]) -> list[JobHold]:
    """Every hold of a job's reply: a REPLY_HELD or REPLY_FAILED its wake turn
    wrote (``wake_job_id``), and each review rejection with its job."""
    out = list(review_rejections(rows))
    for ts, row in stamped(rows):
        if row.get("action_type") in ("REPLY_HELD", "REPLY_FAILED"):
            job = str(meta(row).get("wake_job_id") or "")
            if job:
                out.append(JobHold(str(row.get("id") or ""), ts, job))
    return out


def released_reply_jobs(rows: list[dict]) -> dict[str, datetime]:
    """job id -> when a held reply that answered it was released from SMD's
    review and sent.

    The release (overlay ``_review_one``) writes ``REPLY_SENT`` with
    ``released_from_review`` but no job, so the "reply is not in Sent Items"
    line stayed 72 hours after the requester had the reply (triage,
    2026-11-05). The job is the one the SAME session's ``REPLY_HELD`` named
    (``wake_job_id``) for the SAME recipient, held before the send; a release
    that names its job itself (``wake_job_id`` on the REPLY_SENT) wins."""
    _by_review, by_session = _held_wake_jobs(rows)
    out: dict[str, datetime] = {}
    for ts, row in stamped(rows):
        md = meta(row)
        if row.get("action_type") != "REPLY_SENT" or not md.get("released_from_review"):
            continue
        named = str(md.get("wake_job_id") or "")
        if named:
            out[named] = ts
            continue
        recipient = str(md.get("recipient") or "").strip().lower()
        for held_at, held_for, job in by_session.get(str(md.get("session_id") or ""), []):
            if held_at <= ts and recipient and held_for == recipient:
                out[job] = ts
    return out


_USAGE_CENTS = re.compile(r"\busage (\d{1,9}) cents\b")


def _spend_item(seat: str, job_id: str, lane: str, md: dict, ts: datetime) -> Item:
    """A ``spend_unrecorded`` row (the seat's hourly sweep, ``jobs/spend.py``):
    the job ended with usage that prices above $0 and a row that records $0, so
    the firm's allowance and SMD's cost read low. It is about money, not
    delivery, so it is worded as such."""
    m = _USAGE_CENTS.search(str(md.get("detail") or ""))
    spent = f"${int(m.group(1)) / 100:.2f}" if m else "spend"
    return item(
        seat,
        "job_failed",
        f"proof:{job_id}:spend_unrecorded",
        ts,
        f"The {lane} job's record shows $0 while its usage shows {spent} ({hhmm(ts)}, ref {job_id[-8:]}); "
        "the firm's allowance and SMD's cost read low.",
        "Correct the job's recorded spend from its usage ledger before the month's figures are used.",
        job=job_id,
    )


class _ProofLedger(NamedTuple):
    """What one pass over the ledger knows about each job's delivery proof."""

    proven: dict[str, datetime]
    replied: dict[str, datetime]
    failures: dict[str, tuple[datetime, dict]]
    ended_bad: dict[str, datetime]
    rejected: dict[str, datetime]
    #: ``ending_unrecorded`` pages, kept apart: it is not a proof failure.
    unrecorded: dict[str, tuple[datetime, dict]]
    #: The newest ending record (delivered, held or failed) per job.
    ended: dict[str, datetime]
    #: ``served_owed`` pages, kept apart so a later failure of the same job never replaces them.
    owed: dict[str, tuple[datetime, dict]] = {}


#: The code of a chronology ending the broker did not take (``jobs/medchron/unrecorded.py``).
UNRECORDED = "ending_unrecorded"
#: How the page of a job wiped with its ending unrecorded begins.
_WIPED = "true ending "


def _proof_ledger(rows: list[dict]) -> _ProofLedger:
    proven: dict[str, datetime] = {}
    replied: dict[str, datetime] = dict(released_reply_jobs(rows))
    failures: dict[str, tuple[datetime, dict]] = {}
    unrecorded: dict[str, tuple[datetime, dict]] = {}
    owed: dict[str, tuple[datetime, dict]] = {}
    ended_bad: dict[str, datetime] = {}
    ended: dict[str, datetime] = {}
    for ts, row in stamped(rows):
        answered = _found_reply_job(row)
        if answered:
            replied[answered] = max(ts, replied.get(answered, ts))
        md = meta(row)
        job_id = str(md.get("job_id") or "")
        if not job_id:
            continue
        if row.get("action_type") == "DELIVERY_PROVEN":
            proven[job_id] = ts
        elif row.get("action_type") == "DELIVERY_PROOF_FAILED":
            code = reason_token(md.get("code"))
            (unrecorded if code == UNRECORDED else owed if code == OWED else failures)[job_id] = (ts, md)
        elif str(row.get("action_type") or "").endswith(("_JOB_FAILED", "_JOB_HELD", "_JOB_DELIVERED")):
            ended[job_id] = ts
            if not str(row["action_type"]).endswith("_JOB_DELIVERED"):
                ended_bad[job_id] = ts
    rejected = {r.job: r.at for r in review_rejections(rows)}
    return _ProofLedger(proven, replied, failures, ended_bad, rejected, unrecorded, ended, owed)


def _unrecorded_items(seat: str, cfg: Config, now: datetime, led: _ProofLedger, closed: Optional[set]) -> list[Item]:
    """An ending the broker did not take is open until an ending record for the
    job lands after its newest page: the lane's retry while the workdir lives,
    or SMD's hand correction after the wipe, when nothing retries it. Its
    sentence is the page's own, which the lane composed from the job's facts:
    which ending, whether a wake was owed, and whether it went. The action
    follows the page: a wipe's page begins ``true ending <state>;``
    (``jobs/medchron/unrecorded.detail``)."""
    out: list[Item] = []
    for job_id, (ts, md) in led.unrecorded.items():
        if now - ts > timedelta(hours=cfg.ended_job_hours) or (closed and (seat, job_id) in closed):
            continue
        if job_id in led.ended and led.ended[job_id] >= ts:
            continue
        lane = cfg.lanes[md["lane"]].label if md.get("lane") in cfg.lanes else str(md.get("lane") or "a")
        said = str(md.get("detail") or "the page gave no detail").rstrip(".")
        if said.startswith(_WIPED):
            true = said.removeprefix(_WIPED).split(";", 1)[0].strip()
            todo = (
                f"Nothing retries it: correct the ledger row by hand to {true}; "
                "a same-work request is refused as already running until then."
            )
        else:
            todo = "Fix what the broker answered (named above); the lane keeps trying the record."
        out.append(
            item(
                seat,
                "job_failed",
                f"proof:{job_id}:{UNRECORDED}",
                ts,
                f"The {lane} job's ending is not in its record ({hhmm(ts)}, ref {job_id[-8:]}): {said}.",
                todo,
                job=job_id,
            )
        )
    return out


#: A response job delivered with sets SMD still owes (``jobs/drafting/served.py``).
OWED = "served_owed"


def _owed_item(seat: str, job_id: str, ts: datetime, md: dict, led: _ProofLedger) -> Optional[Item]:
    """The sets SMD owes on a delivered response job, until a record of them
    lands: ``deliver_by_smd --set`` writes a new delivery proof, and pages
    again for any set still owed."""
    if job_id in led.proven and led.proven[job_id] > ts:
        return None
    said = str(md.get("detail") or "").rstrip(".")
    return item(
        seat,
        "job_hand_step",
        f"proof:{job_id}:{OWED}",
        ts,
        f"The drafting job delivered its drafts at {hhmm(ts)} (ref {job_id[-8:]}), and the firm was told SMD is "
        f"preparing the rest: {said}.",
        "Draft each set, file it on the matter, and record it with "
        f"python -m jobs.drafting.deliver_by_smd {job_id} <record.json> --set '<set>'.",
        job=job_id,
    )


def _served_sets(job: dict) -> list[dict]:
    try:
        delivery = json.loads(job.get("delivery_json") or "{}") or {}
    except (TypeError, ValueError):
        return []
    return [r for r in delivery.get("served_sets") or [] if isinstance(r, dict) and str(r.get("set") or "").strip()]


def _told(job: dict, row: dict, rows: list[dict]) -> bool:
    """The attorney heard that an SMD-filed set is filed: staff said so
    (``deliver_by_smd --set ... --told``), or a reply went out in his request's
    thread after the set was recorded."""
    if row.get("told") is True:
        return True
    ref, after = str(job.get("message_ref") or ""), parse_ts(row.get("recorded_at"))
    if not ref or after is None:
        return False
    for ts, r in stamped(rows):
        if r.get("action_type") == "REPLY_SENT" and ts > after and str(meta(r).get("in_reply_to") or "") == ref:
            return True
    return False


def served_owed_items(
    seat: str, jobs: list[dict], rows: list[dict], now: datetime, closed: Optional[set[tuple[str, str]]] = None
) -> list[Item]:
    """Each served set SMD owes on a delivered response job, from the job's own
    ledger row (``delivery_json.served_sets``), with no lookback: a set still
    being prepared by us, then a set SMD filed whose attorney has not been told.
    One line per set, until it is filed and told."""
    out: list[Item] = []
    for job in jobs:
        job_id = str(job.get("id") or "")
        if not job_id or job.get("state") != "delivered" or (closed and (seat, job_id) in closed):
            continue
        matter = str(job.get("matter_number") or "")[:40]
        on = parse_ts(job.get("updated_at")) or now
        for r in _served_sets(job):
            label = str(r["set"])
            if r.get("status") != "filed":
                out.append(
                    item(
                        seat,
                        "job_hand_step",
                        f"proof:{job_id}:{OWED}:{label}",
                        on,
                        f"SMD owes {label} on matter {matter} (ref {job_id[-8:]}); the attorney was told it is being "
                        "prepared by us.",
                        "Draft it, file it on the matter, and record it with "
                        f"python -m jobs.drafting.deliver_by_smd {job_id} <record.json> --set '{label}'.",
                        job=job_id,
                    )
                )
            elif r.get("drafted_by") == "smd" and not _told(job, r, rows):  # an Operator set's reply told him
                doc = str(r.get("document") or "the filed document")
                out.append(
                    item(
                        seat,
                        "job_hand_step",
                        f"proof:{job_id}:{OWED}:{label}:told",
                        parse_ts(r.get("recorded_at")) or on,
                        f"{label} on matter {matter} is filed (ref {job_id[-8:]}), and the attorney has not been told.",
                        f'Tell the attorney: {label}: filed as "{doc}". Send it as an SMD note in his request\'s '
                        f"thread, then mark it: python -m jobs.drafting.deliver_by_smd {job_id} --set '{label}' --told.",
                        job=job_id,
                    )
                )
    return out


def _proof_closed(job_id: str, ts: datetime, md: dict, led: _ProofLedger) -> bool:
    """Whether a proof failure is answered or already said by another line."""
    if job_id in led.proven and led.proven[job_id] >= ts:
        return True
    if reason_token(md.get("code")) in _ANSWERED_BY_REPLY and job_id in led.replied and led.replied[job_id] >= ts:
        return True
    if job_id in led.ended_bad and led.ended_bad[job_id] >= ts - _SAME_CLOSE:
        # The job's own FAILED/HELD line already says it did not deliver
        # (job_items): one landing AFTER the proof failure, or just before it in
        # the same close (the retention cleanup writes the failure, then the
        # notice). A hold well BEFORE it is not: a job held, then released and
        # delivered, then failing its proof, is reported by nothing else
        # (job_items sees it delivered; review DB1, 2026-10-17).
        return True
    # The job DELIVERED; the lane carried an alert of its own (on negotiation, a
    # held offer). That alert has its own mail. Saying "NOT proven delivered"
    # here was false, and it was six of the 33 lines in the 2026-10-16 email.
    return "delivered, and its lane raised" in str(md.get("detail") or "")


def delivery_proof_items(
    seat: str,
    rows: list[dict],
    cfg: Config,
    now: datetime,
    closed: Optional[set[tuple[str, str]]] = None,
    owed_jobs: Optional[list[dict]] = None,
) -> list[Item]:
    """A DELIVERY_PROOF_FAILED row (the seat refused to call a job delivered,
    or its requester reply never reached Sent Items) is open work until a
    DELIVERY_PROVEN row for the same job lands after it. A reply-not-seen code
    (``_ANSWERED_BY_REPLY``) also closes when the reply went after all: the
    seat's watch stops looking at its 20 minute deadline, so a reply at minute
    21 reached the requester and the alarm stayed listed for 72 hours (triage
    reported it 10-20 and again 11-02). A job SMD closed
    (``work-health-closed.yaml``) is not listed here either (triage, 2026-11-06)."""
    led = _proof_ledger(rows)
    out: list[Item] = _unrecorded_items(seat, cfg, now, led, closed)
    # Served sets SMD owes: from the delivered ledger rows when the caller reads
    # them (no lookback, ``owed_jobs``), else from the served_owed pages.
    if owed_jobs is not None:
        out += served_owed_items(seat, owed_jobs, rows, now, closed)
    else:
        for job_id, (ts, md) in led.owed.items():
            owed = None if closed and (seat, job_id) in closed else _owed_item(seat, job_id, ts, md, led)
            out += [owed] if owed is not None else []
    for job_id, (ts, md) in led.failures.items():
        if closed and (seat, job_id) in closed:
            continue
        if reason_token(md.get("code")) == "smd_owed":
            continue  # work SMD owes a person, listed by work_health_sources.smd_owed_items
        if now - ts > timedelta(hours=cfg.ended_job_hours):
            continue
        if _proof_closed(job_id, ts, md, led):
            continue
        code = reason_token(md.get("code")) or "unknown"
        lane = cfg.lanes[md["lane"]].label if md.get("lane") in cfg.lanes else str(md.get("lane") or "a")
        if code == "spend_unrecorded":
            out.append(_spend_item(seat, job_id, lane, md, ts))
            continue
        why, todo = (
            _PROOF_WORDS.get(code, code),
            "Check what the requester actually received before anyone says it was delivered.",
        )
        if job_id in led.rejected:
            # One job, one line, naming the real cause (triage, 2026-11-04):
            # "not in Sent Items" and "nobody was told" both pointed away from it.
            why = f"SMD rejected its reply in review {hhmm(led.rejected[job_id])}, so nothing reached the requester"
            todo = (
                "Rewrite and send the reply SMD's review stopped, or tell the requester why; they have heard nothing."
            )
        out.append(
            item(
                seat,
                "job_failed",
                f"proof:{job_id}:{code}",
                ts,
                f"The {lane} job is NOT proven delivered: {why} ({hhmm(ts)}, ref {job_id[-8:]}).",
                todo,
                job=job_id,
            )
        )
    return out
