"""``mailbox_keeper_run``: the broker keeps one managed mailbox.

Class N (ADR 0050, doctrine Law 18): the cron pre_run of the ``mailbox-keeper``
skill calls this verb hourly and wakes no turn. The caller names only the MODE
(and, from the pre_run, whether the seat's cost breaker allows model spend).
Who is written to, what is filed and what the email says are the broker's, from
the seat's own customer.yaml and the mailbox itself.

MODES.
``scheduled``  what the cron row sends, hourly: ``daily`` on the first run at
               or after ``send_after_hour`` (seat-local) whose day's list has
               not gone out, else ``hourly``. A morning run that failed is made
               up by the next one.
``hourly``     SMD Ops mail only, $0: settled alert threads are trashed, the
               rest are filed under ``alert_label`` and leave the inbox, and the
               lines that need the owner are kept for the next list. The first
               run with a label also files older SMD Ops mail (one unwindowed
               backfill).
``daily``      the hourly pass over the whole inbox, then (``classify: true``)
               a model reads each remaining thread (``mailbox_keeper_model.py``),
               then the one list is sent, then receipts and notices are filed.
``dry_run``    read and decide only. With ``"classify": true`` the model reads
               every candidate thread and each result comes back (kind, what,
               recommend, draft, amount, the floor applied); nothing is filed,
               drafted or sent, and only the spend ledger is written. This is
               the rehearsal, run through the seat's socket (verb class
               ROOT_OR_AGENT): ``operator/bin/seat-probe.sh``.

ORDER (critic findings 2026-11-16). SMD Ops lines are recorded BEFORE their
threads leave the inbox; drafts are made before the send (so the list says
"Draft is in the thread" only when Gmail returned one); filing runs after the
send, on a re-read that skips any thread that changed; the classified and
expense ledgers and the listed state are written last.

STATE: ``mailbox_keeper_store.py`` (gone-means-gone inventory).
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import mailbox_keeper as mk
from . import mailbox_keeper_classify as mkc
from . import mailbox_keeper_digest as mkd
from . import mailbox_keeper_mail as mail
from . import mailbox_keeper_model as model
from . import mailbox_keeper_store as store
from . import seat_config
from .broker_context import BrokerContext

VERBS: tuple[str, ...] = ("mailbox_keeper_run",)
SKILL_NAME = mail.SKILL_NAME
LANE = mail.LANE
MODES: tuple[str, ...] = ("scheduled", "daily", "hourly", "dry_run")
STATE_DIR_ENV = store.STATE_DIR_ENV
ReadFailed = mail.ReadFailed


# ---------------------------------------------------------------- config


def _seat_doc(customer_path: Path) -> dict[str, Any] | None:
    try:
        doc = seat_config.load(customer_path, encoding="utf-8", on_missing="raise") or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    return doc if isinstance(doc, dict) else None


def skill_entry(customer_path: Path) -> dict[str, Any] | None:
    """The skill's entry, or None unless the seat LISTS it and does not
    disable it. Omission is refusal (ADR 0035); unreadable enables nothing."""
    doc = _seat_doc(customer_path)
    personas = doc.get("personas") if doc else None
    for persona in personas if isinstance(personas, list) else []:
        skills = persona.get("skills") if isinstance(persona, dict) else None
        for skill in skills if isinstance(skills, list) else []:
            if isinstance(skill, dict) and skill.get("name") == SKILL_NAME:
                return None if skill.get("enabled") is False else skill
    return None


def classify_authored(customer_path: Path) -> bool:
    """The seat lists the keeper and authors its ``classify`` setting (true or
    false): the entrypoint materializes the model key for the broker only then."""
    entry = skill_entry(customer_path)
    found = entry.get("settings") if entry else None
    return isinstance(found, dict) and "classify" in found


def _daily_cap(customer_path: Path, fallback: float) -> float:
    """The seat's ``safety.sticky_stop.cost_cap_daily_cents``; unauthored, the
    keeper's own per-run fence (the stricter reading, never a platform guess)."""
    doc = _seat_doc(customer_path) or {}
    block = (doc.get("safety") or {}).get("sticky_stop") if isinstance(doc.get("safety"), dict) else None
    raw = block.get("cost_cap_daily_cents") if isinstance(block, dict) else None
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0 else fallback


