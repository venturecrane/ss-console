"""Tests for deadline-miss-escalator/pre_run.py (ADR 0021 Stream B).

Exercises the wake / suppress decision, the rung mapping, the SUPPRESSED_WAKE
heartbeat emission, and the mirror-don't-gate fallback (audit failure → wake —
the dead-man's-switch the plan critique flagged). Fake source + fake executor;
no network, no OAuth, no D1.

Mirrors `retainer-hours-reconciler/test_retainer_pre_run.py` — same harness,
adapted for authored-deadline proximity instead of utilization buckets.

Run from repo root:

    cd operator && python -m pytest \\
        skills/deadline-miss-escalator/test_escalator_pre_run.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout

import pytest
from datetime import date, datetime, timezone
from pathlib import Path

# operator/ on sys.path (for adapter.*). Load this skill's pre_run.py under a
# unique module name — every Stream B skill names the file pre_run.py, so a bare
# import would collide across skills.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from adapter.audit_log import AuditLogWriter, SuppressedWakeWriter  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

_PRE_RUN_PATH = _HERE.parent / "pre_run.py"
_spec = importlib.util.spec_from_file_location("deadline_escalator_pre_run", _PRE_RUN_PATH)
assert _spec is not None and _spec.loader is not None
_pre_run = importlib.util.module_from_spec(_spec)
sys.modules["deadline_escalator_pre_run"] = _pre_run
_spec.loader.exec_module(_pre_run)

EscalationWindows = _pre_run.EscalationWindows
MatterDeadline = _pre_run.MatterDeadline
decide = _pre_run.decide
run_once = _pre_run.run_once
_rung_for = _pre_run._rung_for


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSource:
    def __init__(self, deadlines):
        self._deadlines = deadlines

    def pull_deadlines(self):
        return self._deadlines


class FakeExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list]] = []

    async def execute(self, sql: str, params: list) -> None:
        self.calls.append((sql, params))
        if self.fail:
            raise RuntimeError("D1 unreachable")


TODAY = date(2026, 6, 8)
NOW = datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc)


def _dl(
    *,
    matter_id: str = "7001",
    days_out: int = 30,
    label: str = "filing-deadline",
    matter_open: bool = True,
    conflict_hold: bool = False,
    acknowledged: bool = False,
    acked: bool = False,
    task_id: str | None = None,
    last_raised: str | None = None,
    matter_number: str | None = None,
    matter_number_absent: str | None = None,
) -> MatterDeadline:
    from datetime import timedelta

    return MatterDeadline(
        matter_id=matter_id,
        authored_date=TODAY + timedelta(days=days_out),
        label=label,
        matter_open=matter_open,
        conflict_hold=conflict_hold,
        acknowledged=acknowledged,
        acked=acked,
        task_id=task_id,
        last_raised=last_raised,
        matter_number=matter_number,
        matter_number_absent=matter_number_absent,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _capture_stdout(coro) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _run(coro)
    return code, buf.getvalue().strip()


# ---------------------------------------------------------------------------
# decide() — pure
# ---------------------------------------------------------------------------


def test_decide_suppresses_when_all_far_off():
    # 30 days out, window 14 → not in range.
    decision = decide([_dl(days_out=30)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY)
    assert decision.wake is False
    assert decision.decision_basis == "no_deadline_in_escalation_range"


def test_decide_wakes_when_within_window():
    decision = decide([_dl(days_out=10)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY)
    assert decision.wake is True
    assert decision.decision_basis == "deadline_in_escalation_range"
    assert decision.extra_metadata["matters"][0]["matter_id"] == "7001"


def test_decide_wakes_on_overdue():
    decision = decide([_dl(days_out=-3)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY)
    assert decision.wake is True
    assert decision.extra_metadata["matters"][0]["days_out"] == -3


def test_decide_suppresses_when_acknowledged():
    # In-range but already acknowledged → stop re-firing.
    decision = decide(
        [_dl(days_out=5, acknowledged=True)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY
    )
    assert decision.wake is False


def test_decide_suppresses_when_matter_closed():
    decision = decide(
        [_dl(days_out=2, matter_open=False)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY
    )
    assert decision.wake is False


def test_decide_wakes_on_held_matter_in_range():
    decision = decide(
        [_dl(days_out=5, conflict_hold=True)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY
    )
    assert decision.wake is True
    assert decision.extra_metadata["matters"][0]["rung"] == "clearance"


def test_decide_aggregates_multiple_in_range():
    decision = decide(
        [_dl(matter_id="a", days_out=2), _dl(matter_id="b", days_out=40), _dl(matter_id="c", days_out=12)],
        EscalationWindows(),
        raw_inputs_for_digest=b"x",
        today=TODAY,
    )
    assert decision.wake is True
    ids = {m["matter_id"] for m in decision.extra_metadata["matters"]}
    assert ids == {"a", "c"}  # b is 40 days out, outside the 14-day window


# ---------------------------------------------------------------------------
# rung mapping
# ---------------------------------------------------------------------------


def test_rung_notify_within_3_days_or_overdue():
    assert _rung_for(_dl(days_out=2), TODAY, EscalationWindows()) == "notify"
    assert _rung_for(_dl(days_out=-1), TODAY, EscalationWindows()) == "notify"


def test_rung_re_route_within_near_window():
    assert _rung_for(_dl(days_out=6), TODAY, EscalationWindows()) == "re-route"


def test_rung_re_surface_in_outer_window():
    assert _rung_for(_dl(days_out=12), TODAY, EscalationWindows()) == "re-surface"


def test_rung_clearance_for_held_regardless_of_proximity():
    assert _rung_for(_dl(days_out=1, conflict_hold=True), TODAY, EscalationWindows()) == "clearance"


# ---------------------------------------------------------------------------
# run_once() — integration
# ---------------------------------------------------------------------------


def test_run_once_wakes_on_in_range_no_audit_written(tmp_path, monkeypatch):
    # HERMES_HOME pinned so the dispatch-envelope write is DETERMINISTIC
    # (WS-RENDER): unpinned, the default /opt/data is unwritable on a dev
    # laptop and writable in the root CI container, and dispatch_expected
    # flipped between the two. With no red-flag recipient authored the matter
    # is unroutable, so the envelope carries only the memo duty.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    sources = [FakeSource([_dl(days_out=5, task_id="task-9")])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    # The wake line carries the facts the gate computed (#2253). A bare
    # wakeAgent flag left the woken turn to source per-item facts itself, and
    # with the connector down it sourced them from nowhere.
    payload = json.loads(out)
    digest = payload.pop("digest")  # ss #2405: asserted separately below
    assert payload.pop("dispatch_expected") is True  # the envelope landed
    assert payload == {
        "wakeAgent": True,
        "decision_basis": "deadline_in_escalation_range",
        "plans": [
            {
                "matter_id": "7001",
                "matter_number": None,  # FakeSource authored no number
                "matter_number_absent": None,
                "task_id": "task-9",
                "label": "filing-deadline",
                "authored_date": "2026-06-13",  # verbatim, not re-derived from days_out
                "days_out": 5,
                "rung": "re-route",
                "last_raised": None,  # never raised → no Operator raise on record
                "last_raised_source": "operator_ledger",
            }
        ],
        "plans_total": 1,
        "plans_emitted": 1,
        "plans_truncated": False,
    }
    assert digest["subject"].startswith("[Deadlines] 1 need you")
    # The wake leaves a row too (#2253). Before this, the gate logged why it did
    # NOT act and logged nothing when it did — which is why the 2026-08-10
    # fabricated escalation email was findable only by reading the mailbox.
    assert len(executor.calls) == 1
    _, params = executor.calls[0]
    assert params[2] == "EMITTED_WAKE"
    assert params[5] == "deadline-miss-escalator"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "deadline_in_escalation_range"
    # The row's plan accounting matches the wake line's, field for field.
    assert metadata["plans_total"] == 1
    assert metadata["plans_emitted"] == 1
    assert metadata["plans_truncated"] is False


def test_run_once_wake_is_unchanged_when_the_emitted_wake_write_fails(tmp_path, monkeypatch):
    """The inverted contract: a failed audit write must not touch the wake.

    On the suppress path an audit failure escalates to a wake, because a silent
    suppress is indistinguishable from a broken gate. Here the wake is already
    the decision, so the row is observability and never a gate — the stdout must
    be byte-identical to the succeeding case above.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # see the succeeding case
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    sources = [FakeSource([_dl(days_out=5, task_id="task-9")])]
    executor = FakeExecutor(fail=True)

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    payload = json.loads(out)
    payload.pop("digest")  # ss #2405: same digest as the succeeding case
    assert payload.pop("dispatch_expected") is True
    assert payload == {
        "wakeAgent": True,
        "decision_basis": "deadline_in_escalation_range",
        "plans": [
            {
                "matter_id": "7001",
                "matter_number": None,
                "matter_number_absent": None,
                "task_id": "task-9",
                "label": "filing-deadline",
                "authored_date": "2026-06-13",
                "days_out": 5,
                "rung": "re-route",
                "last_raised": None,
                "last_raised_source": "operator_ledger",
            }
        ],
        "plans_total": 1,
        "plans_emitted": 1,
        "plans_truncated": False,
    }
    assert len(executor.calls) == 1  # attempted, failed, swallowed


