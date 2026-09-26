"""The escalator on a case-manager seat: what it stops alarming on, what it
never stops alarming on, and the one line that replaces the overflow band.

The unconfigured path is pinned separately and byte-for-byte by
``test_case_manager_golden.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_SKILL = Path(__file__).resolve().parent


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _SKILL / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pre_run = _load("pre_run.py", "escalator_pre_run_cm")
_cw = _load("casework_ledger.py", "escalator_cw_ledger_cm")
_render = _load("render.py", "escalator_render_cm")

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
MATTER = "m-101"
LEGACY = "1ec31561-1136-4986-9692-1457dae9513a"


class _Source:
    def __init__(self, deadlines):
        self._deadlines = deadlines
        self.probe_stats = None

    def pull_deadlines(self):
        return self._deadlines


def _task(task_id, days_out, *, stamped=False, label="task-deadline", matter=MATTER):
    return _pre_run.MatterDeadline(
        matter_id=matter,
        matter_number="2026-PI-101",
        authored_date=TODAY + timedelta(days=days_out),
        label=label,
        task_id=task_id,
        operator_stamped=stamped,
    )


def _yaml(tmp_path: Path, case_manager: dict | None) -> Path:
    doc = {
        "escalation": {"red_flag_recipients": ["triage@firm.test"]},
        "personas": [{"cron": [{"skill": "task-list-keeper", "schedule": "37 6 * * 1"}]}],
    }
    if case_manager is not None:
        doc["case_manager"] = case_manager
    path = tmp_path / "customer.yaml"
    import yaml

    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


CM = {
    "own_tasks": {"level": "handles", "legacy_task_ids": [LEGACY]},
    "task_cleanup": {"level": "prepares", "keep_quiet_days": 30, "max_lines": 30},
}


def _run(tmp_path, monkeypatch, deadlines, case_manager, casework_rows=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(_yaml(tmp_path, case_manager)))
    ledger_file = tmp_path / "casework.jsonl"
    ledger_file.write_text("".join(json.dumps(r) + "\n" for r in casework_rows), encoding="utf-8")
    monkeypatch.setenv("SMD_CASEWORK_LEDGER_PATH", str(ledger_file))
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = asyncio.new_event_loop().run_until_complete(
            _pre_run.run_once(
                [_Source(deadlines)],
                _pre_run.EscalationWindows(),
                lambda: None,
                today=TODAY,
                now=NOW,
                fire_policy=_pre_run.FirePolicy(),
                ledger_events=[],
            )
        )
    assert code == 0
    wake = json.loads(buf.getvalue().strip().splitlines()[-1])
    env_path = tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json"
    envelope = json.loads(env_path.read_text()) if env_path.exists() else None
    return wake, envelope


def _row(kind, source_id, event, **extra):
    key = _cw.item_key(matter_id=MATTER, kind=kind, source_id=source_id)
    return {"item_key": key, "matter_id": MATTER, "kind": kind, "source_id": source_id, "event": event, **extra}


def _raise(kind, source_id, event="proposed", n=1, action="close"):
    return _row(
        kind,
        source_id,
        event,
        n=n,
        thread_ref="t-1",
        dispatch_ref="b" * 32,
        payload={"action": action, "class": "done", "evidence": []},
        ts="2026-09-21T14:00:00Z",
    )


def _task_ids(envelope) -> set:
    return {a.get("item_key") for d in (envelope or {}).get("dispatches", []) for a in d["appends"]}


def _key(task_id, label="task-deadline", days_out=-10):
    ledger = _pre_run._load_ledger_module()
    return ledger.item_key(MATTER, task_id, label, TODAY + timedelta(days=days_out))


def test_own_tasks_leave_the_digest_only_when_own_tasks_is_authored(tmp_path, monkeypatch):
    deadlines = [_task(LEGACY, -80), _task("t-stamped", -10, stamped=True), _task("t-firm", -5)]
    _wake, with_cm = _run(tmp_path / "a", monkeypatch, deadlines, CM)
    assert _task_ids(with_cm) == {_key("t-firm", days_out=-5)}
    _wake, without = _run(tmp_path / "b", monkeypatch, deadlines, None)
    assert len(_task_ids(without)) == 3


def test_a_task_in_the_review_leaves_the_digest(tmp_path, monkeypatch):
    rows = [_raise("task", "t-proposed")]
    deadlines = [_task("t-proposed", -20), _task("t-firm", -5)]
    wake, envelope = _run(tmp_path, monkeypatch, deadlines, CM, rows)
    assert _task_ids(envelope) == {_key("t-firm", days_out=-5)}
    assert wake["digest"]["task_review"] == {"day": "Monday"}


def test_everything_quiet_means_no_wake(tmp_path, monkeypatch):
    rows = [_raise("task", "t-proposed")]
    wake, envelope = _run(tmp_path, monkeypatch, [_task("t-proposed", -20)], CM, rows)
    # decide() found nothing to raise; with no heartbeat writer wired the
    # dev-mode fallback wakes bare, which is exactly the suppress branch.
    assert wake.get("decision_basis") == "no_audit_writer_fail_open"
    assert envelope is None


def test_a_briefed_court_date_is_quiet_until_the_backstop(tmp_path, monkeypatch):
    rows = [_raise("date", "ev-1", event="briefed", action="step")]
    rows[0]["payload"] = {"action": "keep", "class": "at_stake", "evidence": []}
    far = [_task("ev-1", 6, label="court-date")]
    _wake, quiet = _run(tmp_path / "far", monkeypatch, far, CM, rows)
    assert not _task_ids(quiet)
    near = [_task("ev-1", 2, label="court-date")]
    _wake, backstop = _run(tmp_path / "near", monkeypatch, near, CM, rows)
    assert _task_ids(backstop) == {_key("ev-1", "court-date", 2)}, (
        "an unanswered brief never silences the notify window"
    )
    answered = rows + [_row("date", "ev-1", "approved", n=1, thread_ref="t-1", ts="2026-09-22T14:00:00Z")]
    _wake, done = _run(tmp_path / "answered", monkeypatch, near, CM, answered)
    assert not _task_ids(done)


def test_overdue_tasks_past_the_top_five_become_one_review_line(tmp_path, monkeypatch):
    deadlines = [_task(f"t-{i}", -10 - i) for i in range(8)] + [
        _task("t-soon", 2),
        _task("ev-9", 5, label="court-date"),
    ]
    _wake, envelope = _run(tmp_path, monkeypatch, deadlines, CM)
    body = envelope["dispatches"][0]["full_body"]
    assert "5 more overdue tasks are in Monday's task review." not in body
    assert "3 more overdue tasks are in Monday's task review." in body
    # The band keeps what the review does not cover: the upcoming task and the court date.
    assert "## Also open (2 across 1 matter)" in body
    raised = _task_ids(envelope)
    assert len(raised) == 7  # five needs-you + the two still in the band; the three in review are not raised
    assert "—" not in body


def test_review_line_names_no_day_when_the_schedule_names_none():
    assert _render._task_review_block({"count": 2, "day": None}) == [
        "2 more overdue tasks are in the next task review.",
        "",
    ]
    assert (
        _render._task_review_block({"count": 1, "day": "Monday"})[0]
        == "1 more overdue task is in Monday's task review."
    )
    assert _render._task_review_block({"day": "Monday"}) == []


# ---------------------------------------------------------------------------
# Job 3: "Done since last time" in the daily digest
# ---------------------------------------------------------------------------

QUIET = {**CM, "quiet": {"level": "handles"}}
OTHER = "m-102"


def _closed(task_id="t-closed", matter=MATTER):
    key = _cw.item_key(matter_id=matter, kind="task", source_id=task_id)
    base = {"item_key": key, "matter_id": matter, "kind": "task", "source_id": task_id}
    payload = {"action": "close", "class": "done", "evidence": ["document:proof_of_service:2026-07-09"]}
    return [
        {**base, "event": "closed_by_record", "payload": payload, "ts": "2026-09-21T14:00:00Z"},
        {**base, "event": "completed", "tool_call_id": "call-1", "ts": "2026-09-21T14:01:00Z"},
    ]


def _stepped(event_id="ev-7", matter=MATTER):
    key = _cw.item_key(matter_id=matter, kind="date", source_id=event_id)
    step = {
        "catalog_id": "records_refresh:rt-7",
        "skill": "medical-records-chaser",
        "level": "handles",
        "params": {"provider": "Valley Imaging", "mode": "update", "newest_record": "2026-06-20"},
    }
    return [
        {
            "item_key": key,
            "matter_id": matter,
            "kind": "date",
            "source_id": event_id,
            "event": "step_ran",
            "payload": {"action": "step", "class": "open", "step": step},
            "tool_call_id": "call-memo",
            "ts": "2026-09-25T15:05:00Z",
        }
    ]


def _handoff(tmp_path):
    return json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.json").read_text())


def test_the_digest_says_what_was_done_since_last_time(tmp_path, monkeypatch):
    rows = _closed() + _stepped()
    wake, envelope = _run(tmp_path, monkeypatch, [_task("t-firm", -5)], QUIET, rows)
    dispatch = envelope["dispatches"][0]
    body = dispatch["full_body"]
    assert body.startswith(
        "Done since last time: matter 2026-PI-101: a task I closed on 2026-09-21, a proof of service "
        "dated 2026-07-09 was on file; matter 2026-PI-101: on 2026-09-25 I asked Valley Imaging for "
        "records dated after 2026-06-20.\n\n"
    )
    assert "Done since" not in dispatch["skeleton_body"], "the skeleton stays identifier-free"
    assert dispatch["casework_mentions"] == [
        {k: r[k] for k in ("item_key", "matter_id", "kind", "source_id")} for r in (rows[0], rows[2])
    ]
    assert "Done since last time" not in dispatch["subject"]
    assert "—" not in body
    # Every day a done line renders is seeded with its matter, so the send gate
    # traces it to this run's read.
    paired = {r["matterNumber"]: r["dates"] for r in _handoff(tmp_path)["records"]}
    for day in ("2026-09-21", "2026-07-09", "2026-09-25", "2026-06-20"):
        assert day in paired["2026-PI-101"], day
    assert [r["line"] for r in wake["digest"]["done_since"]][1].endswith("2026-06-20")


def test_done_since_needs_quiet_and_a_message_to_ride(tmp_path, monkeypatch):
    rows = _closed() + _stepped()
    _wake, no_quiet = _run(tmp_path / "a", monkeypatch, [_task("t-firm", -5)], CM, rows)
    assert "Done since" not in no_quiet["dispatches"][0]["full_body"]
    assert "casework_mentions" not in no_quiet["dispatches"][0]
    # Nothing else to say: a done line is never a message of its own.
    wake, alone = _run(tmp_path / "b", monkeypatch, [_task(LEGACY, -80)], QUIET, rows)
    assert alone is None or not alone.get("dispatches")


def test_a_matter_with_nothing_in_the_digest_rides_its_recipients_message(tmp_path, monkeypatch):
    other = _pre_run.MatterDeadline(
        matter_id=OTHER,
        matter_number="2026-PI-102",
        authored_date=TODAY - timedelta(days=80),
        label="task-deadline",
        task_id=LEGACY,
    )
    rows = _closed(matter=OTHER)
    _wake, envelope = _run(tmp_path, monkeypatch, [_task("t-firm", -5), other], QUIET, rows)
    dispatch = envelope["dispatches"][0]
    assert dispatch["full_body"].startswith("Done since last time: matter 2026-PI-102: a task I closed")
    assert [m["matter_id"] for m in dispatch["casework_mentions"]] == [OTHER]
    assert OTHER not in envelope["memo_matters"]
    assert not [u for u in envelope["unroutable"] if u["matter_id"] == OTHER]


def test_a_mentioned_item_is_not_told_again(tmp_path, monkeypatch):
    rows = _closed() + _stepped()
    told = [
        {**{k: r[k] for k in ("item_key", "matter_id", "kind", "source_id")}, "event": "mentioned"}
        | {"ts": "2026-09-26T13:00:00Z"}
        for r in (rows[0], rows[2])
    ]
    _wake, envelope = _run(tmp_path, monkeypatch, [_task("t-firm", -5)], QUIET, rows + told)
    assert "Done since" not in envelope["dispatches"][0]["full_body"]