def _seat_inbox(broker: BrokerContext) -> str:
    from .agentmail_auth import seat_inbox_address

    try:
        return seat_inbox_address(broker.customer_path, broker.customer_slug)
    except Exception:  # noqa: BLE001 - the setting can still name it
        return ""


def _classify_unauthored(settings: mk.Settings) -> bool:
    return not (settings.classify_model and settings.receipts_label and settings.cost_fence_cents > 0)


# ---------------------------------------------------------------- reading


def _ops_threads(broker: BrokerContext, settings: mk.Settings, state: dict[str, Any], base: list[dict[str, Any]]):
    """The SMD Ops threads to sweep: ``base`` plus, once, the unwindowed backfill."""
    if not settings.alert_label or state["backfill_done"]:
        return base, ""
    try:
        older, _ = mail.read_threads(
            broker.operations, settings, mail.ops_query(settings, windowed=False), mail.BACKFILL_MAX_PAGES
        )
    except ReadFailed as exc:
        return base, exc.code
    seen = {str(t.get("id")) for t in base}
    state["backfill_done"] = True
    return base + [t for t in older if str(t.get("id")) not in seen], ""


def _sweep(broker: BrokerContext, settings: mk.Settings, state: dict[str, Any], directory: Path, threads):
    """Plan the SMD Ops pass, RECORD its needs-you lines, then act. Returns the
    Sweep, or None when the record could not be written (nothing then acted)."""
    pending = state["ops_pending"]
    listed = set(state["ops_listed"]) | {str(p.get("message_id")) for p in pending}
    sweep = mail.plan_sweep(threads, settings, listed)
    state["ops_pending"] = pending + sweep.needs_you
    try:
        store.save_state(directory, state)
    except OSError:
        return None
    mail.apply_sweep(broker.operations, settings, sweep, lambda t: store.log_trashed(directory, t))
    return sweep


def _sweep_counts(sweep: mail.Sweep) -> dict[str, int]:
    return {
        "settled": len(sweep.trash),
        "trashed": sweep.trashed,
        "trash_failed": sweep.trash_failed,
        "labelled": sweep.labelled,
        "label_failed": sweep.label_failed,
        "needs_you": len(sweep.needs_you),
    }


def _hourly(broker: BrokerContext, settings: mk.Settings, state: dict[str, Any], directory: Path) -> dict[str, Any]:
    try:
        threads, pages = mail.read_threads(broker.operations, settings, mail.ops_query(settings), settings.max_pages)
    except ReadFailed as exc:
        return {"ok": True, "mode": "hourly", "status": "read_failed", "reason": exc.code}
    threads, backfill_error = _ops_threads(broker, settings, state, threads)
    sweep = _sweep(broker, settings, state, directory, threads)
    if sweep is None:
        return {"ok": True, "mode": "hourly", "status": "state_unsaved"}
    failed = sweep.trash_failed or sweep.label_failed
    out = {"ok": True, "mode": "hourly", "status": "partial" if failed else "done"}
    out["counts"] = {"pages": pages, "threads": len(threads), **_sweep_counts(sweep)}
    return {**out, **({"reason": "backfill:" + backfill_error} if backfill_error else {})}


# ---------------------------------------------------------------- classify


def _candidates(threads: list[dict[str, Any]], settings: mk.Settings) -> list[mkc.Candidate]:
    return [c for c in (mkc.candidate(t, settings) for t in threads) if c is not None]


def _still_open(
    cands: list[mkc.Candidate], classified: dict[str, dict[str, str]]
) -> tuple[list[mkc.Candidate], int, str]:
    """(threads to read, decisions listed before that still wait, the earliest listing date)."""
    fresh, still, since = [], 0, ""
    for c in cands:
        prior = classified.get(c.thread_id) or {}
        if prior.get("message_id") != c.message_id:
            fresh.append(c)
        elif prior.get("kind") == "decision":
            still += 1
            listed_on = str(prior.get("listed_on") or "")
            since = min(since, listed_on) if since and listed_on else (since or listed_on)
        else:
            fresh.append(c)  # a receipt or notice read before and still in the inbox failed to file: read it again
    return fresh, still, since