def test_run_once_wake_survives_a_writer_without_the_emitted_wake_method():
    """A writer object too old to have `write_emitted_wake` must not break a
    wake. The failure mode this closes is a half-deployed image, where the
    gate's own observability would otherwise take the tick down with it."""

    class _LegacyWriter:
        async def write_suppressed_wake(self, **_kwargs) -> str:
            return "x"

    sources = [FakeSource([_dl(days_out=5, task_id="task-9")])]
    code, out = _capture_stdout(run_once(sources, EscalationWindows(), lambda: _LegacyWriter(), today=TODAY, now=NOW))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "deadline_in_escalation_range"
    assert len(parsed["plans"]) == 1


def test_run_once_writes_heartbeat_then_suppresses_when_quiet():
    sources = [FakeSource([_dl(days_out=40)])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}
    assert len(executor.calls) == 1  # the SUPPRESSED_WAKE heartbeat row
    sql, params = executor.calls[0]
    assert sql.startswith("INSERT INTO audit_log")
    assert params[2] == "SUPPRESSED_WAKE"
    assert params[5] == "deadline-miss-escalator"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "no_deadline_in_escalation_range"


def test_run_once_falls_back_to_wake_on_audit_failure():
    """The dead-man's-switch: audit-write failure forces wake, so a silently
    broken heartbeat surfaces as the agent waking rather than going dark.

    Asserted by exact equality with NO ``plans`` key: pre-#2253 every wake path
    printed the same bare flag, so a blind fail-open and a fact-carrying wake
    were indistinguishable on the wire — which is precisely how a fact-free turn
    could read as a well-briefed one.
    """
    sources = [FakeSource([_dl(days_out=40)])]
    executor = FakeExecutor(fail=True)

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    parsed = json.loads(out)
    assert parsed == {
        "wakeAgent": True,
        "decision_basis": "suppress_heartbeat_failed_fail_open",
    }
    assert "plans" not in parsed  # woke blind: SKILL.md's enumeration fallback applies
    assert len(executor.calls) == 1  # attempt made before fallback


def test_run_once_falls_back_to_wake_when_no_writer():
    sources = [FakeSource([_dl(days_out=40)])]
    code, out = _capture_stdout(run_once(sources, EscalationWindows(), lambda: None, today=TODAY, now=NOW))
    assert code == 0
    parsed = json.loads(out)
    assert parsed == {
        "wakeAgent": True,
        "decision_basis": "no_audit_writer_fail_open",
    }
    assert "plans" not in parsed


# ---------------------------------------------------------------------------
# parse_pull — the production Smokeball pull parser (#1748 wiring)
# ---------------------------------------------------------------------------

parse_pull = _pre_run.parse_pull


