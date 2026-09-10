"""Tests for client-verification-tracker/pre_run.py (WP-B, #1889).

Exercises the bespoke cadence/ceiling gate that graduated this skill off the
shared empty-seat template: cadence suppression, ceiling wake-once + handed_off
terminal, unauthored-config single surface, ledger-unreadable fire-open, the
nudge numerator, and the per-skill settings config read. Fake source + fake
executor; no network, no OAuth, no D1.

Mirrors `deadline-miss-escalator/test_escalator_pre_run.py` — same harness,
adapted for chase cadence instead of deadline proximity.

Run from repo root:

    cd operator && python -m pytest \\
        skills/client-verification-tracker/test_verification_pre_run.py -v
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# operator/ on sys.path (for adapter.*). Load this skill's pre_run.py under a
# unique module name — every Stream B skill names the file pre_run.py, so a bare
# import would collide across skills.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from adapter.audit_log import AuditLogWriter, SuppressedWakeWriter  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

_PRE_RUN_PATH = _HERE.parent / "pre_run.py"
_spec = importlib.util.spec_from_file_location("cvt_pre_run", _PRE_RUN_PATH)
assert _spec is not None and _spec.loader is not None
_pre_run = importlib.util.module_from_spec(_spec)
sys.modules["cvt_pre_run"] = _pre_run
_spec.loader.exec_module(_pre_run)

ChaseConfig = _pre_run.ChaseConfig
VerificationItem = _pre_run.VerificationItem
decide = _pre_run.decide
run_once = _pre_run.run_once
load_chase_config = _pre_run.load_chase_config
parse_pull = _pre_run.parse_pull
ACTION_CHASE = _pre_run.ACTION_CHASE
ACTION_HANDOFF = _pre_run.ACTION_HANDOFF
ACTION_SURFACE_CONFIG = _pre_run.ACTION_SURFACE_CONFIG
ACTION_SURFACE_HOLD = _pre_run.ACTION_SURFACE_HOLD
HOLD_SOURCE_ID = _pre_run.HOLD_SOURCE_ID

# The vendored ledger module the skill loads at runtime — used here to mint real
# events so the tests exercise the true item_key/state join.
_LEDGER_PATH = _HERE.parent / "escalation_ledger.py"
_lspec = importlib.util.spec_from_file_location("cvt_ledger_test", _LEDGER_PATH)
_ledger = importlib.util.module_from_spec(_lspec)
sys.modules["cvt_ledger_test"] = _ledger
_lspec.loader.exec_module(_ledger)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSource:
    def __init__(self, items):
        self._items = items

    def pull_open_verifications(self):
        return self._items


class FakeExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list]] = []

    async def execute(self, sql: str, params: list) -> None:
        self.calls.append((sql, params))
        if self.fail:
            raise RuntimeError("D1 unreachable")


TODAY = date(2026, 7, 14)
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
_CFG = ChaseConfig(chase_cadence_days=5, escalate_after_attempts=3)
_REFIRE = 3


def _item(
    *,
    matter_id: str = "m-1",
    task_id: str | None = "task-1",
    authored_date: date | None = None,  # mirrors production: identity is task_id, not a moving date
    next_chase_due: date | None = None,
    label: str = "client-verification",
) -> VerificationItem:
    return VerificationItem(
        matter_id=matter_id,
        task_id=task_id,
        authored_date=authored_date,
        next_chase_due=next_chase_due or TODAY,
        label=label,
    )


def _chased_event(item, *, ts, attempt):
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    return _ledger.make_event(
        skill="client-verification-tracker",
        matter_id=item.matter_id,
        item_key=key,
        event="chased",
        attempt=attempt,
        token=_ledger.token_for(key),
        ts=ts,
    )


def _handed_off_event(item, *, ts, attempt):
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    return _ledger.make_event(
        skill="client-verification-tracker",
        matter_id=item.matter_id,
        item_key=key,
        event="handed_off",
        attempt=attempt,
        ts=ts,
    )


def _resolved_event(item, *, ts):
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    return _ledger.make_event(
        skill="client-verification-tracker",
        matter_id=item.matter_id,
        item_key=key,
        event="resolved",
        attempt=0,
        ts=ts,
    )


def _decide(items, events, *, config=_CFG, today=TODAY):
    return decide(
        items,
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
# decide() — cadence (condition a)
# ---------------------------------------------------------------------------


def test_new_item_first_chase_due_when_task_date_arrived():
    # No prior chase; the tracking task's authored due date is today → chase due.
    d = _decide([_item(next_chase_due=TODAY)], [])
    assert d.wake is True
    assert d.plans[0].action == ACTION_CHASE
    assert d.plans[0].attempt == 1  # first nudge


def test_new_item_first_chase_not_due_before_task_date():
    d = _decide([_item(next_chase_due=TODAY + timedelta(days=2))], [])
    assert d.wake is False
    assert d.decision_basis == "no_verification_action_due"


def test_chase_suppressed_within_cadence_window():
    # Chased 2 days ago, cadence 5 → not due yet.
    item = _item()
    events = [_chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=1)]
    d = _decide([item], events)
    assert d.wake is False


def test_chase_refires_after_cadence_window():
    # Chased 5 days ago, cadence 5 → due again.
    item = _item()
    events = [_chased_event(item, ts="2026-07-09T09:00:00.000Z", attempt=1)]
    d = _decide([item], events)
    assert d.wake is True
    assert d.plans[0].action == ACTION_CHASE
    assert d.plans[0].attempt == 2  # nudge 2 of 3


def test_nudge_numerator_counts_prior_chases():
    # Two prior chases, last one 6 days ago → the next chase is nudge 3 of 3.
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-02T09:00:00.000Z", attempt=1),
        _chased_event(item, ts="2026-07-08T09:00:00.000Z", attempt=2),
    ]
    d = _decide([item], events)
    assert d.wake is True
    assert d.plans[0].attempt == 3
    assert d.extra_metadata["items"][0]["ceiling"] == 3


# ---------------------------------------------------------------------------
# decide() — attempt ceiling (condition b)
# ---------------------------------------------------------------------------


def test_ceiling_reached_wakes_once_to_hand_off():
    # Three chases (= ceiling) unanswered → stop chasing, hand off.
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-02T09:00:00.000Z", attempt=1),
        _chased_event(item, ts="2026-07-07T09:00:00.000Z", attempt=2),
        _chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=3),
    ]
    d = _decide([item], events)
    assert d.wake is True
    assert d.plans[0].action == ACTION_HANDOFF
    assert d.extra_metadata["handoff_due"] == 1
    assert d.extra_metadata["chase_due"] == 0


def test_handed_off_is_terminal_and_suppresses():
    # Ceiling reached AND already handed off → quiet (a person owns it now).
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-02T09:00:00.000Z", attempt=1),
        _chased_event(item, ts="2026-07-07T09:00:00.000Z", attempt=2),
        _chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=3),
        _handed_off_event(item, ts="2026-07-12T09:05:00.000Z", attempt=3),
    ]
    d = _decide([item], events)
    assert d.wake is False


def test_resolved_is_terminal_and_suppresses():
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-09T09:00:00.000Z", attempt=1),
        _resolved_event(item, ts="2026-07-11T09:00:00.000Z"),
    ]
    d = _decide([item], events)
    assert d.wake is False


# ---------------------------------------------------------------------------
# decide() — unauthored config (condition c): surface, hold, re-fire until
# authored (#1899). Never daily, never once-ever.
# ---------------------------------------------------------------------------


def _config_sentinel_fired(*, ts="2026-07-10T09:00:00.000Z", attempt=1):
    key = _ledger.item_key("", "__chase_config__", "chase-config-missing", "")
    return _ledger.make_event(
        skill="client-verification-tracker",
        matter_id=None,
        item_key=key,
        event="fired",
        attempt=attempt,
        ts=ts,
    )


def test_unauthored_config_surfaces():
    # Cadence missing → surface, no chase of the open item.
    d = _decide([_item(next_chase_due=TODAY)], [], config=ChaseConfig(escalate_after_attempts=3))
    assert d.wake is True
    assert d.plans[0].action == ACTION_SURFACE_CONFIG
    assert d.plans[0].attempt == 1  # first surface
    assert "chase_cadence_days" in d.extra_metadata["missing"]


def test_unauthored_config_quiet_within_refire_window():
    # Surfaced 2 days ago, refire window 3 → hold quiet, do not re-surface yet.
    d = _decide(
        [_item(next_chase_due=TODAY)],
        [_config_sentinel_fired(ts="2026-07-12T09:00:00.000Z")],
        config=ChaseConfig(chase_cadence_days=5),  # ceiling missing
    )
    assert d.wake is False
    assert d.decision_basis == "chase_config_unauthored_within_refire_window"


def test_unauthored_config_resurfaces_after_refire_window():
    # Surfaced 4 days ago, refire window 3, dials still unauthored → re-surface
    # (#1899: a held chase must not go permanently dark on one missed notice).
    d = _decide(
        [_item(next_chase_due=TODAY)],
        [_config_sentinel_fired(ts="2026-07-10T09:00:00.000Z")],
        config=ChaseConfig(chase_cadence_days=5),  # ceiling missing
    )
    assert d.wake is True
    assert d.plans[0].action == ACTION_SURFACE_CONFIG
    assert d.plans[0].attempt == 2  # second surface, numbered from the ledger


def test_unauthored_config_ack_snoozes_the_surface():
    # Staff acked the config notice yesterday → snoozed (ack window = refire
    # window), not re-fired, and still no chase.
    key = _ledger.item_key("", "__chase_config__", "chase-config-missing", "")
    events = [
        _config_sentinel_fired(ts="2026-07-10T09:00:00.000Z"),
        _ledger.make_event(
            skill="client-verification-tracker",
            matter_id=None,
            item_key=key,
            event="acked",
            attempt=1,
            ts="2026-07-13T09:00:00.000Z",
        ),
    ]
    d = _decide([_item(next_chase_due=TODAY)], events, config=ChaseConfig())
    assert d.wake is False
    assert d.decision_basis == "chase_config_unauthored_within_refire_window"


def test_unauthored_config_never_chases():
    # Even with an item long overdue for a chase, unauthored config holds.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    d = _decide([item], [], config=ChaseConfig())
    assert d.plans[0].action == ACTION_SURFACE_CONFIG  # never ACTION_CHASE


# ---------------------------------------------------------------------------
# decide() — per-MATTER hold (condition d, ss #2402): an open hold blocks
# chase AND hand-off for every item on the matter; it re-surfaces on the
# re-fire window; only a resolved hold releases the chase. Founding case:
# signer unresolved (2026-08-11 the turn surfaced the hold in an email only,
# and the 2026-08-14 wake planned a chase to the unconfirmed signer).
# ---------------------------------------------------------------------------


def _hold_event(item, *, event="fired", ts, attempt=1):
    # Matter-level identity: the hold names the MATTER, not the tracking task.
    key = _ledger.item_key(item.matter_id, HOLD_SOURCE_ID, "chase-hold", None)
    return _ledger.make_event(
        skill="client-verification-tracker",
        matter_id=item.matter_id,
        item_key=key,
        event=event,
        attempt=attempt,
        ts=ts,
    )


def test_hold_blocks_a_due_chase_and_resurfaces():
    # Chase long overdue, but a hold fired 4 days ago (refire window 3) →
    # the plan is a re-surface of the hold, never a chase.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [_hold_event(item, ts="2026-07-10T09:00:00.000Z")]
    d = _decide([item], events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]
    assert d.plans[0].attempt == 2  # second surface, numbered from the ledger
    assert d.extra_metadata["hold_surface_due"] == 1


def test_hold_quiet_within_refire_window_still_blocks_chase():
    # Hold surfaced yesterday → nothing re-fires, and the due chase stays blocked.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [_hold_event(item, ts="2026-07-13T09:00:00.000Z")]
    d = _decide([item], events)
    assert d.wake is False
    assert d.decision_basis == "no_verification_action_due"


def test_acked_hold_snoozes_the_surface_but_still_blocks():
    # Ack means "a person saw it", not "the signer is confirmed" → no chase.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-07-08T09:00:00.000Z"),
        _hold_event(item, event="acked", ts="2026-07-13T09:00:00.000Z"),
    ]
    d = _decide([item], events)
    assert d.wake is False


def test_resolved_hold_releases_the_chase():
    # The falsifier (Law 12): same item, hold resolved → the chase plans again.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-07-08T09:00:00.000Z"),
        _hold_event(item, event="resolved", ts="2026-07-12T09:00:00.000Z"),
    ]
    d = _decide([item], events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_CHASE]


def test_handed_off_hold_blocks_and_goes_quiet():
    # A hold handed to a person: no chase, and no autonomous re-surface either.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-07-01T09:00:00.000Z"),
        _hold_event(item, event="handed_off", ts="2026-07-02T09:00:00.000Z"),
    ]
    d = _decide([item], events)
    assert d.wake is False


def test_hold_blocks_the_ceiling_handoff_too():
    # Attempts at ceiling AND a hold → the ambiguity precedes the count: no
    # hand-off plan while the hold is open, only the hold surface.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _chased_event(item, ts="2026-06-20T09:00:00.000Z", attempt=1),
        _chased_event(item, ts="2026-06-25T09:00:00.000Z", attempt=2),
        _chased_event(item, ts="2026-06-30T09:00:00.000Z", attempt=3),
        _hold_event(item, ts="2026-07-10T09:00:00.000Z"),
    ]
    d = _decide([item], events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]


def test_hold_on_one_matter_does_not_block_another():
    held = _item(matter_id="m-1", task_id="task-1", next_chase_due=TODAY - timedelta(days=30))
    free = _item(matter_id="m-2", task_id="task-2", next_chase_due=TODAY)
    events = [_hold_event(held, ts="2026-07-13T09:00:00.000Z")]
    d = _decide([held, free], events)
    assert d.wake is True
    assert {(p.matter_id, p.action) for p in d.plans} == {("m-2", ACTION_CHASE)}


def test_hold_survives_tracking_task_recreation():
    # The blocker is a fact about the MATTER. A hold written while task-1 was
    # the tracking task must still block a REPLACEMENT task-9 on the same
    # matter — a task-keyed hold would evaporate here and the first wake on
    # the new task would chase straight past the unresolved signer.
    original = _item(matter_id="m-1", task_id="task-1", next_chase_due=TODAY)
    events = [_hold_event(original, ts="2026-07-13T09:00:00.000Z")]
    replacement = _item(matter_id="m-1", task_id="task-9", next_chase_due=TODAY)
    d = _decide([replacement], events)
    assert d.wake is False  # within the refire window: quiet, and no chase


def test_held_matter_with_two_items_surfaces_once():
    a = _item(matter_id="m-1", task_id="task-1", next_chase_due=TODAY - timedelta(days=30))
    b = _item(matter_id="m-1", task_id="task-2", next_chase_due=TODAY - timedelta(days=30))
    events = [_hold_event(a, ts="2026-07-10T09:00:00.000Z")]
    d = _decide([a, b], events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]  # one, not two


# ---------------------------------------------------------------------------
# decide() — a raise after a resolved hold RE-ACTIVATES it (the symmetric
# reset in the shared ledger). Live sequence off the pilot: the hold fired
# 08-24, resolved 08-27, and the 08-31 turn re-raised it; before the fix the
# fold kept `resolved` sticky and the next wake planned a chase straight past
# the re-surfaced blocker (the investigate -> re-block -> chase loop).
# ---------------------------------------------------------------------------


def test_hold_reactivated_by_fired_after_resolved():
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z", attempt=1),
        _hold_event(item, event="resolved", ts="2026-06-27T14:00:00.000Z"),
        _hold_event(item, ts="2026-07-10T09:00:00.000Z", attempt=2),
    ]
    # Re-raised 4 days ago (refire window 3): the hold re-surfaces, the due
    # chase stays blocked — never ACTION_CHASE.
    d = _decide([item], events)
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]


def test_hold_reactivated_within_refire_window_blocks_quietly():
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z", attempt=1),
        _hold_event(item, event="resolved", ts="2026-06-27T14:00:00.000Z"),
        _hold_event(item, ts="2026-07-13T09:00:00.000Z", attempt=2),
    ]
    d = _decide([item], events)
    assert d.wake is False  # within the window: quiet, and still no chase
    assert d.decision_basis == "no_verification_action_due"


# ---------------------------------------------------------------------------
# decide() — the signer determination consult (ss #2402 Part 3): the current
# role-snapshot hash rides every hold surface; a determination whose recorded
# hash no longer matches the live roles swaps a due chase for a hold surface.
# ---------------------------------------------------------------------------

_SNAP_A = "aa" * 32
_SNAP_B = "bb" * 32

_DETERMINATION = {
    "note": "plaintiff is a single adult; Minor/Deceased tags are layout artifacts",
    "role_snapshot_sha256": _SNAP_A,
    "confirmed_via": "matter_record",
}


def _hold_resolved_with_determination(item, *, ts, determination=_DETERMINATION):
    row = _hold_event(item, event="resolved", ts=ts)
    row["determination"] = determination
    return row


def _decide_with_hashes(items, events, hashes, *, config=_CFG, today=TODAY):
    return decide(
        items,
        config,
        _ledger,
        events,
        raw_inputs_for_digest=b"x",
        today=today,
        refire_days=_REFIRE,
        role_snapshot_hashes=hashes,
    )


def test_surface_hold_plan_carries_current_snapshot_hash():
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [_hold_event(item, ts="2026-07-10T09:00:00.000Z")]
    d = _decide_with_hashes([item], events, {"m-1": _SNAP_A})
    assert d.plans[0].action == ACTION_SURFACE_HOLD
    assert d.plans[0].current_role_snapshot_sha256 == _SNAP_A


def test_surface_hold_plan_carries_null_hash_when_pull_failed():
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [_hold_event(item, ts="2026-07-10T09:00:00.000Z")]
    d = _decide_with_hashes([item], events, {"m-1": None})
    assert d.plans[0].action == ACTION_SURFACE_HOLD
    assert d.plans[0].current_role_snapshot_sha256 is None


def test_current_determination_rides_the_chase_plan():
    # Hold resolved with a determination, roles unchanged (hash matches) → the
    # chase plans normally, stamped so an ambiguous fresh derivation can adopt
    # the recorded determination and cite it.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z"),
        _hold_resolved_with_determination(item, ts="2026-06-27T14:00:00.000Z"),
    ]
    d = _decide_with_hashes([item], events, {"m-1": _SNAP_A})
    assert [p.action for p in d.plans] == [ACTION_CHASE]
    assert d.plans[0].determination["status"] == "current"
    assert d.plans[0].determination["note"] == _DETERMINATION["note"]
    assert d.plans[0].determination["recorded_sha256"] == _SNAP_A


def test_stale_determination_swaps_chase_for_hold_surface():
    # The roles moved since the determination was recorded → never chase on
    # either reading; surface the discrepancy on the HOLD key so the turn's
    # fresh `fired` re-activates the hold.
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z"),
        _hold_resolved_with_determination(item, ts="2026-06-27T14:00:00.000Z"),
    ]
    d = _decide_with_hashes([item], events, {"m-1": _SNAP_B})
    assert d.wake is True
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]
    plan = d.plans[0]
    assert plan.reason == "determination_stale"
    assert plan.item_key == _ledger.item_key("m-1", HOLD_SOURCE_ID, "chase-hold", None)
    assert plan.current_role_snapshot_sha256 == _SNAP_B
    assert plan.determination["status"] == "stale"
    assert d.extra_metadata["items"][0]["reason"] == "determination_stale"


def test_stale_determination_surfaces_once_for_two_items():
    a = _item(matter_id="m-1", task_id="task-1", next_chase_due=TODAY - timedelta(days=30))
    b = _item(matter_id="m-1", task_id="task-2", next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(a, ts="2026-06-24T14:00:00.000Z"),
        _hold_resolved_with_determination(a, ts="2026-06-27T14:00:00.000Z"),
    ]
    d = _decide_with_hashes([a, b], events, {"m-1": _SNAP_B})
    assert [p.action for p in d.plans] == [ACTION_SURFACE_HOLD]  # one, not two


def test_unknown_snapshot_degrades_to_stamped_chase():
    # Pull failed (hash unknown) → fail toward the turn deciding: the chase
    # plans, stamped status "unknown" so the turn treats it as no
    # determination (fresh derivation; ambiguity → hold).
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z"),
        _hold_resolved_with_determination(item, ts="2026-06-27T14:00:00.000Z"),
    ]
    d = _decide_with_hashes([item], events, {"m-1": None})
    assert [p.action for p in d.plans] == [ACTION_CHASE]
    assert d.plans[0].determination["status"] == "unknown"


def test_no_determination_means_no_stamp():
    item = _item(next_chase_due=TODAY - timedelta(days=30))
    events = [
        _hold_event(item, ts="2026-06-24T14:00:00.000Z"),
        _hold_event(item, event="resolved", ts="2026-06-27T14:00:00.000Z"),
    ]
    d = _decide_with_hashes([item], events, {"m-1": _SNAP_A})
    assert [p.action for p in d.plans] == [ACTION_CHASE]
    assert d.plans[0].determination is None


def test_hold_source_id_literal_is_the_cross_repo_contract():
    """The overlay's escalation plugin keys hold-release enforcement on this
    exact literal (hermes-smd-overlay plugins/hermes-smd-escalation): a derive
    whose source_id is `__hold__` and whose event is `resolved` must carry the
    determination. A drifted literal here would silently exempt every hold."""
    assert HOLD_SOURCE_ID == "__hold__"


# ---------------------------------------------------------------------------
# The role-snapshot projection + hash — pinned against the LIVE probe payload
# (pilot-smokeball staging 2026-08-31, vfy_01M1CB0NTKCV3ACRY0P6QD6JX7;
# fixture tests/role_snapshot_probe.json).
# ---------------------------------------------------------------------------

_FIXTURE_PATH = _HERE.parent / "tests" / "role_snapshot_probe.json"

# The projection/hash live in the sibling role_snapshot.py, loaded the same
# way the runtime loads it (through pre_run's sibling loader), so these tests
# exercise the real staging path rather than a direct import.
_role_snapshot = _pre_run._load_role_snapshot_module()
assert _role_snapshot is not None, "role_snapshot.py sibling must load from the skill dir"


def _load_probe_fixture():
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _shuffle(value, *, reverse):
    """Deep-rebuild a payload with reversed key insertion order and reversed
    list order — the same content a different wire ordering would produce."""
    if isinstance(value, dict):
        keys = list(value)
        if reverse:
            keys = list(reversed(keys))
        return {k: _shuffle(value[k], reverse=reverse) for k in keys}
    if isinstance(value, list):
        items = [_shuffle(v, reverse=reverse) for v in value]
        return list(reversed(items)) if reverse else items
    return value


def test_snapshot_hash_is_stable_across_field_order():
    fixture = _load_probe_fixture()
    base = _role_snapshot.role_snapshot_hash(
        _role_snapshot.role_snapshot_projection(fixture["matter"], fixture["roles"])
    )
    shuffled = _role_snapshot.role_snapshot_hash(
        _role_snapshot.role_snapshot_projection(
            _shuffle(fixture["matter"], reverse=True), _shuffle(fixture["roles"], reverse=True)
        )
    )
    assert base == shuffled


def test_snapshot_hash_moves_when_a_role_fact_moves():
    """The falsifier: a hash that never changes measures nothing. Re-linking a
    role's contact must move the hash."""
    fixture = _load_probe_fixture()
    base = _role_snapshot.role_snapshot_hash(
        _role_snapshot.role_snapshot_projection(fixture["matter"], fixture["roles"])
    )
    mutated = json.loads(json.dumps(fixture["roles"]))
    mutated["roles"][0]["contactId"] = "00000000-0000-0000-0000-000000000000"
    mutated["roles"][0]["contact"]["id"] = "00000000-0000-0000-0000-000000000000"
    assert (
        _role_snapshot.role_snapshot_hash(
            _role_snapshot.role_snapshot_projection(fixture["matter"], mutated)
        )
        != base
    )


