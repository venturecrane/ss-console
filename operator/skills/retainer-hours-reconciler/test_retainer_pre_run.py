"""Tests for retainer-hours-reconciler/pre_run.py (ADR 0021 Stream B.2).

Exercises the wake / suppress decision logic, the audit emission, and the
mirror-don't-gate fallback (audit failure → wake). Uses a fake
RetainerHoursConnector + a fake AuditLogWriter pair so the tests run with
no network, no real OAuth, and no D1.

Mirrors `paid-media-anomaly-watcher/test_pre_run.py` (B.1, PR #1062) —
same harness shape, adapted for the retainer-hours utilization buckets +
weekly mandatory boundary + previously-critical-ack policy.

Run from repo root:

    cd operator && python -m pytest \\
        skills/retainer-hours-reconciler/test_retainer_pre_run.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


# Allow running from repo root or from operator/. Every Stream B skill
# has a `pre_run.py` named identically per Hermes convention, so we can't
# add the skill dir to sys.path and `from pre_run import ...` — the first
# skill's pre_run wins the bare module name and subsequent tests collide.
# Load this skill's pre_run.py via importlib under a unique module name.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path (for adapter.*)

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AuditLogWriter,
    SuppressedWakeWriter,
)

_PRE_RUN_PATH = _HERE.parent / "pre_run.py"
_spec = importlib.util.spec_from_file_location(
    "retainer_hours_pre_run", _PRE_RUN_PATH
)
assert _spec is not None and _spec.loader is not None
_pre_run = importlib.util.module_from_spec(_spec)
sys.modules["retainer_hours_pre_run"] = _pre_run
_spec.loader.exec_module(_pre_run)

BucketThresholds = _pre_run.BucketThresholds
ClientUtilization = _pre_run.ClientUtilization
_assign_bucket = _pre_run._assign_bucket
_project_eom_pct = _pre_run._project_eom_pct
decide = _pre_run.decide
run_once = _pre_run.run_once


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConnector:
    """Stand-in for a time-tracking adapter. Returns whatever
    utilizations the test injected."""

    def __init__(self, utilizations: Sequence[ClientUtilization]) -> None:
        self._utilizations = utilizations

    def pull_utilizations(self) -> Sequence[ClientUtilization]:
        return self._utilizations


class FakeExecutor:
    """Audit executor that records SQL calls or raises on demand."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list]] = []

    async def execute(self, sql: str, params: list) -> None:
        self.calls.append((sql, params))
        if self.fail:
            raise RuntimeError("D1 unreachable")


def _make_util(
    *,
    client_slug: str = "client_a",
    actual_mtd_hours: float = 50.0,
    contracted_monthly_hours: float = 80.0,
    mtd_days_elapsed: int = 15,
    calendar_days_in_month: int = 30,
    previously_critical_pending_ack: bool = False,
) -> ClientUtilization:
    return ClientUtilization(
        client_slug=client_slug,
        actual_mtd_hours=actual_mtd_hours,
        contracted_monthly_hours=contracted_monthly_hours,
        mtd_days_elapsed=mtd_days_elapsed,
        calendar_days_in_month=calendar_days_in_month,
        previously_critical_pending_ack=previously_critical_pending_ack,
    )


# Tuesday at 09:00 UTC — NOT the weekly mandatory boundary.
TUESDAY = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
# Monday at 14:00 UTC — IS the weekly mandatory boundary.
MONDAY = datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _project_eom_pct() and _assign_bucket() — math sanity
# ---------------------------------------------------------------------------


def test_project_eom_pct_linear_extrapolation():
    """50 hours over 15 of 30 days = 100 projected EOM = 125% of 80h cap."""
    util = _make_util(
        actual_mtd_hours=50.0,
        contracted_monthly_hours=80.0,
        mtd_days_elapsed=15,
        calendar_days_in_month=30,
    )
    assert abs(_project_eom_pct(util) - 1.25) < 0.001


def test_project_eom_pct_handles_zero_days_elapsed():
    util = _make_util(actual_mtd_hours=0.0, mtd_days_elapsed=0)
    assert _project_eom_pct(util) == 0.0


def test_project_eom_pct_handles_zero_contracted_hours():
    util = _make_util(contracted_monthly_hours=0.0)
    assert _project_eom_pct(util) == 0.0