def _classify(broker, settings, request, directory, today, fresh) -> tuple[model.Reading, str]:
    """(the reading, the classify word for the heartbeat row)."""
    if not settings.classify:
        return model.Reading(), "off"
    if request.get("model") != "allowed":  # only the pre_run asks the seat's cost breaker; no answer is a refusal
        return model.Reading(), "refused_by_meter"
    if _classify_unauthored(settings):
        return model.Reading(), "unauthored"
    cap = _daily_cap(broker.customer_path, settings.cost_fence_cents)
    reading = model.read_all(fresh, settings, day_room_cents=max(0.0, cap - store.spent_today(directory, today)))
    try:
        store.add_spend(directory, today, reading.cents)
    except OSError:
        pass  # the breaker is still charged by the pre_run with the cents this run reports
    if reading.stopped in ("no_credential", "model_unpriced", "model_errors"):
        return reading, "failed"
    if reading.stopped in ("fence", "deadline"):
        return reading, "fence"  # threads were left unread: the triage flags it
    return reading, "ok"


# ---------------------------------------------------------------- the list


def _decision_needs(
    broker, settings, reading: model.Reading, by_id: dict[str, mkc.Candidate]
) -> tuple[list[mkd.Need], int]:
    needs, drafts = [], 0
    for o in reading.outcomes:
        if o.kind != "decision":
            continue
        c = by_id[o.thread_id]
        draft = mail.make_draft(broker.operations, settings, c, o) if o.draft else ""
        drafts += draft == "made"
        needs.append(
            mkd.Need(
                f"{mkd.who(c.sender_name, c.sender)}: {o.what}",
                o.recommend,
                draft if draft in ("made", "exists") else "",
            )
        )
    return needs, drafts


def _waiting_needs(plan: mk.Plan, state: dict[str, Any], zone: str) -> tuple[list[mkd.Need], int]:
    """Rule 2's people waiting on a reply, for a list the model did not read:
    (the ones not listed before, the count listed before and still waiting)."""
    listed = set(state["waiting"])
    needs = []
    for w in plan.waiting:
        if w.message_id in listed:
            continue
        sender = f"{w.sender_name} <{w.sender}>" if w.sender_name else w.sender
        since = mkd.local_time(w.since_ms, zone)
        needs.append(mkd.Need(f"{sender}: {w.subject or '(no subject)'} (waiting since {since})"))
    return needs, sum(1 for w in plan.waiting if w.message_id in listed)


def _digest(settings, state, sweep, reading, by_id, needs, still) -> mkd.Digest:
    d = mkd.Digest(needs=needs, still_open=still[0], still_open_since=still[1], receipts_label=settings.receipts_label)
    where = f"{settings.alert_label} label" if settings.alert_label else "in the inbox"
    d.needs += [mkd.Need(f"{line} ({where}).") for line in mkd.ops_lines(state["ops_pending"], settings.timezone)]
    for o in reading.outcomes:
        c = by_id[o.thread_id]
        if o.kind == "receipt":
            d.receipts.append(f"{c.sender_name or c.sender} {o.amount}")
        elif o.kind == "notice":
            d.archived.append(f'{c.sender_name or c.sender} "{c.subject}"')
        elif o.kind == "unread":
            d.unread.append(f"{mkd.who(c.sender_name, c.sender)}: {c.subject or '(no subject)'}")
    d.stopped = reading.stopped
    d.ops_label, d.ops_labelled, d.ops_trashed = settings.alert_label, sweep.labelled, sweep.trashed
    d.ops_failed, d.ops_unverified = sweep.trash_failed + sweep.label_failed, sweep.kept_unverified
    d.ops_unlabelled = sweep.unlabelled
    return d


# ---------------------------------------------------------------- after the send