def test_snapshot_hash_moves_when_a_structural_slot_appears():
    """The F13 conflict source is the layout's structural Minor/Deceased slots
    under matter.items — adding or removing one is exactly the fact change a
    determination was derived from."""
    fixture = _load_probe_fixture()
    base = _role_snapshot.role_snapshot_hash(
        _role_snapshot.role_snapshot_projection(fixture["matter"], fixture["roles"])
    )
    mutated = json.loads(json.dumps(fixture["matter"]))
    plaintiff = mutated["items"]["Plaintiff"][0]
    del plaintiff["subItems"]["Minor"]  # the structural slot disappears
    assert (
        _role_snapshot.role_snapshot_hash(_role_snapshot.role_snapshot_projection(mutated, fixture["roles"]))
        != base
    )


def test_snapshot_hash_ignores_volatile_fields():
    """href/versionId/title/description/status churn must NOT invalidate a
    determination — only role facts may."""
    fixture = _load_probe_fixture()
    base = _role_snapshot.role_snapshot_hash(
        _role_snapshot.role_snapshot_projection(fixture["matter"], fixture["roles"])
    )
    mutated = json.loads(json.dumps(fixture["matter"]))
    mutated["versionId"] = "999999999999999999"
    mutated["href"] = "https://elsewhere.example/matters/x"
    mutated["description"] = "edited"
    mutated["status"] = "Closed"
    assert (
        _role_snapshot.role_snapshot_hash(_role_snapshot.role_snapshot_projection(mutated, fixture["roles"]))
        == base
    )