def test_assign_bucket_over_critical():
    util = _make_util(actual_mtd_hours=50.0, contracted_monthly_hours=80.0)
    # 100h projected / 80h cap = 125% → OVER_CRITICAL
    assert _assign_bucket(util, BucketThresholds()).bucket == "OVER_CRITICAL"


def test_assign_bucket_over_warning():
    util = _make_util(actual_mtd_hours=40.0, contracted_monthly_hours=80.0)
    # 80h projected / 80h cap = 100% → OVER_WARNING (95-110%)
    assert _assign_bucket(util, BucketThresholds()).bucket == "OVER_WARNING"


def test_assign_bucket_balanced():
    util = _make_util(actual_mtd_hours=30.0, contracted_monthly_hours=80.0)
    # 60h projected / 80h cap = 75% → BALANCED (65-95%)
    assert _assign_bucket(util, BucketThresholds()).bucket == "BALANCED"


def test_assign_bucket_under_warning():
    util = _make_util(actual_mtd_hours=20.0, contracted_monthly_hours=80.0)
    # 40h projected / 80h cap = 50% → UNDER_WARNING (40-65%)
    assert _assign_bucket(util, BucketThresholds()).bucket == "UNDER_WARNING"


def test_assign_bucket_under_critical():
    util = _make_util(actual_mtd_hours=10.0, contracted_monthly_hours=80.0)
    # 20h projected / 80h cap = 25% → UNDER_CRITICAL (<40%)
    assert _assign_bucket(util, BucketThresholds()).bucket == "UNDER_CRITICAL"


def test_assign_bucket_low_confidence_under_min_days():
    util = _make_util(mtd_days_elapsed=3)  # < default low_confidence_min_days=5
    assignment = _assign_bucket(util, BucketThresholds())
    assert assignment.low_confidence is True


def test_assign_bucket_high_confidence_at_or_above_min_days():
    util = _make_util(mtd_days_elapsed=15)
    assert _assign_bucket(util, BucketThresholds()).low_confidence is False


# ---------------------------------------------------------------------------
# decide() — pure function tests
# ---------------------------------------------------------------------------


def test_decide_suppresses_when_balanced_midweek():
    utils = [_make_util(actual_mtd_hours=30.0)]  # BALANCED at 75%
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is False
    assert decision.decision_basis == "all_clients_in_balanced_or_under_warning"


def test_decide_suppresses_when_under_warning_midweek():
    utils = [_make_util(actual_mtd_hours=20.0)]  # UNDER_WARNING at 50%
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is False
    assert decision.decision_basis == "all_clients_in_balanced_or_under_warning"


def test_decide_wakes_on_monday_even_with_all_balanced():
    """The weekly mandatory boundary fires even when nothing is wrong.
    Owner relies on the absence-of-noise as a signal."""
    utils = [_make_util(actual_mtd_hours=30.0)]  # BALANCED
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=MONDAY
    )
    assert decision.wake is True
    assert decision.decision_basis == "weekly_mandatory_boundary"


def test_decide_wakes_on_over_critical_client():
    utils = [_make_util(actual_mtd_hours=50.0)]  # OVER_CRITICAL at 125%
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is True
    assert decision.decision_basis == "client_in_critical_band"
    assert decision.extra_metadata["critical_clients"][0]["bucket"] == "OVER_CRITICAL"


def test_decide_wakes_on_over_warning_client():
    """OVER_WARNING is also a critical wake bucket — the owner needs to
    know before it tips into OVER_CRITICAL."""
    utils = [_make_util(actual_mtd_hours=40.0)]  # OVER_WARNING at 100%
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is True
    assert decision.extra_metadata["critical_clients"][0]["bucket"] == "OVER_WARNING"


def test_decide_wakes_on_under_critical_client():
    utils = [_make_util(actual_mtd_hours=10.0)]  # UNDER_CRITICAL at 25%
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is True
    assert decision.extra_metadata["critical_clients"][0]["bucket"] == "UNDER_CRITICAL"


def test_decide_wakes_on_previously_critical_pending_ack():
    """Auto-promotion ban: a previously-critical client that has not been
    acknowledged continues to wake even if current bucket is balanced."""
    utils = [
        _make_util(
            actual_mtd_hours=30.0,  # currently BALANCED
            previously_critical_pending_ack=True,
        )
    ]
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is True
    assert decision.decision_basis == "previously_critical_pending_ack"
    assert decision.extra_metadata["pending_ack_clients"] == ["client_a"]