def test_parse_pull_clean_tasks_and_events() -> None:
    raw = {
        "tasks": {"items": [{"matterId": "m-1", "dueDate": "2026-07-20T00:00:00Z"}]},
        "events": {"items": [{"matterId": "m-2", "startTime": "2026-07-09T09:00:00"}]},
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert {(d.matter_id, d.label, d.authored_date.isoformat()) for d in deadlines} == {
        ("m-1", "task-deadline", "2026-07-20"),
        ("m-2", "court-date", "2026-07-09"),
    }


def test_parse_pull_reads_nested_matter_link_object() -> None:
    # The live Smokeball /tasks payload nests the matter as a link object —
    # the flat-key miss put "unknown-matter" (or worse, the task's own id via
    # the bare-"id" fallback) into item identity (WP-D probe find, ss #1915).
    raw = {
        "tasks": {
            "items": [
                {
                    "id": "t-1",
                    "matter": {"id": "m-real", "href": "https://api/matters/m-real"},
                    "dueDate": "2026-07-20T00:00:00Z",
                }
            ]
        },
        "events": [],
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert deadlines[0].matter_id == "m-real"


def test_parse_pull_bare_list_envelope() -> None:
    raw = {"tasks": [{"matterId": "m-1", "dueDate": "2026-07-20"}], "events": []}
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert len(deadlines) == 1


def test_parse_pull_excludes_probe_artifacts() -> None:
    # ss #2403: a rehearsal probe task is never a deadline, [Operator]-stamped
    # or not. But a real task QUOTING the marker mid-subject is kept — the
    # match is position-anchored so subject text cannot silence a deadline.
    raw = {
        "tasks": [
            {
                "id": "t-p",
                "matterId": "m-1",
                "subject": "[Operator] [SMD-PROBE 2026-08-18T14:00Z] drafting prove-out",
                "dueDate": "2026-07-20",
            },
            {
                "id": "t-p2",
                "matterId": "m-1",
                "subject": "[SMD-PROBE 2026-08-18T14:00Z] unstamped probe",
                "dueDate": "2026-07-20",
            },
            {
                "id": "t-r",
                "matterId": "m-1",
                "subject": "Review the [SMD-PROBE] cleanup contract",
                "dueDate": "2026-07-21",
            },
        ],
        "events": [],
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert [(d.task_id, d.authored_date.isoformat()) for d in deadlines] == [("t-r", "2026-07-21")]


def test_parse_pull_error_key_is_a_problem() -> None:
    raw = {"tasks": {"items": []}, "events": {"items": []}, "eventsError": "boom"}
    deadlines, problem, _probe = parse_pull(raw)
    assert deadlines == [] and problem is not None


def test_parse_pull_unrecognized_envelope_is_a_problem() -> None:
    deadlines, problem, _probe = parse_pull({"tasks": {"weird": 1}, "events": {"items": []}})
    assert deadlines == [] and problem is not None


def test_parse_pull_nonempty_pull_with_zero_dates_is_a_problem() -> None:
    """A wire shape whose date keys we don't recognize must WAKE, not read as
    an empty deadline book."""
    raw = {
        "tasks": {"items": [{"matterId": "m-1", "deadline_when": "2026-07-20"}]},
        "events": {"items": []},
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert deadlines == [] and problem is not None


def test_parse_pull_dateless_items_skipped_when_others_parse() -> None:
    raw = {
        "tasks": {
            "items": [
                {"matterId": "m-1", "dueDate": "2026-07-20"},
                {"matterId": "m-2"},  # dateless task: not an authored deadline
            ]
        },
        "events": {"items": []},
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert [d.matter_id for d in deadlines] == ["m-1"]


def test_parse_pull_empty_pull_is_a_clean_empty_book() -> None:
    deadlines, problem, _probe = parse_pull({"tasks": {"items": []}, "events": {"items": []}})
    assert deadlines == [] and problem is None


def test_parse_pull_carries_stable_task_id() -> None:
    raw = {
        "tasks": {"items": [{"matterId": "m-1", "id": "task-77", "dueDate": "2026-07-20"}]},
        "events": {"items": []},
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert deadlines[0].task_id == "task-77"


def test_parse_pull_idless_item_has_no_task_id() -> None:
    # An event with a date but no id key: still a deadline, but blanket-ack only.
    raw = {
        "tasks": {"items": []},
        "events": {"items": [{"matterId": "m-2", "startTime": "2026-07-09T09:00:00"}]},
    }
    deadlines, problem, _probe = parse_pull(raw)
    assert problem is None
    assert deadlines[0].task_id is None


# ---------------------------------------------------------------------------
# Ledger-aware re-fire policy (the daily-re-fire fix) — run_once + enrich
# ---------------------------------------------------------------------------

FirePolicy = _pre_run.FirePolicy
enrich_with_ledger = _pre_run.enrich_with_ledger
load_escalation_config = _pre_run.load_escalation_config

# The vendored ledger module the skill loads at runtime — used here to mint
# real events so the tests exercise the true item_key/state join.
import importlib.util as _il  # noqa: E402 - imported beside the ledger loader it serves, after the pre_run module is loaded above

_LEDGER_PATH = _HERE.parent / "escalation_ledger.py"
_lspec = _il.spec_from_file_location("escalation_ledger_test", _LEDGER_PATH)
_ledger = _il.module_from_spec(_lspec)
sys.modules["escalation_ledger_test"] = _ledger
_lspec.loader.exec_module(_ledger)


def _fired_event(dl, *, ts, attempt=1):
    key = _ledger.item_key(dl.matter_id, dl.task_id, dl.label, dl.authored_date)
    return _ledger.make_event(
        skill="deadline-miss-escalator",
        matter_id=dl.matter_id,
        item_key=key,
        event="fired",
        attempt=attempt,
        token=_ledger.token_for(key),
        ts=ts,
    )


def _acked_event(dl, *, ts):
    key = _ledger.item_key(dl.matter_id, dl.task_id, dl.label, dl.authored_date)
    return _ledger.make_event(
        skill="deadline-miss-escalator",
        matter_id=dl.matter_id,
        item_key=key,
        event="acked",
        attempt=1,
        token=_ledger.token_for(key),
        ts=ts,
    )


_POLICY = FirePolicy(refire_days=3, ack_snooze_days=7)


def test_enrich_suppresses_item_fired_within_refire_window() -> None:
    dl = _dl(days_out=2, matter_id="m-1")  # overdue-ish, in range
    fired = _fired_event(dl, ts="2026-06-07T07:00:00.000Z")  # yesterday
    enriched = enrich_with_ledger([dl], today=TODAY, policy=_POLICY, ledger_events=[fired])
    assert enriched[0].acknowledged is True  # inside the 3-day window → suppressed


def test_enrich_refires_after_window() -> None:
    dl = _dl(days_out=2, matter_id="m-1")
    fired = _fired_event(dl, ts="2026-06-04T07:00:00.000Z")  # 4 days ago
    enriched = enrich_with_ledger([dl], today=TODAY, policy=_POLICY, ledger_events=[fired])
    assert enriched[0].acknowledged is False  # window elapsed → fires again


def test_enrich_acked_is_snoozed_then_resurfaces() -> None:
    dl = _dl(days_out=2, matter_id="m-1")
    events = [
        _fired_event(dl, ts="2026-06-05T07:00:00.000Z"),
        _acked_event(dl, ts="2026-06-05T09:00:00.000Z"),  # acked 3 days ago
    ]
    snoozed = enrich_with_ledger([dl], today=TODAY, policy=_POLICY, ledger_events=events)
    assert snoozed[0].acknowledged is True  # within 7-day snooze
    later = date(2026, 6, 13)  # 8 days after the ack
    resurfaced = enrich_with_ledger([dl], today=later, policy=_POLICY, ledger_events=events)
    assert resurfaced[0].acknowledged is False  # ack is a snooze, not a tombstone


def test_enrich_new_item_fires() -> None:
    dl = _dl(days_out=2, matter_id="m-1")
    enriched = enrich_with_ledger([dl], today=TODAY, policy=_POLICY, ledger_events=[])
    assert enriched[0].acknowledged is False


def test_run_once_suppresses_recently_fired_in_range_item() -> None:
    """End-to-end: an in-range item that fired yesterday does NOT re-wake."""
    dl = _dl(days_out=2, matter_id="m-1")
    fired = _fired_event(dl, ts="2026-06-07T07:00:00.000Z")
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(
            [FakeSource([dl])],
            EscalationWindows(),
            factory,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[fired],
        )
    )
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}  # suppressed via ledger
    assert len(executor.calls) == 1  # heartbeat row written


def test_run_once_still_wakes_when_refire_window_elapsed() -> None:
    dl = _dl(days_out=2, matter_id="m-1")
    fired = _fired_event(dl, ts="2026-06-01T07:00:00.000Z")  # a week ago
    code, out = _capture_stdout(
        run_once(
            [FakeSource([dl])],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[fired],
        )
    )
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "deadline_in_escalation_range"
    # The re-fire carries the prior raise it read, with its provenance — the
    # turn states "last raised" only from this, never from recall (#2253).
    assert parsed["plans"][0]["last_raised"] == "2026-06-01T07:00:00.000Z"
    assert parsed["plans"][0]["last_raised_source"] == "operator_ledger"


# ---------------------------------------------------------------------------
# The wake payload (#2253) — the handoff Hermes injects verbatim into the
# woken turn's prompt. What is absent here is what the turn has to invent.
# ---------------------------------------------------------------------------


def test_wake_payload_carries_last_raised_only_when_the_ledger_has_one() -> None:
    """A never-raised item carries None. Rendered downstream as "no Operator
    raise on record" — the ledger records Operator raises after a successful
    send, so absent is never evidence that nobody raised it."""
    raised = _dl(days_out=2, matter_id="m-1", task_id="t-1")
    fresh = _dl(days_out=4, matter_id="m-2", task_id="t-2")
    fired = _fired_event(raised, ts="2026-06-01T07:00:00.000Z")
    code, out = _capture_stdout(
        run_once(
            [FakeSource([raised, fresh])],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[fired],
        )
    )
    assert code == 0
    by_matter = {p["matter_id"]: p for p in json.loads(out)["plans"]}
    assert by_matter["m-1"]["last_raised"] == "2026-06-01T07:00:00.000Z"
    assert by_matter["m-2"]["last_raised"] is None
    assert {p["last_raised_source"] for p in by_matter.values()} == {"operator_ledger"}


def test_wake_payload_truncation_announces_itself() -> None:
    """Over the cap the list is partial, and the payload says so. A truncated
    list that reads as complete is a check that cannot fail (Law 12)."""
    deadlines = [_dl(days_out=(i % 14), matter_id=f"m-{i}", task_id=f"t-{i}") for i in range(58)]
    code, out = _capture_stdout(
        run_once(
            [FakeSource(deadlines)],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[],
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["plans_total"] == 58
    assert parsed["plans_emitted"] == 50
    assert parsed["plans_truncated"] is True
    assert len(parsed["plans"]) == 50


def test_wake_payload_untruncated_says_so_explicitly() -> None:
    """The flag is present on the complete case too, so its absence never has
    to be read as "complete"."""
    deadlines = [_dl(days_out=3, matter_id=f"m-{i}", task_id=f"t-{i}") for i in range(4)]
    code, out = _capture_stdout(
        run_once(
            [FakeSource(deadlines)],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[],
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["plans_total"] == parsed["plans_emitted"] == 4
    assert parsed["plans_truncated"] is False


def test_wake_payload_carries_the_rung_and_authored_date_per_item() -> None:
    """Each rung serializes, and the authored date rides verbatim alongside the
    derived days_out — an integer alone invites re-deriving a date, which is the
    one arithmetic this skill may never do."""
    deadlines = [
        _dl(days_out=1, matter_id="near", task_id="t-a"),
        _dl(days_out=6, matter_id="mid", task_id="t-b"),
        _dl(days_out=12, matter_id="far", task_id="t-c"),
        _dl(days_out=2, matter_id="held", task_id="t-d", conflict_hold=True),
    ]
    code, out = _capture_stdout(
        run_once(
            [FakeSource(deadlines)],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[],
        )
    )
    assert code == 0
    plans = {p["matter_id"]: p for p in json.loads(out)["plans"]}
    assert plans["near"]["rung"] == "notify"
    assert plans["mid"]["rung"] == "re-route"
    assert plans["far"]["rung"] == "re-surface"
    assert plans["held"]["rung"] == "clearance"
    assert plans["far"]["authored_date"] == "2026-06-20"
    assert plans["far"]["days_out"] == 12


def test_decide_plans_mirror_the_in_range_set() -> None:
    decision = decide(
        [_dl(matter_id="a", days_out=2), _dl(matter_id="b", days_out=40), _dl(matter_id="c", days_out=12)],
        EscalationWindows(),
        raw_inputs_for_digest=b"x",
        today=TODAY,
    )
    assert {p.matter_id for p in decision.plans} == {"a", "c"}
    assert len(decision.plans) == len(decision.extra_metadata["matters"])


def test_decide_suppressed_decision_carries_no_plans() -> None:
    decision = decide([_dl(days_out=40)], EscalationWindows(), raw_inputs_for_digest=b"x", today=TODAY)
    assert decision.wake is False
    assert decision.plans == ()


def test_no_stable_id_item_always_fires_until_blanket_acked() -> None:
    """An idless item can be acked only en bloc; with no ack event it keeps
    firing (its item_key state is absent so should_fire stays True)."""
    idless = _dl(days_out=2, matter_id="m-2")  # _dl leaves task_id=None
    assert idless.task_id is None
    enriched = enrich_with_ledger([idless], today=TODAY, policy=_POLICY, ledger_events=[])
    assert enriched[0].acknowledged is False


def test_load_escalation_config_reads_overrides(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(
        "escalation:\n"
        "  red_flag_recipients: [scott@smd.services]\n"
        "  refire_days: 5\n"
        "  ack_snooze_days: 10\n"
        "  near_days: 9\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    windows, policy = load_escalation_config()
    assert policy.refire_days == 5
    assert policy.ack_snooze_days == 10
    assert windows.near_days == 9
    assert windows.escalation_window_days == 14  # unset → pack default


def test_load_escalation_config_defaults_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    windows, policy = load_escalation_config()
    assert policy == FirePolicy()  # pack defaults
    assert windows == EscalationWindows()


def test_load_escalation_config_defaults_on_unparseable(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "customer.yaml"
    bad.write_text("escalation: [this is: not valid: yaml", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(bad))
    _windows, policy = load_escalation_config()
    assert policy == FirePolicy()  # never crash, never silent-suppress


# ---------------------------------------------------------------------------
# Digest projection (ss #2405) — counts are list lengths by construction; the
# 2026-08-14 defect ("1 routine confirmation(s)" above two ack codes, subject
# counting 32 routine confirms as "need you") is structurally impossible here.
# ---------------------------------------------------------------------------

project_digest = _pre_run.project_digest
parse_pull = _pre_run.parse_pull


def _project(deadlines, *, windows=None):
    return project_digest(deadlines, windows or EscalationWindows(), _ledger, today=TODAY)


def test_digest_counts_equal_list_lengths_per_matter():
    # 7 firing stable items: 5 most-overdue go to needs_you, 2 to admin —
    # and each admin matter's count equals its code-list length (the PI-106
    # falsifier: two items on one matter can never render as "1").
    items = [_dl(matter_id="m-a", days_out=-40 + i, task_id=f"t-{i}") for i in range(5)] + [
        _dl(matter_id="m-b", days_out=-2, task_id="t-b1"),
        _dl(matter_id="m-b", days_out=-1, task_id="t-b2"),
    ]
    d = _project(items)
    assert len(d["needs_you"]) == 5
    assert d["subject"] == "[Deadlines] 5 need you, 2026-06-08"
    admin = d["admin_confirms"]
    assert admin["total"] == 2
    assert admin["matter_count"] == 1
    (matter,) = admin["matters"]
    assert matter["matter_id"] == "m-b"
    assert matter["count"] == 2
    assert matter["count"] == len(matter["ack_codes"]) == len(matter["items"])
    assert all(code and code.startswith("ACK-") for code in matter["ack_codes"])


def test_digest_needs_you_is_most_overdue_first_and_subject_counts_only_it():
    items = [
        _dl(matter_id="m-1", days_out=5, task_id="t-near"),
        _dl(matter_id="m-2", days_out=-30, task_id="t-overdue"),
    ]
    d = _project(items)
    assert [i["task_id"] for i in d["needs_you"]] == ["t-overdue", "t-near"]
    assert d["subject"].startswith("[Deadlines] 2 need you")
    assert "admin_confirms" not in d  # empty sections omitted whole (rule 9)


def test_digest_recently_raised_items_band_as_elsewhere_not_admin():
    quiet = _dl(
        matter_id="m-1",
        days_out=2,
        task_id="t-raised",
        acknowledged=True,
        last_raised="2026-06-07T09:00:00.000Z",
    )
    d = _project([quiet, _dl(matter_id="m-2", days_out=1, task_id="t-live")])
    band = d["under_active_escalation_elsewhere"]
    assert [i["task_id"] for g in band["matters"] for i in g["items"]] == ["t-raised"]
    assert [i["task_id"] for i in d["needs_you"]] == ["t-live"]


def test_digest_acked_items_are_omitted_entirely():
    acked = _dl(
        matter_id="m-1",
        days_out=2,
        task_id="t-acked",
        acknowledged=True,
        acked=True,
        last_raised="2026-06-01T09:00:00.000Z",
    )
    d = _project([acked, _dl(matter_id="m-2", days_out=1, task_id="t-live")])
    assert "under_active_escalation_elsewhere" not in d
    assert [i["task_id"] for i in d["needs_you"]] == ["t-live"]


def test_digest_clearance_and_blanket_bands():
    held = _dl(matter_id="m-h", days_out=2, task_id="t-h", conflict_hold=True)
    idless = _dl(matter_id="m-b", days_out=-3, task_id=None)
    d = _project([held, idless])
    assert [i["task_id"] for i in d["awaiting_clearance"]] == ["t-h"]
    assert [i["matter_id"] for i in d["blanket_ack_only"]] == ["m-b"]
    assert d["blanket_ack_only"][0]["ack_code"] is None
    assert d["needs_you"] == []


def test_digest_is_computed_over_the_full_universe_not_the_plan_cap():
    # 60 firing items — beyond _MAX_SERIALIZED_PLANS. The projection's totals
    # must cover all of them (the /critique finding: a projection built from a
    # truncated plan list reproduces confident-wrong counts).
    items = [_dl(matter_id=f"m-{i}", days_out=-i, task_id=f"t-{i}") for i in range(1, 61)]
    d = _project(items)
    assert len(d["needs_you"]) == 5
    assert d["admin_confirms"]["total"] == 55


def test_digest_out_of_range_and_closed_matters_are_excluded():
    d = _project(
        [
            _dl(matter_id="m-far", days_out=40, task_id="t-far"),
            _dl(matter_id="m-closed", days_out=1, task_id="t-c", matter_open=False),
            _dl(matter_id="m-live", days_out=1, task_id="t-live"),
        ]
    )
    assert [i["task_id"] for i in d["needs_you"]] == ["t-live"]
    assert "admin_confirms" not in d


def test_digest_probe_stats_render_only_when_present():
    live = [_dl(matter_id="m-1", days_out=1, task_id="t-1")]
    d = project_digest(live, EscalationWindows(), _ledger, today=TODAY, probe_stats={"excluded": 0, "stale": 0})
    assert "probe_artifacts" not in d
    d2 = project_digest(
        live,
        EscalationWindows(),
        _ledger,
        today=TODAY,
        probe_stats={"excluded": 2, "stale": 1, "stale_task_ids": ["t-p"]},
    )
    assert d2["probe_artifacts"]["stale"] == 1


def test_parse_pull_probe_census_counts_and_ages():
    from datetime import datetime as _dt, timezone as _tz

    now = _dt(2026, 6, 8, 12, 0, tzinfo=_tz.utc)
    raw = {
        "tasks": [
            {  # fresh probe: excluded, not stale
                "id": "t-fresh",
                "matterId": "m-1",
                "subject": "[SMD-PROBE 2026-06-08T10:00Z] fresh rehearsal",
                "dueDate": "2026-06-20",
            },
            {  # stale probe: stamp older than 24h
                "id": "t-stale",
                "matterId": "m-1",
                "subject": "[Operator] [SMD-PROBE 2026-06-01T10:00Z] old rehearsal",
                "dueDate": "2026-06-20",
            },
            {  # malformed stamp: stale immediately
                "id": "t-bad",
                "matterId": "m-1",
                "subject": "[SMD-PROBE someday] malformed",
                "dueDate": "2026-06-20",
            },
            {"id": "t-real", "matterId": "m-1", "subject": "Real", "dueDate": "2026-06-20"},
        ],
        "events": [],
    }
    deadlines, problem, probe = parse_pull(raw, now=now)
    assert problem is None
    assert [d.task_id for d in deadlines] == ["t-real"]
    assert probe["excluded"] == 3
    assert probe["stale"] == 2
    assert set(probe["stale_task_ids"]) == {"t-stale", "t-bad"}


def test_run_once_wake_line_carries_the_digest():
    sources = [FakeSource([_dl(days_out=-2, task_id="task-9")])]

    def factory():
        return None  # fail-open writer path still emits the decision's digest

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    digest = payload["digest"]
    assert digest["subject"].startswith("[Deadlines] 1 need you")
    (item,) = digest["needs_you"]
    assert item["task_id"] == "task-9"
    assert item["ack_code"].startswith("ACK-")


# ---------------------------------------------------------------------------
# Pre-run handoff (ss#2547)
# ---------------------------------------------------------------------------
# The gate reads authored dates from the firm's record and hands them to the
# turn as prompt text. Prompt text is not a source, so on 2026-08-19 the
# identifier gate refused this skill's digest four times over those very dates.
# The handoff file is what lets the overlay seed them as read, and these tests
# guard the three properties that make it safe to seed from: it lands, it
# carries exactly what the wake line already said, and it carries nothing else.

_HANDOFF_KEYS = {"skill", "started_at", "dates", "matter_ids", "records"}


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
            [FakeSource([_dl(days_out=3, matter_id="7001", task_id="t-1")])],
            EscalationWindows(),
            lambda: None,
            today=TODAY,
            now=NOW,
            fire_policy=_POLICY,
            ledger_events=[],
        )
    )
    assert code == 0
    return out


def _handoff_path(home) -> Path:
    return Path(home) / ".smd" / "pre_run" / "deadline-miss-escalator.json"


def test_the_wake_writes_a_handoff_whose_dates_are_the_dates_it_emitted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = json.loads(_wake_stdout())
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    emitted = _authored_dates_in(payload)
    assert emitted, "this fixture must emit an authored date or the test proves nothing"
    assert record["dates"] == emitted
    assert record["skill"] == "deadline-miss-escalator"
    assert "7001" in record["matter_ids"]


def test_the_handoff_carries_nothing_but_the_projection(tmp_path, monkeypatch) -> None:
    """Labels, ack codes, subjects and prose never reach the register."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _wake_stdout()
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert set(record) == _HANDOFF_KEYS
    assert record["started_at"].endswith("Z")
    datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))


def test_the_handoff_is_readable_only_by_its_owner(tmp_path, monkeypatch) -> None:
    """It names the matters the firm is working on. Asserted at the canonical
    site; the same block is copied verbatim into the other three pre_runs."""
    import stat

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _wake_stdout()
    mode = stat.S_IMODE(_handoff_path(tmp_path).stat().st_mode)
    assert mode & 0o077 == 0, f"group/other bits set: {oct(mode)}"


def test_a_temp_file_left_by_a_crashed_run_does_not_wedge_the_writer(tmp_path, monkeypatch) -> None:
    """The open is O_EXCL so it cannot follow a planted symlink. Without the
    unlink in front of it, one crashed run would silence the handoff forever."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    stale = tmp_path / ".smd" / "pre_run" / ".deadline-miss-escalator.json.tmp"
    stale.parent.mkdir(parents=True)
    stale.write_text("left over", encoding="utf-8")
    _wake_stdout()
    assert _handoff_path(tmp_path).exists()
    assert not stale.exists()


def test_a_handoff_write_failure_leaves_stdout_byte_identical(tmp_path, monkeypatch) -> None:
    """HERMES_HOME is a FILE, so the write fails for any uid. A read-only
    directory would still be writable by root, and CI containers run as root.

    ``dispatch_expected`` is the ONE legitimate difference (WS-RENDER): it
    reflects whether the dispatch envelope actually landed on disk, and on the
    blocked volume it must clear — that flag going absent is exactly what
    routes the woken turn to the failure-note branch. Everything else stays
    byte-identical."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    good = json.loads(_wake_stdout())
    assert _handoff_path(tmp_path).exists()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(blocked))
    degraded = json.loads(_wake_stdout())
    assert degraded.pop("dispatch_expected", None) is None
    good.pop("dispatch_expected", None)
    assert degraded == good


# ---------------------------------------------------------------------------
# Matter numbers projected in code (ss #2390 / the 2026-08-24 degraded digest)
# ---------------------------------------------------------------------------
#
# The fixture is CAPTURED FROM THE LIVE API (pilot tenant, 2026-08-24, trimmed
# to the fields these paths read) — ss #2390 AC4: fixtures come from the live
# API, not authored dict literals. The connector join itself is unit-tested in
# operator/connectors/smokeball/tests/test_matter_ref.py against the same
# capture; here the join is REPLAYED over the capture and the projection is
# checked against the SOURCE record, so a digest item wearing a lookalike
# matter's number fails (the ss #2405 falsifier shape).

_FIXTURE_PATH = _HERE.parent / "tests" / "fixtures" / "live-pull-2026-08-24.json"
_CONNECTOR_PKG = _HERE.parents[2] / "connectors" / "smokeball"


def _load_matter_ref():
    matter_ref_path = _CONNECTOR_PKG / "smokeball_connector" / "matter_ref.py"
    spec = importlib.util.spec_from_file_location("smokeball_matter_ref", matter_ref_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FixtureClient:
    def __init__(self, matters):
        self._matters = matters

    def get(self, path, **params):
        matter = self._matters.get(path.rsplit("/", 1)[-1])
        if matter is None:
            raise RuntimeError("404")
        return matter


def _enriched_fixture():
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    matter_ref = _load_matter_ref()
    tasks = fixture["tasks"]["value"]
    matter_ref.attach_matter_numbers(_FixtureClient(fixture["matters"]), tasks)
    return fixture


def test_parse_pull_carries_the_code_projected_matter_number():
    fixture = _enriched_fixture()
    deadlines, problem, _stats = _pre_run.parse_pull({"tasks": fixture["tasks"], "events": []})
    assert problem is None
    assert deadlines, "the live capture holds open tasks with due dates"
    for d in deadlines:
        source = fixture["matters"][d.matter_id]
        assert d.matter_number == source["number"]
        assert d.matter_number_absent is None


def test_the_digest_item_number_matches_the_source_record_for_every_item():
    """The ss #2405 falsifier: the projection is compared against the SOURCE,
    per item — a digest item carrying another matter's number, or a composed
    one, fails here."""
    fixture = _enriched_fixture()
    deadlines, _problem, _stats = _pre_run.parse_pull({"tasks": fixture["tasks"], "events": []})
    ledger = _pre_run._load_ledger_module()
    digest = _pre_run.project_digest(
        deadlines,
        EscalationWindows(),
        ledger,
        today=date(2026, 7, 20),
    )
    rendered = 0
    for section in ("needs_you", "under_active_escalation_elsewhere", "blanket_ack_only"):
        band = digest.get(section) or []
        items = [i for g in band["matters"] for i in g["items"]] if isinstance(band, dict) else band
        for item in items:
            source = fixture["matters"][item["matter_id"]]
            assert item["matter_number"] == source["number"]
            rendered += 1
    assert rendered > 0


def test_an_unenriched_item_reads_as_lookup_failed_not_as_authored_absence():
    """An item with neither annotation came from a pull whose enrichment never
    ran or crashed wholesale — for the degraded judgment that IS a resolution
    failure, and must never read as "the record has no number"."""
    deadlines, _problem, _stats = _pre_run.parse_pull(
        {
            "tasks": [
                {
                    "id": "t1",
                    "subject": "x",
                    "dueDate": "2026-07-10",
                    "matter": {"id": "m-1"},
                }
            ],
            "events": [],
        }
    )
    (d,) = deadlines
    assert d.matter_number is None
    assert d.matter_number_absent == "lookup_failed"


def test_a_typed_absence_rides_through_parse():
    deadlines, _problem, _stats = _pre_run.parse_pull(
        {
            "tasks": [
                {
                    "id": "t1",
                    "subject": "x",
                    "dueDate": "2026-07-10",
                    "matter": {"id": "m-1"},
                    "matterNumberAbsent": "no_number_on_record",
                }
            ],
            "events": [],
        }
    )
    (d,) = deadlines
    assert d.matter_number is None
    assert d.matter_number_absent == "no_number_on_record"


def test_the_handoff_records_group_each_matters_dates_under_its_number(tmp_path, monkeypatch):
    """The association half of ss #2390: the overlay register seeds (number,
    dates) PER RECORD, so the grouping here is the mispairing boundary — a date
    grouped under the wrong number would seed the wrong pair."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = {
        "plans": [
            {
                "matter_id": "m-1",
                "matter_number": "PI-2026-0001",
                "authored_date": "2026-07-08",
            },
            {
                "matter_id": "m-1",
                "matter_number": "PI-2026-0001",
                "authored_date": "2026-07-14",
            },
            {
                "matter_id": "m-2",
                "matter_number": "PI-2026-0002",
                "authored_date": "2026-07-10",
            },
            # No number: contributes to dates, never to records.
            {"matter_id": "m-3", "authored_date": "2026-07-12"},
        ]
    }
    _pre_run._write_pre_run_handoff(payload)
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.json").read_text(encoding="utf-8"))
    assert written["records"] == [
        {"matterNumber": "PI-2026-0001", "dates": ["2026-07-08", "2026-07-14"]},
        {"matterNumber": "PI-2026-0002", "dates": ["2026-07-10"]},
    ]
    assert "2026-07-12" in written["dates"]


def test_the_handoff_records_include_each_matters_last_raised_day(tmp_path, monkeypatch):
    """The 2026-08-24 rehearsal refusal: the under-active band renders
    "(last raised <date>)" beside the matter number, and that PAIRING must seed
    or a fully correct digest is refused on it. ``last_raised`` is an ISO
    timestamp; the digest renders its day, so the day seeds."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = {
        "digest": {
            "under_active_escalation_elsewhere": [
                {
                    "matter_id": "m-1",
                    "matter_number": "2026-PI-101",
                    "authored_date": "2026-07-08",
                    "last_raised": "2026-08-24T14:04:09.774Z",
                }
            ]
        }
    }
    _pre_run._write_pre_run_handoff(payload)
    written = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.json").read_text(encoding="utf-8"))
    assert written["records"] == [{"matterNumber": "2026-PI-101", "dates": ["2026-07-08", "2026-08-24"]}]


def test_load_matter_lookup_budget_reads_the_authored_value_and_allows_zero(tmp_path, monkeypatch):
    """Zero is a legitimate authored value — the staging lever that forces the
    degraded path for the runtime rehearsal. Missing or malformed → default."""
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text("escalation:\n  matter_lookup_budget: 0\n", encoding="utf-8")
    assert _pre_run.load_matter_lookup_budget(str(yaml_path)) == 0
    yaml_path.write_text("escalation:\n  matter_lookup_budget: 25\n", encoding="utf-8")
    assert _pre_run.load_matter_lookup_budget(str(yaml_path)) == 25
    yaml_path.write_text("escalation: {}\n", encoding="utf-8")
    assert _pre_run.load_matter_lookup_budget(str(yaml_path)) == _pre_run._DEFAULT_MATTER_LOOKUP_BUDGET
    yaml_path.write_text("escalation:\n  matter_lookup_budget: -3\n", encoding="utf-8")
    assert _pre_run.load_matter_lookup_budget(str(yaml_path)) == _pre_run._DEFAULT_MATTER_LOOKUP_BUDGET
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    assert _pre_run.load_matter_lookup_budget(None) == _pre_run._DEFAULT_MATTER_LOOKUP_BUDGET


def test_the_subprocess_source_passes_the_budget_in_the_env_and_captures_counts(
    monkeypatch,
):
    seen = {}

    def fake_run(argv, capture_output, text, timeout, env):
        seen["env_budget"] = env.get("SMD_MATTER_LOOKUP_BUDGET")
        seen["argv"] = argv

        class R:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "tasks": [],
                    "events": [],
                    "matterNumberCounts": {"resolved": 0},
                }
            )

        return R()

    monkeypatch.setattr(_pre_run.subprocess, "run", fake_run)
    source = _pre_run.SmokeballSubprocessSource(EscalationWindows(), date(2026, 8, 24), matter_lookup_budget=7)
    assert source.pull_deadlines() == []
    assert seen["env_budget"] == "7"
    # argv stays exactly interpreter, -c, snippet, and the two date strings
    # (the nosemgrep justification's contract).
    assert len(seen["argv"]) == 5
    assert source.matter_number_counts == {"resolved": 0}


# ---------------------------------------------------------------------------
# The degraded-run rule (2026-08-24): a digest naming zero matters is withheld
# ---------------------------------------------------------------------------


def _dl_num(number=None, absent=None, **kwargs):
    """An in-range deadline with matter-number provenance."""
    return _dl(matter_number=number, matter_number_absent=absent, **kwargs)


def test_zero_resolved_with_failures_suppresses_and_pages_not_sends():
    """The incident rule. Every line of the would-be digest reads "matter
    number unavailable" because the JOIN failed — that artifact is withheld,
    and the SUPPRESSED_WAKE row carries the digest_degraded basis + a reason
    with the run's own numbers (what the team@ page renders)."""
    sources = [
        FakeSource(
            [
                _dl_num(absent="lookup_failed", days_out=5, task_id="t-1"),
                _dl_num(absent="lookup_failed", days_out=2, task_id="t-2", matter_id="7002"),
            ]
        )
    ]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}
    (call,) = executor.calls
    sql, params = call
    assert params[2] == "SUPPRESSED_WAKE"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "digest_degraded_suppressed"
    assert metadata["matter_numbers_resolved"] == 0
    assert metadata["matter_lookups_failed"] == 2
    assert "withheld" in metadata["degraded_reason"]
    assert "2 lookup(s) failed" in metadata["degraded_reason"]


def test_degraded_suppress_with_a_failed_audit_write_wakes_stripped():
    """The critique's blocking finding: the old fail-open (wake WITH the
    digest) would re-ship the incident artifact the suppress just withheld.
    The stripped wake carries no plans and no digest — nothing degraded for
    the turn to render — under its own basis."""
    sources = [FakeSource([_dl_num(absent="lookup_failed", days_out=5, task_id="t-1")])]
    executor = FakeExecutor(fail=True)

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    payload = json.loads(out)
    assert payload == {
        "wakeAgent": True,
        "decision_basis": "digest_degraded_audit_unavailable",
    }


def test_partial_failure_ships_the_digest_and_pages_the_degradation():
    """1-of-40 must neither sail silently nor be withheld: real deadlines with
    a resolved number outweigh the failed lookups (each renders explicit
    absence), and the degraded fact rides the EMITTED_WAKE metadata so the
    heartbeat's degraded kind still pages."""
    sources = [
        FakeSource(
            [
                _dl_num(number="PI-2026-0001", days_out=5, task_id="t-1"),
                _dl_num(absent="lookup_failed", days_out=3, task_id="t-2", matter_id="7002"),
            ]
        )
    ]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    assert payload["decision_basis"] == "deadline_in_escalation_range"
    assert payload["digest"] is not None
    (call,) = executor.calls
    _sql, params = call
    assert params[2] == "EMITTED_WAKE"
    metadata = json.loads(params[11])
    assert metadata["degraded_reason"].startswith("digest sent with explicit absences")
    assert metadata["matter_numbers_resolved"] == 1
    assert metadata["matter_lookups_failed"] == 1


def test_authored_absence_alone_is_never_degraded():
    """A firm whose matters carry no numbers keeps its deadline watch: the
    digest ships with "no number on record" per item and nothing pages."""
    sources = [FakeSource([_dl_num(absent="no_number_on_record", days_out=5, task_id="t-1")])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(run_once(sources, EscalationWindows(), factory, today=TODAY, now=NOW))
    assert code == 0
    payload = json.loads(out)
    assert payload["wakeAgent"] is True
    assert payload["decision_basis"] == "deadline_in_escalation_range"
    (call,) = executor.calls
    _sql, params = call
    metadata = json.loads(params[11])
    assert "degraded_reason" not in metadata


def test_elsewhere_band_collapses_per_matter_never_one_row_per_item():
    """The 2026-08-25 regression: 38 flat rows, 20 of them one matter.

    A band whose whole message is "already handled, no action here" must never
    be the longest thing in the alert (Law 11). It arrives pre-collapsed, so the
    turn has no flat list to render even if it wanted one.

    Falsifier: if the projection went back to a flat list, ``matters`` would not
    exist and this would raise TypeError/KeyError rather than pass.
    """
    quiet = [
        _dl(
            matter_id="m-1",
            days_out=-40 - i,
            task_id=f"t-{i}",
            acknowledged=True,
            last_raised="2026-06-07T09:00:00.000Z",
        )
        for i in range(20)
    ]
    quiet.append(
        _dl(
            matter_id="m-2",
            days_out=-5,
            task_id="t-other",
            acknowledged=True,
            last_raised="2026-06-09T09:00:00.000Z",
        )
    )
    d = _project(quiet + [_dl(matter_id="m-3", days_out=1, task_id="t-live")])
    band = d["under_active_escalation_elsewhere"]

    assert band["total"] == 21, "every item still counted"
    assert band["matter_count"] == 2, "two matters, not 21 rows"
    assert len(band["matters"]) == 2

    m1 = next(g for g in band["matters"] if g["matter_id"] == "m-1")
    assert m1["count"] == 20
    # One line can state one date, so it states the most recent raise.
    assert m1["last_raised"] == "2026-06-07T09:00:00.000Z"
    # Counts are list lengths by construction, never arithmetic.
    assert m1["count"] == len(m1["items"])
    assert band["total"] == sum(g["count"] for g in band["matters"])


def test_grouped_bands_carry_the_matter_number_so_a_line_can_name_the_matter():
    """A collapsed line renders "matter <number>", so the GROUP needs the number.

    Before this, only items carried it and a renderer had to reach into an item
    to name the matter the group is about.
    """
    quiet = _dl(
        matter_id="m-1",
        days_out=-9,
        task_id="t-1",
        acknowledged=True,
        last_raised="2026-06-07T09:00:00.000Z",
        matter_number="2026-PI-101",
    )
    d = _project([quiet, _dl(matter_id="m-2", days_out=1, task_id="t-live")])
    group = d["under_active_escalation_elsewhere"]["matters"][0]
    assert group["matter_number"] == "2026-PI-101"
    assert "matter_number_absent" in group


# ---------------------------------------------------------------------------
# Blind wakes: a trace and a rendered body, never neither (2026-09-02)
#
# THE INCIDENT. pilot-smokeball's Smokeball credential expired. pre_run took a
# fail-open path and the seat produced a scheduled tick with NEITHER a
# SUPPRESSED_WAKE nor an EMITTED_WAKE row -- SKILL.md step 5's dead-man's
# signal -- while the woken turn, handed no rendered dispatch, composed a
# digest body out of nothing and sent it. Both guarantees were documented and
# neither was enforced. Every test below fails against the pre-fix code.
# ---------------------------------------------------------------------------


class _FakeWakeWriter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    async def write_emitted_wake(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("broker socket down")
        return "row-1"


def _authored_seat(tmp_path, monkeypatch, *, red_flag=("scott@smd.services",), fallback=()):
    """A seat that can read its own customer.yaml but nothing else."""
    cfg = tmp_path / "customer.yaml"
    block = "escalation:\n"
    if red_flag:
        block += "  red_flag_recipients: [%s]\n" % ", ".join(red_flag)
    if fallback:
        block += "  case_alert_routing:\n    fallback_recipients: [%s]\n" % ", ".join(fallback)
    if not red_flag and not fallback:
        block += "  refire_days: 3\n"
    cfg.write_text(block, encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json"


def _blind(monkeypatch, basis, writer):
    monkeypatch.setattr(_pre_run, "_sibling_writer_factory", lambda: writer)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _pre_run._blind_wake(basis)
    return rc, json.loads(buf.getvalue().strip().splitlines()[-1])


def _sibling(name, mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, _PRE_RUN_PATH.parent / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_render = _sibling("render.py", "escalator_render_under_test")
_envelope = _sibling("dispatch_envelope.py", "escalator_envelope_under_test")


def test_blind_wake_leaves_an_emitted_wake_row(tmp_path, monkeypatch):
    """The dead-man's signal. Before this, a decision-less wake wrote no row at
    all, so the slot was indistinguishable from a tick that never ran."""
    _authored_seat(tmp_path, monkeypatch)
    writer = _FakeWakeWriter()
    rc, payload = _blind(monkeypatch, "pre_run_crashed_fail_open", writer)
    assert rc == 0
    assert payload["wakeAgent"] is True
    assert payload["decision_basis"] == "pre_run_crashed_fail_open"
    assert len(writer.calls) == 1
    call = writer.calls[0]
    assert call["skill_name"] == "deadline-miss-escalator"
    assert call["decision_basis"] == "pre_run_crashed_fail_open"
    assert call["extra_metadata"]["blind_wake"] is True


def test_blind_wake_dispatches_the_authored_failure_note(tmp_path, monkeypatch):
    """The body half. The turn must be handed a rendered note to deliver, not
    a gap plus a SKILL.md sentence asking it to behave."""
    envelope_path = _authored_seat(tmp_path, monkeypatch)
    rc, payload = _blind(monkeypatch, "pre_run_crashed_fail_open", _FakeWakeWriter())
    assert rc == 0
    # The wake line puts the turn on the compose-nothing branch.
    assert payload["dispatch_expected"] is True
    assert payload["dispatch_variant"] == "failure_note"
    assert "plans" not in payload  # nothing was read, so nothing is claimed

    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["skill"] == "deadline-miss-escalator"
    assert len(envelope["dispatches"]) == 1
    d = envelope["dispatches"][0]
    assert d["recipients"] == ["scott@smd.services"]
    assert d["routing_leg"] == "central"
    assert d["subject"] == _render.FAILURE_NOTE_SUBJECT
    assert d["full_body"] == _render.FAILURE_NOTE
    # No rung below a one-line note, so the overlay's full -> skeleton ladder
    # cannot degrade it into something vaguer.
    assert d["skeleton_body"] == _render.FAILURE_NOTE
    assert d["body_sha256_full"] == d["body_sha256_skeleton"]
    # Nothing was raised, so nothing may be appended: a `fired` event here
    # would record an escalation that never happened.
    assert d["appends"] == []


def test_blind_wake_falls_back_to_authored_fallback_recipients(tmp_path, monkeypatch):
    envelope_path = _authored_seat(tmp_path, monkeypatch, red_flag=(), fallback=("ops@smd.services",))
    _blind(monkeypatch, "pre_run_crashed_fail_open", _FakeWakeWriter())
    d = json.loads(envelope_path.read_text(encoding="utf-8"))["dispatches"][0]
    assert d["recipients"] == ["ops@smd.services"]
    assert d["routing_leg"] == "fallback"


def test_blind_wake_with_nobody_authored_still_leaves_a_row(tmp_path, monkeypatch):
    """The fail-closed floor: no authored address means no delivery, and the
    row is then the ONLY thing that makes the slot visible."""
    envelope_path = _authored_seat(tmp_path, monkeypatch, red_flag=(), fallback=())
    writer = _FakeWakeWriter()
    _rc, payload = _blind(monkeypatch, "pre_run_crashed_fail_open", writer)
    assert not envelope_path.exists()
    assert "dispatch_expected" not in payload
    assert len(writer.calls) == 1  # the trace survives the floor


def test_blind_wake_keeps_the_two_unwritable_exemptions(tmp_path, monkeypatch):
    """These two bases fire BECAUSE the writer is unusable -- calling it would
    be asking a broken thing to record that it is broken. The exemption is
    preserved deliberately; what the fix removed is its silent application to
    every OTHER decision-less path."""
    for basis in ("no_audit_writer_fail_open", "suppress_heartbeat_failed_fail_open"):
        _authored_seat(tmp_path, monkeypatch)
        writer = _FakeWakeWriter()
        _rc, payload = _blind(monkeypatch, basis, writer)
        assert writer.calls == [], basis
        # ...but the failure note still goes out: an unusable audit writer says
        # nothing about whether we can render and deliver.
        assert payload["dispatch_expected"] is True, basis


def test_blind_wake_row_failure_never_changes_the_wake(tmp_path, monkeypatch):
    """Observability may not gate the wake. A broker that refuses the row must
    leave stdout byte-identical to the succeeding case."""
    _authored_seat(tmp_path, monkeypatch)
    _rc_ok, ok = _blind(monkeypatch, "pre_run_crashed_fail_open", _FakeWakeWriter())
    _authored_seat(tmp_path, monkeypatch)
    rc_bad, bad = _blind(monkeypatch, "pre_run_crashed_fail_open", _FakeWakeWriter(fail=True))
    assert rc_bad == 0
    assert bad == ok


def test_envelope_build_fault_writes_the_failure_note(tmp_path, monkeypatch):
    """build_and_write's own fault path. Today it degraded to 'no envelope',
    which is precisely the state that let the model compose."""
    envelope_path = _authored_seat(tmp_path, monkeypatch)
    out = _envelope.build_and_write(
        digest={"needs_you": object()},  # not iterable the way the builder expects
        deadlines=[],
        states={},
        ledger=None,
        today=TODAY,
        ack_snooze_days=7,
    )
    assert out.get("dispatch_variant") == "failure_note"
    assert out["dispatch_expected"] is True
    assert json.loads(envelope_path.read_text(encoding="utf-8"))["failure_note_reason"] == ("envelope_build_failed")


def test_a_clean_run_with_nothing_to_say_sends_no_failure_note(tmp_path, monkeypatch):
    """The guard against the opposite defect: an empty digest that rendered
    fine is a SUCCESS. Paging 'the run failed' here would be a false alarm on
    every quiet day, which is how alerts get ignored."""
    envelope_path = _authored_seat(tmp_path, monkeypatch)
    out = _envelope.build_and_write(
        digest={},
        deadlines=[],
        states={},
        ledger=None,
        today=TODAY,
        ack_snooze_days=7,
    )
    assert out == {}
    assert not envelope_path.exists()


# ---------------------------------------------------------------------------
# Cross-repo wiring: the dispatcher must ACCEPT the failure-note envelope
#
# An envelope the overlay refuses is worse than no envelope: the turn wakes
# with dispatch_expected true, no note arrives, and we are back to the model
# filling the gap. The shape is validated in the OTHER repo, so asserting it
# here from memory would be exactly the "documented, not enforced" mistake
# this whole change is fixing. Read the pinned overlay's real validator and
# run it. Skipped (never silently passed) when no overlay checkout exists --
# CI has none, same honest limitation as tests/heartbeat-field-parity.test.ts.
# ---------------------------------------------------------------------------

_OVERLAY_DIR = Path(os.environ.get("SS_OVERLAY_DIR") or (Path.home() / "dev" / "hermes-smd-overlay"))
_OVERLAY_AVAILABLE = (_OVERLAY_DIR / ".git").exists()


def _pinned_overlay_ref() -> str:
    dockerfile = (_PRE_RUN_PATH.parents[2] / "templates" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'ARG OVERLAY_REF="([0-9a-f]{40})"', dockerfile)
    assert m, "no ARG OVERLAY_REF in operator/templates/Dockerfile"
    return m.group(1)


@pytest.mark.skipif(not _OVERLAY_AVAILABLE, reason="no overlay checkout (expected in CI)")
def test_failure_note_envelope_passes_the_pinned_dispatchers_validator(tmp_path, monkeypatch):
    envelope_path = _authored_seat(tmp_path, monkeypatch)
    _blind(monkeypatch, "pre_run_crashed_fail_open", _FakeWakeWriter())
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))

    # `git show <ref>:path`, never the working tree: overlay checkouts sit on
    # dirty feature branches far from the pin, and the pin is what seats run.
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    source = subprocess.run(  # noqa: S603 - literal git show argv in a test against the pinned overlay ref
        ["git", "show", "%s:shared/prerendered_dispatch.py" % _pinned_overlay_ref()],
        cwd=str(_OVERLAY_DIR),
        capture_output=True,
        text=True,
        env=clean_env,
        check=True,
    ).stdout

    # Lift the validator + its bounds out of the pinned source and run them.
    # Importing the module would drag the overlay's whole dependency set in.
    # STRUCTURAL, not behavioural, and the difference is stated rather than
    # glossed: running the pinned validator would mean exec'ing code from
    # another repo inside a test, which the security scanner blocks and is
    # right to. So this reads which keys the validator REQUIRES and asserts
    # the envelope supplies each one. It catches the drift that actually
    # bites -- the dispatcher starting to require a field we do not write --
    # and it does NOT prove value-level acceptance.
    func = re.search(r"^def _valid_dispatch\(entry: object\) -> bool:\n(?:[ \t].*\n|\n)+", source, re.M)
    assert func, "could not lift _valid_dispatch out of the pinned dispatcher"
    required = set(re.findall(r'entry\.get\("(\w+)"', func.group(0)))
    # A regex that silently matched nothing would make every assertion below
    # vacuous -- the exact hole this repo keeps finding in its own gates.
    assert {"recipients", "subject", "full_body"} <= required, required

    for entry in envelope["dispatches"]:
        missing = [k for k in required if k not in entry]
        assert missing == [], (
            "the pinned dispatcher reads %s and the failure-note envelope omits %s; "
            "it would be refused whole and the turn would compose the gap" % (sorted(required), missing)
        )
        assert isinstance(entry["recipients"], list) and entry["recipients"]
        assert isinstance(entry["subject"], str) and entry["subject"].strip()
        assert isinstance(entry["full_body"], str) and entry["full_body"].strip()