def test_run_once_pulls_hashes_only_for_hold_bearing_matters():
    """The pull is a second connector subprocess on a 1 vCPU seat: only
    matters with hold-sentinel history pay for it, serialized."""
    held = _item(matter_id="m-1", task_id="task-1", next_chase_due=TODAY - timedelta(days=30))
    free = _item(matter_id="m-2", task_id="task-2", next_chase_due=TODAY)
    events = [_hold_event(held, ts="2026-07-10T09:00:00.000Z")]
    pulled: list[str] = []

    def fake_hash(matter_id):
        pulled.append(matter_id)
        return _SNAP_A

    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([held, free])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
            snapshot_hash_fn=fake_hash,
        )
    )
    assert code == 0
    assert pulled == ["m-1"]  # never the hold-free matter
    parsed = json.loads(out)
    hold_plans = [p for p in parsed["plans"] if p["action"] == "surface_hold"]
    assert hold_plans and hold_plans[0]["current_role_snapshot_sha256"] == _SNAP_A


# ---------------------------------------------------------------------------
# run_once() — integration + fail-open
# ---------------------------------------------------------------------------


def _factory(executor):
    def factory():
        return SuppressedWakeWriter(AuditLogWriter(executor))

    return factory


def test_run_once_wakes_on_chase_due():
    item = _item(next_chase_due=TODAY)
    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    # The wake line carries the gate's plans — the woken turn's work list
    # (#2226): a bare wakeAgent flag left the agent to re-derive targeting it
    # structurally cannot (a NEW item has no ledger state to scan from).
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "verification_action_due"
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    assert parsed["plans"] == [
        {
            "matter_id": "m-1",
            "matter_number": None,
            "matter_number_absent": None,
            "next_chase_due": item.next_chase_due.isoformat(),
            "task_id": "task-1",
            "item_key": key,
            "action": "chase",
            "attempt": 1,
        }
    ]
    # The wake leaves a row too (#2253). Before this, the gate logged why it did
    # NOT act and logged nothing when it did, so the one tick that mattered was
    # the one tick the ledger could not show.
    assert len(executor.calls) == 1
    _, params = executor.calls[0]
    assert params[2] == "EMITTED_WAKE"
    assert params[5] == "client-verification-tracker"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "verification_action_due"
    assert metadata["plans_total"] == 1
    # This gate serializes the whole plan list (no cap), so it does NOT claim an
    # emitted/truncated split — a constant dressed as a measurement is a check
    # that cannot fail.
    assert "plans_emitted" not in metadata
    assert "plans_truncated" not in metadata