def test_decide_aggregates_multiple_critical_clients():
    utils = [
        _make_util(client_slug="a", actual_mtd_hours=50.0),  # OVER_CRITICAL
        _make_util(client_slug="b", actual_mtd_hours=10.0),  # UNDER_CRITICAL
        _make_util(client_slug="c", actual_mtd_hours=30.0),  # BALANCED
    ]
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert decision.wake is True
    critical = decision.extra_metadata["critical_clients"]
    assert len(critical) == 2
    assert {c["client_slug"] for c in critical} == {"a", "b"}


def test_decide_monday_takes_precedence_over_no_critical():
    """Monday boundary fires even if no client would otherwise wake."""
    utils = [_make_util(actual_mtd_hours=30.0)]
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=MONDAY
    )
    assert decision.decision_basis == "weekly_mandatory_boundary"


# ---------------------------------------------------------------------------
# run_once() — integration tests with FakeConnector + FakeExecutor
# ---------------------------------------------------------------------------


def _capture_stdout(coro) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = _run(coro)
    return code, buf.getvalue().strip()


def test_run_once_emits_wake_on_monday():
    connectors = [FakeConnector([_make_util(actual_mtd_hours=30.0)])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=MONDAY)
    )
    assert code == 0
    # The cadence wake carries a basis and NO plans, BY DESIGN (#2253): nothing
    # about a particular client triggered it, so there is no per-item fact to
    # hand over and the turn enumerates the roster — that is the weekly report.
    # This is NOT the blind fail-open case, and the basis is how the turn tells
    # them apart: `weekly_mandatory_boundary` is a finding-free decision the
    # gate made with the data in hand; every blind basis ends in `_fail_open`.
    parsed = json.loads(out)
    assert parsed == {
        "wakeAgent": True,
        "decision_basis": "weekly_mandatory_boundary",
    }
    assert "plans" not in parsed
    assert not parsed["decision_basis"].endswith("_fail_open")
    # The cadence wake is a real decision and leaves a row like any other
    # (#2253) — with NO plan counts, because the gate computed no per-item
    # finding. Absent counts here mean "no finding exists", the same thing the
    # absent `plans` key on the wire means.
    assert len(executor.calls) == 1
    _, params = executor.calls[0]
    assert params[2] == "EMITTED_WAKE"
    assert params[5] == "retainer-hours-reconciler"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "weekly_mandatory_boundary"
    assert "plans_total" not in metadata


def test_run_once_emits_wake_on_critical_client_midweek():
    connectors = [FakeConnector([_make_util(actual_mtd_hours=50.0)])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=TUESDAY)
    )
    assert code == 0
    # The wake line carries the facts the gate computed (#2253). A bare
    # wakeAgent flag left the woken turn to source the band and the projection
    # itself, and with the time tracker down it would source them from nowhere.
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "client_in_critical_band",
        "plans": [
            {
                "client_slug": "client_a",
                "kind": "critical_band",
                "bucket": "OVER_CRITICAL",
                "projected_eom_pct": 1.25,
                "low_confidence": False,
            }
        ],
        "plans_total": 1,
        "plans_emitted": 1,
        "plans_truncated": False,
    }
    # The wake leaves a row too (#2253), and its plan accounting matches the
    # wake line's field for field — the record kept to catch discrepancies must
    # not be a source of one.
    assert len(executor.calls) == 1
    _, params = executor.calls[0]
    assert params[2] == "EMITTED_WAKE"
    assert params[5] == "retainer-hours-reconciler"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "client_in_critical_band"
    assert metadata["plans_total"] == 1
    assert metadata["plans_emitted"] == 1
    assert metadata["plans_truncated"] is False


