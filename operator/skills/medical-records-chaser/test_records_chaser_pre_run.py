"""Tests for medical-records-chaser/pre_run.py (ss #2404 ledger graduation).

Exercises the bespoke cadence gate that graduated this skill off the shared
empty-seat template: cadence suppression + refire, the plan STATE the email
must copy (attempt / last_chased / days_past_confirm_by — the Aug 11→18
Valley Imaging contradiction class), the unauthored-config sentinel, the
no-roster-tasks sentinel (zero marker matches must be loud, never quiet), the
ported matter-level hold (ss #2402), the stall-sentinel isolation (a stall
raise must not inflate the chase numerator), ledger-unavailable fire-open, and
the full-page truncation guard.

Mirrors `client-verification-tracker/test_verification_pre_run.py`.

Run from repo root:

    cd operator && python -m pytest \\
        skills/medical-records-chaser/test_records_chaser_pre_run.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

_PRE_RUN_PATH = _HERE.parent / "pre_run.py"
_spec = importlib.util.spec_from_file_location("mrc_pre_run", _PRE_RUN_PATH)
assert _spec is not None and _spec.loader is not None
_pre_run = importlib.util.module_from_spec(_spec)
sys.modules["mrc_pre_run"] = _pre_run
_spec.loader.exec_module(_pre_run)

ChaseConfig = _pre_run.ChaseConfig
RosterItem = _pre_run.RosterItem
RosterPull = _pre_run.RosterPull
decide = _pre_run.decide
run_once = _pre_run.run_once
load_chase_config = _pre_run.load_chase_config
parse_pull = _pre_run.parse_pull
ACTION_CHASE = _pre_run.ACTION_CHASE
ACTION_SURFACE_CONFIG = _pre_run.ACTION_SURFACE_CONFIG
ACTION_SURFACE_NO_ROSTER = _pre_run.ACTION_SURFACE_NO_ROSTER
ACTION_SURFACE_HOLD = _pre_run.ACTION_SURFACE_HOLD
HOLD_SOURCE_ID = _pre_run.HOLD_SOURCE_ID
STALL_SOURCE_PREFIX = _pre_run.STALL_SOURCE_PREFIX

# The vendored ledger — real events so tests exercise the true item_key join.
_LEDGER_PATH = _HERE.parent / "escalation_ledger.py"
_lspec = importlib.util.spec_from_file_location("mrc_ledger_test", _LEDGER_PATH)
_ledger = importlib.util.module_from_spec(_lspec)
sys.modules["mrc_ledger_test"] = _ledger
_lspec.loader.exec_module(_ledger)


# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------


class FakeSource:
    def __init__(self, pull: RosterPull):
        self._pull = pull

    def pull_open_roster_items(self) -> RosterPull:
        return self._pull


TODAY = date(2026, 8, 18)
_CFG = ChaseConfig(chase_cadence_days=7)
_REFIRE = 3


def _item(
    *,
    matter_id: str = "m-1",
    task_id: str | None = "task-1",
    confirm_by: date | None = None,
) -> RosterItem:
    return RosterItem(
        matter_id=matter_id,
        task_id=task_id,
        confirm_by=confirm_by or (TODAY - timedelta(days=38)),
    )


def _pull(*items: RosterItem, open_task_count: int | None = None) -> RosterPull:
    return RosterPull(
        items=tuple(items),
        open_task_count=len(items) if open_task_count is None else open_task_count,
    )


def _chased_event(item, *, ts, attempt):
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=item.matter_id,
        item_key=key,
        event="chased",
        attempt=attempt,
        token=_ledger.token_for(key),
        ts=ts,
    )


def _resolved_event(item, *, ts):
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=item.matter_id,
        item_key=key,
        event="resolved",
        attempt=0,
        ts=ts,
    )


def _hold_event(item, *, event="fired", ts, attempt=1):
    key = _ledger.item_key(item.matter_id, HOLD_SOURCE_ID, "mrc-chase-hold", None)
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=item.matter_id,
        item_key=key,
        event=event,
        attempt=attempt,
        ts=ts,
    )


def _stall_event(item, *, ts, attempt=1):
    key = _ledger.item_key(item.matter_id, STALL_SOURCE_PREFIX + str(item.task_id), "mrc-stall", None)
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=item.matter_id,
        item_key=key,
        event="fired",
        attempt=attempt,
        ts=ts,
    )


def _decide(pull, events, *, config=_CFG, today=TODAY):
    return decide(
        pull,
        config,
        _ledger,
        events,
        raw_inputs_for_digest=b"x",
        today=today,
        refire_days=_REFIRE,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _capture_stdout(coro) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _run(coro)
    return code, buf.getvalue().strip()


# ---------------------------------------------------------------------------
# decide() — cadence + the plan state the email copies (condition a)
# ---------------------------------------------------------------------------


def test_new_item_first_chase_due_when_confirm_by_arrived():
    d = _decide(_pull(_item()), [])
    assert d.wake is True
    (plan,) = d.plans
    assert plan.action == ACTION_CHASE
    assert plan.attempt == 1
    assert plan.last_chased is None  # no ledger history: the email must NOT
    # claim "no prior chase" (memos may hold pre-ledger chases) — SKILL.md rule
    assert plan.days_past_confirm_by == 38


def test_new_item_not_due_before_confirm_by():
    d = _decide(_pull(_item(confirm_by=TODAY + timedelta(days=3))), [])
    assert d.wake is False


def test_chase_suppressed_within_cadence_window():
    item = _item()
    events = [_chased_event(item, ts="2026-08-15T09:00:00.000Z", attempt=1)]
    d = _decide(_pull(item), events)
    assert d.wake is False
    assert d.decision_basis == "no_records_chase_due"


def test_chase_refires_after_cadence_with_ledger_state_in_plan():
    # THE defect test (Aug 11 → Aug 18): the second wake's plan must carry the
    # first chase's date and the correct attempt number — the email copies
    # these, so consecutive emails cannot contradict each other.
    item = _item()
    events = [_chased_event(item, ts="2026-08-11T15:11:00.000Z", attempt=1)]
    d = _decide(_pull(item), events)
    assert d.wake is True
    (plan,) = d.plans
    assert plan.action == ACTION_CHASE
    assert plan.attempt == 2
    assert plan.last_chased == "2026-08-11"  # never "July 13", never recall
    assert plan.days_past_confirm_by == 38


def test_attempt_numbering_counts_prior_chases():
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-28T09:00:00.000Z", attempt=1),
        _chased_event(item, ts="2026-08-04T09:00:00.000Z", attempt=2),
        _chased_event(item, ts="2026-08-11T09:00:00.000Z", attempt=3),
    ]
    d = _decide(_pull(item), events)
    (plan,) = d.plans
    assert plan.attempt == 4
    assert plan.last_chased == "2026-08-11"


def test_stall_raise_on_its_sentinel_does_not_inflate_the_chase_numerator():
    # A stall escalation is keyed on STALL_SOURCE_PREFIX+task, never the chase
    # item key — otherwise "chase N" drifts (critique issue 4).
    item = _item()
    events = [
        _chased_event(item, ts="2026-08-04T09:00:00.000Z", attempt=1),
        _stall_event(item, ts="2026-08-05T09:00:00.000Z"),
    ]
    d = _decide(_pull(item), events, today=date(2026, 8, 12))
    (plan,) = d.plans
    assert plan.attempt == 2  # not 3


def test_resolved_item_is_terminal():
    item = _item()
    events = [
        _chased_event(item, ts="2026-08-04T09:00:00.000Z", attempt=1),
        _resolved_event(item, ts="2026-08-10T09:00:00.000Z"),
    ]
    d = _decide(_pull(item), events)
    assert d.wake is False


def test_days_past_confirm_by_never_negative():
    item = _item(confirm_by=TODAY)
    d = _decide(_pull(item), [])
    (plan,) = d.plans
    assert plan.days_past_confirm_by == 0


# ---------------------------------------------------------------------------
# decide() — unauthored config (condition c)
# ---------------------------------------------------------------------------


def _config_sentinel_fired(*, ts, attempt=1):
    key = _ledger.item_key("", "__mrc_chase_config__", "mrc-chase-config-missing", "")
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=None,
        item_key=key,
        event="fired",
        attempt=attempt,
        ts=ts,
    )


def test_unauthored_config_surfaces_and_never_chases():
    d = _decide(_pull(_item()), [], config=ChaseConfig())
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_CONFIG]


def test_unauthored_config_quiet_within_refire_window():
    d = _decide(
        _pull(_item()),
        [_config_sentinel_fired(ts="2026-08-16T09:00:00.000Z")],
        config=ChaseConfig(),
    )
    assert d.wake is False
    assert d.decision_basis == "chase_config_unauthored_within_refire_window"


def test_unauthored_config_resurfaces_after_refire_window():
    d = _decide(
        _pull(_item()),
        [_config_sentinel_fired(ts="2026-08-14T09:00:00.000Z")],
        config=ChaseConfig(),
    )
    assert d.wake is True
    assert d.plans[0].attempt == 2


def test_verification_trackers_config_sentinel_does_not_collide():
    # The sibling skill's config sentinel uses source "__chase_config__";
    # ours is "__mrc_chase_config__". An ack on THEIRS must not snooze OURS —
    # item_key ignores label, so distinct source_ids are the only fence.
    their_key = _ledger.item_key("", "__chase_config__", "chase-config-missing", "")
    events = [
        _ledger.make_event(
            skill="client-verification-tracker",
            matter_id=None,
            item_key=their_key,
            event="fired",
            attempt=1,
            ts="2026-08-17T09:00:00.000Z",
        ),
    ]
    d = _decide(_pull(_item()), events, config=ChaseConfig())
    assert d.wake is True  # our sentinel has no history; it surfaces
    assert d.plans[0].attempt == 1


# ---------------------------------------------------------------------------
# decide() — no roster tasks (condition e): zero marker matches must be LOUD
# ---------------------------------------------------------------------------


def _no_roster_sentinel_fired(*, ts, attempt=1):
    key = _ledger.item_key("", "__mrc_no_roster__", "mrc-no-roster-tasks", "")
    return _ledger.make_event(
        skill="medical-records-chaser",
        matter_id=None,
        item_key=key,
        event="fired",
        attempt=attempt,
        ts=ts,
    )


def test_open_tasks_but_zero_marker_matches_surfaces():
    d = _decide(RosterPull(items=(), open_task_count=12), [])
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_NO_ROSTER]


def test_no_roster_surface_respects_refire_window():
    d = _decide(
        RosterPull(items=(), open_task_count=12),
        [_no_roster_sentinel_fired(ts="2026-08-17T09:00:00.000Z")],
    )
    assert d.wake is False
    assert d.decision_basis == "no_roster_tasks_within_refire_window"


def test_truly_empty_seat_suppresses_quietly():
    d = _decide(RosterPull(items=(), open_task_count=0), [])
    assert d.wake is False
    assert d.decision_basis == "no_records_chase_due"


# ---------------------------------------------------------------------------
# decide() — matter-level hold (condition d, the ss #2402 rule ported)
# ---------------------------------------------------------------------------


def test_hold_blocks_a_due_chase_and_resurfaces():
    item = _item()
    events = [_hold_event(item, ts="2026-08-14T09:00:00.000Z")]
    d = _decide(_pull(item), events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]


def test_hold_quiet_within_refire_window_still_blocks():
    item = _item()
    events = [_hold_event(item, ts="2026-08-17T09:00:00.000Z")]
    d = _decide(_pull(item), events)
    assert d.wake is False


def test_resolved_hold_releases_the_chase():
    item = _item()
    events = [
        _hold_event(item, ts="2026-08-10T09:00:00.000Z"),
        _hold_event(item, event="resolved", ts="2026-08-16T09:00:00.000Z"),
    ]
    d = _decide(_pull(item), events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_CHASE]


def test_hold_survives_tracking_task_recreation():
    original = _item(task_id="task-1")
    events = [_hold_event(original, ts="2026-08-17T09:00:00.000Z")]
    replacement = _item(task_id="task-9")
    d = _decide(_pull(replacement), events)
    assert d.wake is False  # held and inside the refire window: quiet, no chase


def test_verification_hold_does_not_block_records_chases():
    # The sibling's hold sentinel is "__hold__"; ours is "__mrc_hold__". A
    # signer-ambiguity hold on the matter must not silence the records chase —
    # distinct source_ids are the fence (item_key ignores label and skill).
    item = _item()
    their_hold_key = _ledger.item_key(item.matter_id, "__hold__", "chase-hold", None)
    events = [
        _ledger.make_event(
            skill="client-verification-tracker",
            matter_id=item.matter_id,
            item_key=their_hold_key,
            event="fired",
            attempt=1,
            ts="2026-08-14T09:00:00.000Z",
        ),
    ]
    d = _decide(_pull(item), events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_CHASE]


def test_held_matter_with_two_items_surfaces_once():
    a = _item(task_id="task-1")
    b = _item(task_id="task-2")
    events = [_hold_event(a, ts="2026-08-14T09:00:00.000Z")]
    d = _decide(_pull(a, b), events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]


# ---------------------------------------------------------------------------
# run_once() — wake line carries the plan state; fail-open on ledger loss
# ---------------------------------------------------------------------------


class _NullWriterFactory:
    def __call__(self):
        return None


def test_run_once_wake_line_carries_the_email_state():
    item = _item()
    events = [_chased_event(item, ts="2026-08-11T15:11:00.000Z", attempt=1)]
    code, out = _capture_stdout(
        run_once(
            [FakeSource(_pull(item))],
            _NullWriterFactory(),
            today=TODAY,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
        )
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    (plan,) = payload["plans"]
    assert plan["attempt"] == 2
    assert plan["last_chased"] == "2026-08-11"
    assert plan["days_past_confirm_by"] == 38


def test_run_once_fires_open_when_ledger_unavailable(monkeypatch):
    monkeypatch.setattr(_pre_run, "_load_ledger_module", lambda: None)
    code, out = _capture_stdout(
        run_once(
            [FakeSource(_pull(_item()))],
            _NullWriterFactory(),
            today=TODAY,
            config=_CFG,
            refire_days=_REFIRE,
        )
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    assert payload["decision_basis"] == "ledger_unavailable_fail_open"


def test_run_once_no_writer_falls_back_to_wake_on_suppress():
    # Nothing due, but no heartbeat writer → mirror-don't-gate: wake.
    code, out = _capture_stdout(
        run_once(
            [FakeSource(RosterPull((), 0))],
            _NullWriterFactory(),
            today=TODAY,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    assert payload["decision_basis"] == "no_audit_writer_fail_open"


# ---------------------------------------------------------------------------
# parse_pull()
# ---------------------------------------------------------------------------


def _task(subject: str, *, task_id="t-1", matter="m-1", due="2026-07-11"):
    return {"id": task_id, "subject": subject, "matter": {"id": matter}, "dueDate": due}


def test_parse_pull_subsets_roster_tasks_by_marker():
    raw = {
        "tasks": [
            _task("Medical records outstanding - Valley Imaging Center (request roster)"),
            _task("Client verification outstanding - FROG responses", task_id="t-2"),
            _task("Chase Medi-Cal lien payoff", task_id="t-3"),
        ]
    }
    pull, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert pull.open_task_count == 3
    assert len(pull.items) == 1
    assert pull.items[0].task_id == "t-1"
    assert pull.items[0].matter_id == "m-1"
    assert pull.items[0].confirm_by == date(2026, 7, 11)


def test_parse_pull_excludes_probe_artifacts():
    # ss #2403: a probe task is never a roster item; a real roster task quoting
    # the marker mid-subject is kept (position-anchored match).
    raw = {
        "tasks": [
            _task(
                "[Operator] [SMD-PROBE 2026-08-18T14:00Z] records (request roster)",
                task_id="t-p",
            ),
            _task("Medical records outstanding - Valley Imaging Center (request roster)"),
        ]
    }
    pull, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert [i.task_id for i in pull.items] == ["t-1"]


def test_parse_pull_dateless_roster_task_seeds_today():
    raw = {"tasks": [{"id": "t-1", "subject": "x (request roster)", "matter": {"id": "m-1"}}]}
    pull, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert pull.items[0].confirm_by == TODAY


def test_parse_pull_error_key_is_a_problem():
    pull, problem = parse_pull({"tasksError": "boom"}, today=TODAY)
    assert problem is not None


def test_parse_pull_unrecognized_envelope_is_a_problem():
    pull, problem = parse_pull({"tasks": {"weird": True}}, today=TODAY)
    assert problem is not None


def test_parse_pull_full_page_is_a_problem():
    # A full 500-row page may be truncated; trusting the subset could silently
    # drop providers (critique issue 7). Fail open instead.
    raw = {"tasks": [_task(f"x (request roster)", task_id=f"t-{i}") for i in range(500)]}
    pull, problem = parse_pull(raw, today=TODAY)
    assert problem is not None
    assert "full page" in problem


# ---------------------------------------------------------------------------
# load_chase_config()
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path, body: str):
    path = tmp_path / "customer.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_load_chase_config_reads_per_skill_settings(tmp_path, monkeypatch):
    path = _write_yaml(
        tmp_path,
        """