def test_run_once_wake_is_unchanged_when_the_emitted_wake_write_fails():
    """The inverted contract: a failed audit write must not touch the wake.

    On the suppress path an audit failure escalates to a wake, because a silent
    suppress is indistinguishable from a broken gate. Here the wake is already
    the decision, so the row is observability and never a gate.
    """
    item = _item()
    executor = FakeExecutor(fail=True)
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "verification_action_due"
    key = _ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
    assert parsed["plans"] == [
        {
            "matter_id": "m-1",
            "matter_number": None,
            "matter_number_absent": None,
            "next_chase_due": item.next_chase_due.isoformat(),
            "task_id": "task-1",
            "item_key": key,
            "action": "chase",
            "attempt": 1,
        }
    ]
    assert len(executor.calls) == 1  # attempted, failed, swallowed


def test_run_once_wake_survives_a_writer_without_the_emitted_wake_method():
    """A writer object too old to have `write_emitted_wake` must not break a
    wake. The failure mode this closes is a half-deployed image, where the
    gate's own observability would otherwise take the tick down with it."""

    class _LegacyWriter:
        async def write_suppressed_wake(self, **_kwargs) -> str:
            return "x"

    code, out = _capture_stdout(
        run_once(
            [FakeSource([_item()])],
            lambda: _LegacyWriter(),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "verification_action_due"
    assert len(parsed["plans"]) == 1


def test_run_once_suppresses_within_cadence_and_writes_heartbeat():
    item = _item()
    events = [_chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=1)]
    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
        )
    )
    assert code == 0
    assert json.loads(out) == {"wakeAgent": False}
    assert len(executor.calls) == 1  # the SUPPRESSED_WAKE heartbeat row
    sql, params = executor.calls[0]
    assert sql.startswith("INSERT INTO audit_log")
    assert params[2] == "SUPPRESSED_WAKE"
    assert params[5] == "client-verification-tracker"
    metadata = json.loads(params[11])
    assert metadata["decision_basis"] == "no_verification_action_due"