def test_run_once_wake_is_unchanged_when_the_emitted_wake_write_fails():
    """The inverted contract: a failed audit write must not touch the wake.

    On the suppress path an audit failure escalates to a wake, because a silent
    suppress is indistinguishable from a broken gate. Here the wake is already
    the decision, so the row is observability and never a gate — the stdout must
    be byte-identical to the succeeding case above.
    """
    connectors = [FakeConnector([_make_util(actual_mtd_hours=50.0)])]
    executor = FakeExecutor(fail=True)

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=TUESDAY)
    )
    assert code == 0
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "client_in_critical_band",
        "plans": [
            {
                "client_slug": "client_a",
                "kind": "critical_band",
                "bucket": "OVER_CRITICAL",
                "projected_eom_pct": 1.25,
                "low_confidence": False,
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

    connectors = [FakeConnector([_make_util(actual_mtd_hours=50.0)])]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: _LegacyWriter(), now=TUESDAY)
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "client_in_critical_band"
    assert len(parsed["plans"]) == 1


def test_run_once_emits_wake_with_slug_only_plans_on_pending_ack():
    """The pending-ack basis is fact-poorer by construction: `decide` returns
    on that branch BEFORE assigning buckets, so the plan carries the slug and
    nulls. Null here means "the gate computed no bucket this tick", never
    "this client is fine" — the turn reads the figure itself."""
    connectors = [
        FakeConnector(
            [_make_util(actual_mtd_hours=30.0, previously_critical_pending_ack=True)]
        )
    ]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "previously_critical_pending_ack",
        "plans": [
            {
                "client_slug": "client_a",
                "kind": "pending_ack",
                "bucket": None,
                "projected_eom_pct": None,
                "low_confidence": None,
            }
        ],
        "plans_total": 1,
        "plans_emitted": 1,
        "plans_truncated": False,
    }


def test_run_once_writes_audit_then_suppresses_on_quiet_tuesday():
    connectors = [FakeConnector([_make_util(actual_mtd_hours=30.0)])]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=TUESDAY)
    )
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}
    assert len(executor.calls) == 1
    sql, params = executor.calls[0]
    assert sql.startswith("INSERT INTO audit_log")
    assert params[2] == "SUPPRESSED_WAKE"  # action_type column
    assert params[5] == "retainer-hours-reconciler"  # skill_name column
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "all_clients_in_balanced_or_under_warning"
    assert metadata["next_scheduled_at"] == "2026-05-27T09:00:00.000Z"
    assert metadata["client_count"] == 1


def test_run_once_falls_back_to_wake_on_audit_failure():
    """The critical safety contract: audit-write failure forces wake.

    Without this, a silent suppress is structurally indistinguishable
    from a silently-broken pre_run.py — exactly the failure mode the
    Devil's Advocate critique flagged.

    Asserted by exact equality with NO ``plans`` key: pre-#2253 every wake path
    printed the same bare flag, so a blind fail-open and a fact-carrying wake
    were indistinguishable on the wire — which is precisely how a fact-free turn
    could read as a well-briefed one.
    """
    connectors = [FakeConnector([_make_util(actual_mtd_hours=30.0)])]
    executor = FakeExecutor(fail=True)

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=TUESDAY)
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed == {
        "wakeAgent": True,
        "decision_basis": "suppress_heartbeat_failed_fail_open",
    }
    assert "plans" not in parsed  # woke blind: SKILL.md's enumeration fallback applies
    assert len(executor.calls) == 1  # the attempt was made before fallback


def test_run_once_falls_back_to_wake_when_writer_factory_returns_none():
    """No writer wired (dev mode / missing env) → wake. The contract is
    that suppress requires a trail; absent a trail, always wake."""
    connectors = [FakeConnector([_make_util(actual_mtd_hours=30.0)])]

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed == {
        "wakeAgent": True,
        "decision_basis": "no_audit_writer_fail_open",
    }
    assert "plans" not in parsed


def test_run_once_suppresses_with_multiple_clients_all_balanced():
    """Realistic agency: 5 clients, all balanced, on a quiet Tuesday.
    Exactly one audit row written; no agent wake."""
    connectors = [
        FakeConnector(
            [
                _make_util(client_slug=f"client_{i}", actual_mtd_hours=30.0)
                for i in range(5)
            ]
        )
    ]
    executor = FakeExecutor()

    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), factory, now=TUESDAY)
    )
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}
    assert len(executor.calls) == 1
    metadata = json.loads(executor.calls[0][1][11])
    assert metadata["client_count"] == 5


# ---------------------------------------------------------------------------
# The wake payload (#2253) — the handoff Hermes injects verbatim into the
# woken turn's prompt. What is absent here is what the turn has to invent.
# Ported from the escalator's fix (PR #2259). Three wake bases, unequal fact
# richness, all three distinguishable on the wire.
# ---------------------------------------------------------------------------