personas:
  - name: operator
    skills:
      - name: medical-records-chaser
        settings:
          chase_cadence_days: 7
escalation:
  refire_days: 4
""",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", path)
    config, refire = load_chase_config()
    assert config.chase_cadence_days == 7
    assert config.authored is True
    assert refire == 4


def test_load_chase_config_unauthored_when_settings_absent(tmp_path, monkeypatch):
    path = _write_yaml(
        tmp_path,
        """
personas:
  - name: operator
    skills:
      - name: medical-records-chaser
""",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", path)
    config, _ = load_chase_config()
    assert config.authored is False


def test_load_chase_config_unauthored_on_missing_file(monkeypatch):
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    config, _ = load_chase_config()
    assert config.authored is False


def test_load_chase_config_rejects_nonpositive(tmp_path, monkeypatch):
    path = _write_yaml(
        tmp_path,
        """
personas:
  - name: operator
    skills:
      - name: medical-records-chaser
        settings:
          chase_cadence_days: 0
""",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", path)
    config, _ = load_chase_config()
    assert config.authored is False


# ---------------------------------------------------------------------------
# Pre-run handoff (ss#2547)
# ---------------------------------------------------------------------------
# The same block every bespoke pre_run carries. This skill emits NO authored
# date today (the confirm-by date is an admin date, so `authored_date` is None
# in production), and the projection is asserted to be empty for exactly that
# reason: an empty `dates` list is the honest reading of what the wake line
# said, and the day this gate starts emitting a date the assertion below starts
# failing rather than silently seeding nothing.

_HANDOFF_KEYS = {"skill", "started_at", "dates", "matter_ids"}


def _authored_dates_in(node, found=None) -> list:
    """Every authored_date in the wake payload, first-seen order.

    Written out again here rather than calling the module's own walker: a test
    that reuses the projection it is checking agrees with that projection's bugs.
    """
    if found is None:
        found = []
    if isinstance(node, dict):
        value = node.get("authored_date")
        if isinstance(value, str) and value and value not in found:
            found.append(value)
        for child in node.values():
            _authored_dates_in(child, found)
    elif isinstance(node, list):
        for child in node:
            _authored_dates_in(child, found)
    return found


def _wake_stdout() -> str:
    code, out = _capture_stdout(
        run_once(
            [FakeSource(_pull(_item()))],
            _NullWriterFactory(),
            today=TODAY,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    return out


def _handoff_path(home) -> Path:
    return Path(home) / ".smd" / "pre_run" / "medical-records-chaser.json"


def test_the_wake_writes_a_handoff_projecting_what_it_emitted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = json.loads(_wake_stdout())
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert record["dates"] == _authored_dates_in(payload) == []
    assert record["skill"] == "medical-records-chaser"
    assert record["matter_ids"] == [p["matter_id"] for p in payload["plans"]]


def test_the_handoff_carries_nothing_but_the_projection(tmp_path, monkeypatch) -> None:
    """Item keys, actions, chase state and prose never reach the register.

    `last_chased` in particular is deliberately NOT projected: it is the
    Operator's own ledger timestamp, and a script may certify what it read from
    the firm's record, never what the Operator wrote about itself.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _wake_stdout()
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert set(record) == _HANDOFF_KEYS
    assert record["started_at"].endswith("Z")
    datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))


def test_a_handoff_write_failure_leaves_stdout_byte_identical(tmp_path, monkeypatch) -> None:
    """HERMES_HOME is a FILE, so the write fails for any uid. A read-only
    directory would still be writable by root, and CI containers run as root."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    good = _wake_stdout()
    assert _handoff_path(tmp_path).exists()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(blocked))
    assert _wake_stdout() == good
