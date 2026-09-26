#!/usr/bin/env python3
"""task-list-keeper pre-run script (ADR 0021 Stream B; case-manager spec Job 1a, 1, 3).

Runs BEFORE the Hermes cron daemon wakes the agent. In code, with no model:

1. reads the authored ``case_manager:`` block (absent = every job off);
2. pulls the firm's overdue open tasks, their matters, documents and court
   calendar through the connector venv (``pull.py``);
3. joins the casework ledger (what was proposed, answered, closed) and the
   records-chase resolutions from the escalation ledger;
4. sorts every task (``classify.py``), plans the review (``review.py``) and
   renders every sentence (``lines.py``);
5. writes the casework envelope the overlay's ``casework_finish`` consumes
   (``$HERMES_HOME/.smd/pre_run/task-list-keeper.casework.json``) and the
   provenance handoff (``task-list-keeper.json``) that seeds the send gate with
   the matter numbers and days the messages carry.

The woken turn composes nothing. It calls ``casework_finish``, which replays the
queued closes, renders each message from this envelope and the outcome rows,
and sends through the full gate (SKILL.md).

Wake iff there is a message to send or a close to make. Otherwise a
SUPPRESSED_WAKE heartbeat row, and an audit failure falls back to wake
(mirror-don't-gate, ADR 0016).

Exit codes: 0 decision emitted; 2 never (every failure is a decision).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_SKILL_DIRNAME = "task-list-keeper"


def _load_skill_helpers():
    """The shared helpers vendored beside this file (canonical: operator/templates/skill_helpers.py)."""
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


_H = _load_skill_helpers()


def _sibling(filename: str):
    module = _H.load_sibling(_SKILL_DIRNAME, __file__, filename, "tlk_" + filename[:-3])
    if module is None:
        raise RuntimeError(filename + " is missing beside pre_run.py for " + _SKILL_DIRNAME)
    return module


_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
_DEFAULT_WINDOW_DAYS = 14
_CHASE_LABEL = "records-chase"


def _chaser_resolved(esc_ledger, events, snapshot) -> set[str]:
    """Task ids whose records chase the chaser resolved (its ledger identity:
    matter, task id, label records-chase, no date)."""
    if esc_ledger is None:
        return set()
    states = esc_ledger.derive_state(events)
    out = set()
    for task in snapshot.tasks:
        state = states.get(esc_ledger.item_key(task.matter_id, task.task_id, _CHASE_LABEL, None))
        if state is not None and state.resolved:
            out.add(task.task_id)
    return out


def _write_private_json(name: str, payload: dict) -> bool:
    """Atomic 0600 write under the .smd/pre_run fence (the dispatch-envelope
    discipline): the files name the matters the firm is working on."""
    try:
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + name + ".tmp")
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, directory / name)
        return True
    except Exception as exc:  # noqa: BLE001 - reported; the caller decides
        sys.stderr.write("[pre_run] " + name + " write failed (" + str(exc) + ")\n")
        return False


def _handoff(plan) -> dict:
    dates = sorted({d for days in plan.records.values() for d in days})
    return {
        "skill": _SKILL_DIRNAME,
        "started_at": _STARTED_AT,
        "dates": dates,
        "matter_ids": sorted(set(plan.matter_ids)),
        "records": [{"matterNumber": number, "dates": days} for number, days in sorted(plan.records.items())],
    }


def build_envelope(plan) -> dict:
    envelope = {"skill": _SKILL_DIRNAME, "started_at": _STARTED_AT, "messages": plan.messages}
    if plan.memos:
        envelope["memos"] = plan.memos
    return envelope


async def _suppress(writer_factory, basis: str, extra: dict, now: datetime) -> int:
    writer = writer_factory()
    if writer is None:
        return _wake(basis + "_no_audit_writer", {})
    try:
        await writer.write_suppressed_wake(
            skill_name=_SKILL_DIRNAME,
            pre_run_inputs=json.dumps(extra, sort_keys=True, default=str).encode("utf-8"),
            decision_basis=basis,
            next_scheduled_at=_H.next_scheduled_at(now, 24 * 7),
            extra_metadata=extra,
        )
    except Exception:  # noqa: BLE001 - a heartbeat that cannot land wakes (dead-man's-switch)
        return _wake(basis + "_heartbeat_failed", {})
    return _H.emit_suppress()


def _wake(basis: str, counts: dict, *, casework: bool = False) -> int:
    payload = {"wakeAgent": True, "decision_basis": basis}
    if casework:
        payload["casework_expected"] = True
    if counts:
        payload["counts"] = counts
    print(json.dumps(payload, sort_keys=True))
    return 0


def plan_review(*, raw: dict, customer_yaml: dict, today: date, casework_events=None, escalation_events=None):
    """Pure-ish core: parse, join, plan. Returns ``(plan, problem, cm)``."""
    view, ledger = _sibling("casework_view.py"), _sibling("casework_ledger.py")
    cm = view.load_case_manager(customer_yaml)
    if cm is None or not (cm.own_level or cm.cleanup_level):
        return None, "case_manager_unauthored", cm
    snapshot, problem = _sibling("pull.py").parse_pull(raw)
    if snapshot is None:
        return None, problem or "pull_failed", cm
    esc = _H.load_sibling(_SKILL_DIRNAME, __file__, "escalation_ledger.py", "tlk_escalation_ledger")
    esc_events = escalation_events if escalation_events is not None else (esc.read_ledger() if esc else [])
    events = casework_events if casework_events is not None else ledger.read_ledger()
    raw_esc = customer_yaml.get("escalation")
    esc_block: dict = raw_esc if isinstance(raw_esc, dict) else {}
    review = _sibling("review.py")
    ctx = review._Ctx(
        snapshot=snapshot,
        cm=cm,
        ledger=ledger,
        states=ledger.derive_state(events),
        chaser_resolved=_chaser_resolved(esc, esc_events, snapshot),
        customer_yaml=customer_yaml,
        routing=_sibling("routing.py"),
        label=_sibling("digest_items.py").display_label,
        classify=_sibling("classify.py"),
        view=view,
        lines=_sibling("lines.py"),
        since=_sibling("done_since.py"),
        today=today,
        window_days=_H.pos_int(esc_block.get("escalation_window_days"), _DEFAULT_WINDOW_DAYS),
    )
    plan = review.build(ctx)
    plan.counts.update(
        {
            "open_tasks": snapshot.open_task_count,
            "overdue_tasks": len(snapshot.tasks),
            "matters_skipped": snapshot.matters_skipped,
        }
    )
    return plan, None, cm


async def run_once(
    *,
    pull_fn,
    customer_yaml: dict,
    writer_factory,
    today: date,
    now: datetime,
    casework_events=None,
    escalation_events=None,
) -> int:
    """Driver: plan, write the envelope + handoff, then wake or suppress."""
    try:
        raw = pull_fn()
    except Exception as exc:  # noqa: BLE001 - a failed read is a quiet run with its reason recorded
        return await _suppress(writer_factory, "pull_failed", {"problem": str(exc)[:300]}, now)
    plan, problem, _cm = plan_review(
        raw=raw,
        customer_yaml=customer_yaml,
        today=today,
        casework_events=casework_events,
        escalation_events=escalation_events,
    )
    if plan is None:
        basis = "case_manager_unauthored" if problem == "case_manager_unauthored" else "pull_failed"
        return await _suppress(writer_factory, basis, {"problem": problem}, now)
    counts = {**{k: v for k, v in plan.counts.items() if isinstance(v, int)}, "messages": len(plan.messages)}
    if not plan.messages:
        return await _suppress(writer_factory, "nothing_to_review", counts, now)
    _write_private_json(_SKILL_DIRNAME + ".json", _handoff(plan))
    if not _write_private_json(_SKILL_DIRNAME + ".casework.json", build_envelope(plan)):
        # The turn would wake to nothing; record why instead and stay quiet.
        return await _suppress(writer_factory, "casework_envelope_unwritten", counts, now)
    return _wake("task_review_ready", counts, casework=True)


def main() -> int:
    if not os.environ.get("CUSTOMER_SLUG"):
        sys.stderr.write("[pre_run] CUSTOMER_SLUG unset; waking so the miss is on record\n")
        return _wake("customer_slug_unset_fail_open", {})
    customer_yaml = _H.load_customer_yaml(None)
    now = datetime.now(timezone.utc)
    today = now.date()
    raw_esc = customer_yaml.get("escalation")
    esc: dict = raw_esc if isinstance(raw_esc, dict) else {}
    window = _H.pos_int(esc.get("escalation_window_days"), _DEFAULT_WINDOW_DAYS)
    writer_mod = _H.load_sibling(_SKILL_DIRNAME, __file__, "broker_writer.py", "tlk_broker_writer")
    factory = writer_mod.writer_factory if writer_mod is not None else (lambda: None)
    pull = _sibling("pull.py")
    try:
        return asyncio.run(
            run_once(
                pull_fn=lambda: pull.run_pull(today, window),
                customer_yaml=customer_yaml,
                writer_factory=factory,
                today=today,
                now=now,
            )
        )
    except Exception as exc:  # noqa: BLE001 - a crashed review wakes so the miss is on record
        sys.stderr.write(f"[pre_run] task-list-keeper pre_run failed ({exc}); waking\n")
        return _wake("pre_run_crashed_fail_open", {})


if __name__ == "__main__":
    sys.exit(main())