def test_run_once_fires_open_when_ledger_unavailable(monkeypatch):
    """Fail-open: a chase watcher that goes silent is the dangerous failure, so a
    ledger that cannot be loaded wakes rather than suppresses.

    ``ledger_module=None`` alone does NOT simulate the failure — it makes
    ``run_once`` call ``_load_ledger_module()``, which succeeds against the
    sibling ledger file in this repo. The pre-#2226 bare-flag stdout made the
    two paths indistinguishable, so this test passed while exercising the
    normal wake path. The loader itself must fail.
    """
    monkeypatch.setattr(_pre_run, "_load_ledger_module", lambda: None)
    item = _item()
    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=None,  # forces the (patched) loader
            ledger_events=None,
        )
    )
    assert code == 0
    # Fail-open wakes carry a basis but NO plans: the agent is told it woke
    # blind so SKILL.md's full-enumeration fallback applies.
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "ledger_unavailable_fail_open",
    }
    assert executor.calls == []  # never reached the suppress/heartbeat path


def test_run_once_falls_back_to_wake_on_audit_failure():
    """The dead-man's-switch: a heartbeat write failure forces wake."""
    item = _item()
    events = [_chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=1)]
    executor = FakeExecutor(fail=True)
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
        )
    )
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "suppress_heartbeat_failed_fail_open",
    }
    assert len(executor.calls) == 1  # attempt made before fallback