def test_wake_payload_carries_every_critical_client_with_its_band():
    """Each firing client lands with the band and the projection the gate
    computed, so the Slack post states figures it was handed rather than
    figures it re-derived."""
    connectors = [
        FakeConnector(
            [
                _make_util(client_slug="a", actual_mtd_hours=50.0),  # OVER_CRITICAL
                _make_util(client_slug="b", actual_mtd_hours=10.0),  # UNDER_CRITICAL
                _make_util(client_slug="c", actual_mtd_hours=30.0),  # BALANCED
            ]
        )
    ]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    plans = {p["client_slug"]: p for p in json.loads(out)["plans"]}
    assert set(plans) == {"a", "b"}  # c is BALANCED — not a finding
    assert plans["a"]["bucket"] == "OVER_CRITICAL"
    assert plans["b"]["bucket"] == "UNDER_CRITICAL"
    assert plans["a"]["projected_eom_pct"] == 1.25
    assert all(p["kind"] == "critical_band" for p in plans.values())


def test_wake_payload_flags_a_low_confidence_projection():
    """Three elapsed days extrapolated to a month is a weaker claim than
    fifteen, and the gate already knows which it made. Handing the percentage
    over without the flag invites the turn to state both identically."""
    connectors = [
        FakeConnector([_make_util(actual_mtd_hours=12.0, mtd_days_elapsed=3)])
    ]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    assert json.loads(out)["plans"][0]["low_confidence"] is True


def test_wake_payload_truncation_announces_itself():
    """Over the cap the list is partial, and the payload says so. A truncated
    list that reads as complete is a check that cannot fail (Law 12)."""
    connectors = [
        FakeConnector(
            [
                _make_util(client_slug=f"client_{i}", actual_mtd_hours=50.0)
                for i in range(58)
            ]
        )
    ]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["plans_total"] == 58
    assert parsed["plans_emitted"] == 50
    assert parsed["plans_truncated"] is True
    assert len(parsed["plans"]) == 50


def test_wake_payload_untruncated_says_so_explicitly():
    """The flag is present on the complete case too, so its absence never has
    to be read as "complete"."""
    connectors = [
        FakeConnector(
            [
                _make_util(client_slug=f"client_{i}", actual_mtd_hours=50.0)
                for i in range(4)
            ]
        )
    ]
    code, out = _capture_stdout(
        run_once(connectors, BucketThresholds(), lambda: None, now=TUESDAY)
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["plans_total"] == parsed["plans_emitted"] == 4
    assert parsed["plans_truncated"] is False


def test_decide_plans_mirror_the_critical_set():
    utils = [
        _make_util(client_slug="a", actual_mtd_hours=50.0),  # OVER_CRITICAL
        _make_util(client_slug="b", actual_mtd_hours=10.0),  # UNDER_CRITICAL
        _make_util(client_slug="c", actual_mtd_hours=30.0),  # BALANCED
    ]
    decision = decide(
        utils, BucketThresholds(), raw_inputs_for_digest=b"x", now=TUESDAY
    )
    assert {p.client_slug for p in decision.plans} == {"a", "b"}
    assert len(decision.plans) == len(decision.extra_metadata["critical_clients"])


def test_decide_weekly_boundary_carries_no_plans_by_design():
    """The cadence wake has no per-item finding to hand over — the absence is
    the design, not blindness, and the basis says which: it does NOT end in
    `_fail_open`, so the turn reads it as "enumerate the roster, that is the
    weekly report", not as "the gate could not see"."""
    decision = decide(
        [_make_util(actual_mtd_hours=50.0)],  # would otherwise be OVER_CRITICAL
        BucketThresholds(),
        raw_inputs_for_digest=b"x",
        now=MONDAY,
    )
    assert decision.wake is True
    assert decision.decision_basis == "weekly_mandatory_boundary"
    assert decision.plans == ()
    assert not decision.decision_basis.endswith("_fail_open")


def test_decide_suppressed_decision_carries_no_plans():
    decision = decide(
        [_make_util(actual_mtd_hours=30.0)],
        BucketThresholds(),
        raw_inputs_for_digest=b"x",
        now=TUESDAY,
    )
    assert decision.wake is False
    assert decision.plans == ()