def _act(
    broker, settings: mk.Settings, reading: model.Reading, by_id, today: str
) -> tuple[list[dict], set[str], dict[str, int]]:
    """File receipts and notices. (expense rows, thread ids skipped, counts)."""
    counts = {"filed": 0, "file_skipped": 0, "file_failed": 0}
    rows: list[dict] = []
    skipped: set[str] = set()
    to_file = [o for o in reading.outcomes if o.kind in ("receipt", "notice")]
    label_id = ""
    if any(o.kind == "receipt" for o in to_file):
        try:
            label_id = broker.operations.gmail_label_ensure(
                {"name": settings.receipts_label, "mailbox": settings.mailbox}
            )
        except Exception:  # noqa: BLE001 - no Receipts label: no receipt leaves the inbox
            for o in to_file:
                if o.kind == "receipt":
                    skipped.add(o.thread_id)  # not recorded, so it is read again on the next run
                    counts["file_failed"] += 1
            to_file = [o for o in to_file if o.kind == "notice"]
    for o in to_file:
        result = mail.file_thread(broker.operations, settings, o, label_id)
        if result != "done":
            skipped.add(o.thread_id)
            counts["file_skipped" if result == "changed" else "file_failed"] += 1
            continue
        counts["filed"] += 1
        if o.kind == "receipt":
            c = by_id[o.thread_id]
            day = datetime.fromtimestamp(c.date_ms / 1000, tz=ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")
            rows.append(
                {
                    "thread_id": o.thread_id,
                    "message_id": o.message_id,
                    "date": day,
                    "vendor": c.sender_name or c.sender,
                    "amount": o.amount,
                    "cents": o.cents,
                    "currency": o.currency,
                    "filed_on": today,
                }
            )
    return rows, skipped, counts


def _ledgers(directory, state, plan, reading, skipped, rows, classified, today, by_thread) -> None:
    """Written last, after the list went out. Raises OSError."""
    store.append_expenses(directory, rows)
    seen = set(by_thread)
    for tid in [t for t in classified if t not in seen]:
        del classified[tid]  # left the inbox: a thread that returns brings a new message id
    for o in reading.outcomes:
        if o.kind != "unread" and o.thread_id not in skipped:
            classified[o.thread_id] = {"message_id": o.message_id, "kind": o.kind, "listed_on": today}
    store.save_classified(directory, classified)
    state["ops_listed"] = state["ops_listed"] + [str(p.get("message_id")) for p in state["ops_pending"]]
    state["ops_pending"] = []
    state["waiting"] = sorted({w.message_id for w in plan.waiting})
    state["alerts"] = sorted({a.thread_id for a in (*plan.open_alerts, *plan.never_clears)})
    state["digest_sent_on"] = today
    store.save_state(directory, state)


def _reading_counts(reading: model.Reading, cands: int, still: int) -> dict[str, int]:
    kinds = [o.kind for o in reading.outcomes]
    return {
        "candidates": cands,
        "still_open": still,
        "decisions": kinds.count("decision"),
        "receipts": kinds.count("receipt"),
        "notices": kinds.count("notice"),
        "unread": kinds.count("unread"),
        "model_calls": reading.calls,
        "cost_cents": math.ceil(reading.cents),
    }


def _daily(broker, settings: mk.Settings, state, directory: Path, request, today: str) -> dict[str, Any]:
    try:
        threads, pages = mail.read_threads(broker.operations, settings, mail.inbox_query(settings), settings.max_pages)
    except ReadFailed as exc:
        return {"ok": True, "mode": "daily", "status": "read_failed", "reason": exc.code}
    swept, _ = _ops_threads(broker, settings, state, threads)
    sweep = _sweep(broker, settings, state, directory, swept)
    if sweep is None:
        return {"ok": True, "mode": "daily", "status": "state_unsaved"}
    plan = mk.plan(threads, settings, int(time.time() * 1000))
    out: dict[str, Any] = {
        "ok": True,
        "mode": "daily",
        "counts": {"pages": pages, "threads": len(threads), **_sweep_counts(sweep)},
    }
    if not settings.digest_recipient:
        return {**out, "status": "recipient_unauthored"}
    stop = _sent_elsewhere(broker, settings, state, directory, today, out, wait=True)
    if stop is not None:
        return stop  # before any model spend: another machine's list owns today
    try:
        classified = store.load_classified(directory)
    except (OSError, ValueError) as exc:
        return {**out, "status": "state_unreadable", "reason": type(exc).__name__}
    cands = _candidates(threads, settings)
    fresh, still, since = _still_open(cands, classified)
    reading, word = _classify(broker, settings, request, directory, today, fresh)
    by_id = {c.thread_id: c for c in cands}
    needs, drafts = _decision_needs(broker, settings, reading, by_id)
    if word in ("off", "refused_by_meter", "unauthored"):
        needs, still = _waiting_needs(plan, state, settings.timezone)
        since = ""
    out["classify"] = word
    out["counts"].update(
        {**_reading_counts(reading, len(fresh), still), "drafts": drafts, "waiting": len(plan.waiting)}
    )
    subject, text = mkd.render(_digest(settings, state, sweep, reading, by_id, needs, (still, _day_label(since))))
    return _send_then_file(
        broker, settings, state, directory, plan, reading, by_id, classified, today, out, subject, text
    )


def _day_label(iso_day: str) -> str:
    try:
        return datetime.strptime(iso_day, "%Y-%m-%d").strftime("%b %d").replace(" 0", " ")
    except ValueError:
        return iso_day


def _sent_elsewhere(broker, settings, state, directory, today, out, *, wait: bool) -> dict[str, Any] | None:
    """The answer that ends the run when today's list already went out (any
    machine) or the lookup failed; None when this run may send. Asked twice:
    after the jitter, before any model spend, and again just before the send."""
    try:
        sent = mail.already_sent(broker, settings, wait=wait)
    except Exception as exc:  # noqa: BLE001 - could not look: never send blind
        return {**out, "status": "send_check_failed", "reason": type(exc).__name__}
    if not sent:
        return None
    state["digest_sent_on"] = today
    try:
        store.save_state(directory, state)
    except OSError:
        pass  # the next run asks AgentMail again
    return {**out, "status": "already_sent_today"}


def _send_then_file(broker, settings, state, directory, plan, reading, by_id, classified, today, out, subject, text):
    stop = _sent_elsewhere(broker, settings, state, directory, today, out, wait=False)
    if stop is not None:
        return stop
    try:
        mail.send_digest(broker, settings.digest_recipient, subject, text)
    except Exception as exc:  # noqa: BLE001 - a refused or failed send is recorded by the transmit itself
        return {**out, "status": "digest_failed", "reason": type(exc).__name__}
    rows, skipped, counts = _act(broker, settings, reading, by_id, today)
    out["counts"].update(counts)
    try:
        _ledgers(directory, state, plan, reading, skipped, rows, classified, today, by_id)
    except OSError as exc:
        return {**out, "status": "sent_state_unsaved", "reason": type(exc).__name__}
    return {**out, "status": "done"}


# ---------------------------------------------------------------- dry run


def _decisions(plan: mk.Plan, state: dict[str, Any]) -> dict[str, Any]:
    listed_alerts, listed_waiting = set(state["alerts"]), set(state["waiting"])
    return {
        "settled": [{"thread_id": t, "rule": "1_settled_alert"} for t in plan.settled],
        "open_alerts": [
            {"thread_id": a.thread_id, "rule": "1b_open_alert", "already_listed": a.thread_id in listed_alerts}
            for a in plan.open_alerts
        ],
        "never_clears": [
            {"thread_id": a.thread_id, "rule": "1b_never_clears", "already_listed": a.thread_id in listed_alerts}
            for a in plan.never_clears
        ],
        "waiting": [
            {
                "thread_id": w.thread_id,
                "message_id": w.message_id,
                "rule": "2_waiting",
                "already_listed": w.message_id in listed_waiting,
            }
            for w in plan.waiting
        ],
    }


def _result_row(o: mkc.Outcome, c: mkc.Candidate) -> dict[str, Any]:
    return {
        "thread_id": o.thread_id,
        "sender": c.sender,
        "sender_name": c.sender_name,
        "subject": c.subject,
        "kind": o.kind,
        "what": o.what,
        "recommend": o.recommend,
        "draft": o.draft,
        "amount": o.amount,
        "cents": o.cents,
        "currency": o.currency,
        "floor": o.floor,
        "reason": o.reason,
    }


def _dry_run(broker, settings: mk.Settings, state, directory: Path, request, today: str) -> dict[str, Any]:
    try:
        threads, pages = mail.read_threads(broker.operations, settings, mail.inbox_query(settings), settings.max_pages)
    except ReadFailed as exc:
        return {"ok": True, "status": "read_failed", "reason": exc.code}
    plan = mk.plan(threads, settings, int(time.time() * 1000))
    sweep = mail.plan_sweep(threads, settings, set(state["ops_listed"]))
    counts = {
        "pages": pages,
        "threads": len(threads),
        "settled": len(plan.settled),
        "open_alerts": len(plan.open_alerts),
        "never_clears": len(plan.never_clears),
        "waiting": len(plan.waiting),
    }
    out: dict[str, Any] = {
        "ok": True,
        "status": "dry_run",
        "mode": "dry_run",
        "counts": counts,
        "decisions": _decisions(plan, state),
    }
    out["ops"] = {"trash": sweep.trash, "label": sweep.label, "needs_you": [p["line"] for p in sweep.needs_you]}
    if request.get("candidates") is True:  # $0: what a reading would be shown, for writing an answer key first
        out["candidates"] = [
            {
                "thread_id": c.thread_id,
                "from": mkd.who(c.sender_name, c.sender),
                "subject": c.subject,
                "facts": {"person": c.is_person, "waiting": c.waiting_on_owner, "watched": c.watched},
                "text": c.text[-1200:],
            }
            for c in _candidates(threads, settings)
        ]
    if request.get("classify") is not True:
        return out
    if _classify_unauthored(settings):
        return {**out, "classify": "unauthored"}
    cands = _candidates(threads, settings)
    cap = _daily_cap(broker.customer_path, settings.cost_fence_cents)
    reading = model.read_all(cands, settings, day_room_cents=max(0.0, cap - store.spent_today(directory, today)))
    try:
        store.add_spend(directory, today, reading.cents)
    except OSError:
        pass  # reported below; the vendor bill is the record
    by_id = {c.thread_id: c for c in cands}
    out["classified"] = [_result_row(o, by_id[o.thread_id]) for o in reading.outcomes]
    out["counts"].update(_reading_counts(reading, len(cands), 0))
    out["cost_cents_exact"] = round(reading.cents, 4)
    return {**out, "classify": reading.stopped or "ok"}


# ---------------------------------------------------------------- the verb


def _today(zone: str) -> str:
    return datetime.now(tz=ZoneInfo(zone)).strftime("%Y-%m-%d")


def run_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    mode = str(request.get("mode") or "scheduled")
    if mode not in MODES:
        raise ValueError(f"mailbox_keeper_run mode must be one of {list(MODES)}")
    entry = skill_entry(broker.customer_path)
    if entry is None:
        return {"ok": True, "status": "not_enabled"}
    found = entry.get("settings")
    raw: dict[str, Any] = found if isinstance(found, dict) else {}
    try:
        settings = mk.settings_from(raw, seat_inbox=_seat_inbox(broker))
    except mk.SettingsError as exc:
        return {"ok": True, "status": "settings_invalid", "reason": str(exc)}
    directory = store.state_dir()
    try:
        state = store.load_state(directory)
    except (OSError, ValueError) as exc:
        return {"ok": True, "status": "state_unreadable", "reason": type(exc).__name__}
    today = _today(settings.timezone)
    if mode == "dry_run":
        return _dry_run(broker, settings, state, directory, request, today)
    if mode == "scheduled":
        hour = datetime.now(tz=ZoneInfo(settings.timezone)).hour
        mode = "daily" if state["digest_sent_on"] != today and hour >= settings.send_after_hour else "hourly"
    try:
        with store.RunLock(directory):
            state = store.load_state(directory)
            if mode == "daily" and state["digest_sent_on"] == today:
                return {"ok": True, "mode": "daily", "status": "already_sent_today"}
            if mode == "hourly":
                return _hourly(broker, settings, state, directory)
            return _daily(broker, settings, state, directory, request, today)
    except store.Busy:
        return {"ok": True, "mode": mode, "status": "busy"}
    except OSError as exc:
        return {"ok": True, "mode": mode, "status": "state_unreadable", "reason": type(exc).__name__}


__all__ = [
    "MODES",
    "SKILL_NAME",
    "STATE_DIR_ENV",
    "VERBS",
    "ReadFailed",
    "classify_authored",
    "run_verb",
    "skill_entry",
]