def test_run_once_falls_back_to_wake_when_no_writer():
    item = _item()
    events = [_chased_event(item, ts="2026-07-12T09:00:00.000Z", attempt=1)]
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            lambda: None,
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
        )
    )
    assert json.loads(out) == {
        "wakeAgent": True,
        "decision_basis": "no_audit_writer_fail_open",
    }


def test_run_once_wake_emits_handoff_and_config_plans():
    """Every plan action serializes, not just chase: the ceiling hand-off and
    the config-missing surface reach the agent the same way (#2226)."""
    item = _item()
    events = [
        _chased_event(item, ts="2026-07-01T09:00:00.000Z", attempt=n) for n in (1, 2, 3)
    ]
    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=events,
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["wakeAgent"] is True
    assert parsed["decision_basis"] == "verification_action_due"
    assert [p["action"] for p in parsed["plans"]] == ["handoff"]
    assert parsed["plans"][0]["matter_id"] == "m-1"

    unauthored = ChaseConfig(chase_cadence_days=None, escalate_after_attempts=None)
    code, out = _capture_stdout(
        run_once(
            [FakeSource([item])],
            _factory(FakeExecutor()),
            today=TODAY,
            now=NOW,
            config=unauthored,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["decision_basis"] == "chase_config_unauthored_surface"
    assert [p["action"] for p in parsed["plans"]] == ["surface_config_missing"]


# ---------------------------------------------------------------------------
# parse_pull — the production Smokeball pull parser
# ---------------------------------------------------------------------------


def test_parse_pull_reads_nested_matter_link_object():
    # The live Smokeball /tasks payload nests the matter as a link object —
    # the flat-key miss put "unknown-matter" into every item identity and
    # forked the ledger join (WP-D probe find, ss #1915).
    raw = {
        "tasks": {
            "items": [
                {
                    "id": "t-1",
                    "matter": {"id": "m-real", "href": "https://api/matters/m-real"},
                    "subject": "Client verification outstanding",
                    "dueDate": "2026-07-20",
                }
            ]
        }
    }
    items, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert items[0].matter_id == "m-real"


def test_parse_pull_filters_to_verification_tasks():
    raw = {
        "tasks": {
            "items": [
                {"matterId": "m-1", "id": "t-1", "subject": "Verification chase: Reyes FROG", "dueDate": "2026-07-20"},
                {"matterId": "m-1", "id": "t-2", "subject": "File the motion", "dueDate": "2026-07-18"},
            ]
        }
    }
    items, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert [i.task_id for i in items] == ["t-1"]  # only the verification task
    assert items[0].next_chase_due == date(2026, 7, 20)


def test_parse_pull_excludes_probe_artifacts():
    # ss #2403: task 28745d01 was a rehearsal probe THIS skill ingested as its
    # live tracking anchor. A probe-marked task is never a verification item;
    # a real one quoting the marker mid-subject is kept (position-anchored).
    raw = {
        "tasks": {
            "items": [
                {
                    "matterId": "m-1",
                    "id": "t-p",
                    "subject": "[Operator] [SMD-PROBE 2026-08-18T14:00Z] verification probe",
                    "dueDate": "2026-07-20",
                },
                {
                    "matterId": "m-1",
                    "id": "t-r",
                    "subject": "Verification chase: quote the [SMD-PROBE] marker",
                    "dueDate": "2026-07-20",
                },
            ]
        }
    }
    items, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert [i.task_id for i in items] == ["t-r"]


def test_parse_pull_dateless_verification_seeds_first_chase_today():
    raw = {"tasks": {"items": [{"matterId": "m-1", "id": "t-1", "subject": "verification tracking"}]}}
    items, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert items[0].next_chase_due == TODAY


def test_parse_pull_error_key_is_a_problem():
    raw = {"tasks": {"items": []}, "tasksError": "boom"}
    items, problem = parse_pull(raw, today=TODAY)
    assert items == [] and problem is not None


def test_parse_pull_unrecognized_envelope_is_a_problem():
    items, problem = parse_pull({"tasks": {"weird": 1}}, today=TODAY)
    assert items == [] and problem is not None


def test_parse_pull_empty_is_clean():
    items, problem = parse_pull({"tasks": {"items": []}}, today=TODAY)
    assert items == [] and problem is None


def test_parse_pull_idless_verification_has_no_task_id():
    raw = {"tasks": {"items": [{"matterId": "m-1", "subject": "verification", "dueDate": "2026-07-20"}]}}
    items, problem = parse_pull(raw, today=TODAY)
    assert problem is None
    assert items[0].task_id is None


# ---------------------------------------------------------------------------
# load_chase_config — per-skill settings block, fail-closed on absence
# ---------------------------------------------------------------------------

_YAML = """\
personas:
  - slug: operator
    skills:
      - name: matter-inbox-router
      - name: client-verification-tracker
        settings:
          chase_cadence_days: 5
          escalate_after_attempts: 3
escalation:
  refire_days: 4
"""


def test_load_chase_config_reads_per_skill_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(_YAML, encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    config, refire = load_chase_config()
    assert config.chase_cadence_days == 5
    assert config.escalate_after_attempts == 3
    assert config.authored is True
    assert refire == 4


def test_load_chase_config_unauthored_when_settings_absent(tmp_path, monkeypatch):
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(
        "personas:\n  - slug: operator\n    skills:\n      - name: client-verification-tracker\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    config, refire = load_chase_config()
    assert config.authored is False  # fail-closed hold, not a default
    assert refire == _pre_run._DEFAULT_REFIRE_DAYS


def test_load_chase_config_unauthored_on_missing_file(monkeypatch):
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH", raising=False)
    config, _refire = load_chase_config()
    assert config.authored is False


def test_load_chase_config_unauthored_on_unparseable(tmp_path, monkeypatch):
    bad = tmp_path / "customer.yaml"
    bad.write_text("personas: [this is: not valid: yaml", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(bad))
    config, _refire = load_chase_config()
    assert config.authored is False  # never crash, never silent cadence


def test_load_chase_config_rejects_nonpositive(tmp_path, monkeypatch):
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(
        "personas:\n  - slug: operator\n    skills:\n"
        "      - name: client-verification-tracker\n"
        "        settings:\n          chase_cadence_days: 0\n"
        "          escalate_after_attempts: -1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    config, _refire = load_chase_config()
    assert config.chase_cadence_days is None
    assert config.escalate_after_attempts is None


# ---------------------------------------------------------------------------
# Pre-run handoff (ss#2547)
# ---------------------------------------------------------------------------
# The same block every bespoke pre_run carries, delegated to the sibling
# handoff_writer.py (WS-RENDER). Identity still emits no authored_date, but
# the wake plans now carry next_chase_due (the tracking task's read due date)
# and the code-projected matter_number, so the projection seeds the (number,
# date) association the rendered alert's matter numbers verify against.

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
            [FakeSource([_item(next_chase_due=TODAY)])],
            _factory(FakeExecutor()),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=_ledger,
            ledger_events=[],
        )
    )
    assert code == 0
    return out


def _handoff_path(home) -> Path:
    return Path(home) / ".smd" / "pre_run" / "client-verification-tracker.json"


def test_the_wake_writes_a_handoff_projecting_what_it_emitted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = json.loads(_wake_stdout())
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert _authored_dates_in(payload) == []  # identity still emits no authored_date
    # The projection now carries the tracking task's read due date (WS-RENDER):
    # the rendered alert names matter numbers, and numbers seed only as
    # (number, date) associations.
    assert record["dates"] == [p["next_chase_due"] for p in payload["plans"]]
    assert record["skill"] == "client-verification-tracker"
    assert record["matter_ids"] == [p["matter_id"] for p in payload["plans"]]


def test_the_handoff_carries_nothing_but_the_projection(tmp_path, monkeypatch) -> None:
    """Item keys, actions, labels and prose never reach the register."""
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


# ---------------------------------------------------------------------------
# Blind wakes: a trace and a rendered body, never neither (2026-09-02)
#
# THE INCIDENT. pilot-smokeball's Smokeball credential expired. pre_run took a
# fail-open path and the seat produced a scheduled tick with NEITHER a
# SUPPRESSED_WAKE nor an EMITTED_WAKE row -- SKILL.md step 5's dead-man's
# signal -- while the woken turn, handed no rendered dispatch, composed a
# verification alert out of nothing and sent it. Both guarantees were documented and
# neither was enforced. Every test below fails against the pre-fix code.
# ---------------------------------------------------------------------------


class _FakeLedger:
    def derive_state(self, events):
        return {}


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
    return tmp_path / ".smd" / "pre_run" / "client-verification-tracker.dispatch.json"


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


_render = _sibling("render.py", "cvt_render_under_test")
_envelope = _sibling("dispatch_envelope.py", "cvt_envelope_under_test")


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
    assert call["skill_name"] == "client-verification-tracker"
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
    assert envelope["skill"] == "client-verification-tracker"
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
    envelope_path = _authored_seat(
        tmp_path, monkeypatch, red_flag=(), fallback=("ops@smd.services",)
    )
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


def test_blind_wake_inside_run_once_still_leaves_a_row(tmp_path, monkeypatch):
    """The live path. ``main`` drives ``run_once`` under ``asyncio.run``, and
    ``ledger_unavailable_fail_open`` fires from inside that coroutine, so the
    row writer runs with a loop already on the thread. Before this test, the
    writer's own ``asyncio.run`` raised there, the failure was swallowed as
    "observability never gates the wake", and the EMITTED_WAKE row this
    module exists to write was never written. The tests above call
    ``_blind_wake`` from a bare thread and could not see it."""
    _authored_seat(tmp_path, monkeypatch)
    monkeypatch.setattr(_pre_run, "_load_ledger_module", lambda: None)
    writer = _FakeWakeWriter()
    monkeypatch.setattr(_pre_run, "_sibling_writer_factory", lambda: writer)
    executor = FakeExecutor()
    code, out = _capture_stdout(
        run_once(
            [FakeSource([_item()])],
            _factory(executor),
            today=TODAY,
            now=NOW,
            config=_CFG,
            refire_days=_REFIRE,
            ledger_module=None,  # forces the (patched) loader
            ledger_events=None,
        )
    )
    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload["decision_basis"] == "ledger_unavailable_fail_open"
    assert payload["dispatch_expected"] is True
    assert len(writer.calls) == 1
    assert writer.calls[0]["decision_basis"] == "ledger_unavailable_fail_open"
    assert writer.calls[0]["extra_metadata"]["blind_wake"] is True
    assert executor.calls == []  # the decision path's writer was never reached


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
        plans=object(),  # not iterable the way the builder expects
        items=[],
        ledger=None,
        ledger_events=[],
        today=TODAY,
        refire_days=3,
        ceiling=None,
    )
    assert out.get("dispatch_variant") == "failure_note"
    assert out["dispatch_expected"] is True
    assert json.loads(envelope_path.read_text(encoding="utf-8"))["failure_note_reason"] == (
        "envelope_build_failed"
    )


def test_a_clean_run_with_nothing_to_say_sends_no_failure_note(tmp_path, monkeypatch):
    """The guard against the opposite defect: an empty digest that rendered
    fine is a SUCCESS. Paging 'the run failed' here would be a false alarm on
    every quiet day, which is how alerts get ignored."""
    envelope_path = _authored_seat(tmp_path, monkeypatch)
    out = _envelope.build_and_write(
        plans=[],
        items=[],
        ledger=_FakeLedger(),
        ledger_events=[],
        today=TODAY,
        refire_days=3,
        ceiling=None,
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

_OVERLAY_DIR = Path(
    os.environ.get("SS_OVERLAY_DIR") or (Path.home() / "dev" / "hermes-smd-overlay")
)
_OVERLAY_AVAILABLE = (_OVERLAY_DIR / ".git").exists()


def _pinned_overlay_ref() -> str:
    dockerfile = (_PRE_RUN_PATH.parents[2] / "templates" / "Dockerfile").read_text(
        encoding="utf-8"
    )
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
    source = subprocess.run(
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
    func = re.search(
        r"^def _valid_dispatch\(entry: object\) -> bool:\n(?:[ \t].*\n|\n)+", source, re.M
    )
    assert func, "could not lift _valid_dispatch out of the pinned dispatcher"
    required = set(re.findall(r'entry\.get\("(\w+)"', func.group(0)))
    # A regex that silently matched nothing would make every assertion below
    # vacuous -- the exact hole this repo keeps finding in its own gates.
    assert {"recipients", "subject", "full_body"} <= required, required

    for entry in envelope["dispatches"]:
        missing = [k for k in required if k not in entry]
        assert missing == [], (
            "the pinned dispatcher reads %s and the failure-note envelope omits %s; "
            "it would be refused whole and the turn would compose the gap"
            % (sorted(required), missing)
        )
        assert isinstance(entry["recipients"], list) and entry["recipients"]
        assert isinstance(entry["subject"], str) and entry["subject"].strip()
        assert isinstance(entry["full_body"], str) and entry["full_body"].strip()
