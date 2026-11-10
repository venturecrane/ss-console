"""The hourly triage sees a refused or missed rep-desk digest (ss-console #3946).

The rep desk's hourly digest is code-rendered and sent out of turn
(``operator/skills/rep-desk/pre_run.py``, the overlay's ``shared/person_digest.py``).
A digest nobody received is invisible to the person it was for, so the
work-health triage must make it an item. These fixtures are the rows the seat
writes, graded by the triage's REAL graders (``work_health_sources``, which
imports reconcile-outcomes and the slot grader rather than restating them):

* a digest whose send the gate refused: an EMITTED_WAKE for ``rep-desk``, then
  the refusal, and no delivery. A ``silent_run`` item.
* an hourly slot with no wake row at all (the scheduler did not run it). A
  ``missed_slot`` item naming rep-desk.

And the falsifiers beside each: a delivered digest, a recorded "nothing due"
(SUPPRESSED_WAKE) and every hour accounted for are no items.

* a run whose pre_run crashed or could not write its envelope: it WAKES with
  nothing to send, so its EMITTED_WAKE is an obligation nothing closes. A
  ``silent_run`` item.

FAIL-FIRST: before ``rep-desk`` had a ``skill_bindings`` entry in
``operator/contracts/output-classes.yaml`` the crashed run's wake was
UNCLASSIFIED by the reconciler (it cannot say which terminal states an unbound
routine accepts), and ``silent_run_items`` returned nothing for it; the run
with the skill name changed to an unbound one still shows that (last test).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bin.lib import work_health as wh
from bin.lib import work_health_sources as src

UTC = timezone.utc
NOW = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)
CFG = wh.load_config()
SEAT = "smd-guide"
SKILL = "rep-desk"
SCHEDULE = "7 * * * *"
GUIDE_GIT = {
    "business_hours": {"timezone": "America/Phoenix"},
    "personas": [
        {
            "slug": "guide",
            "cron": [
                {"skill": SKILL, "schedule": SCHEDULE, "pre_run": "pre_run.py", "wake_policy": "pre_run_decides"},
            ],
        }
    ],
}
DEPLOYED = {(SKILL, SCHEDULE)}


def _row(action: str, at: datetime, skill: str | None = SKILL, **md) -> dict:
    return {
        "id": f"{action}-{at.isoformat()}-{len(md)}",
        "ts": at.isoformat().replace("+00:00", "Z"),
        "action_type": action,
        "skill_name": skill,
        "metadata": json.dumps(md),
    }


def _hourly(rows_for_hour) -> list[dict]:
    """Every hourly slot of the last day, each with the rows ``rows_for_hour`` gives."""
    out: list[dict] = []
    start = (NOW - timedelta(hours=25)).replace(minute=7, second=0, microsecond=0)
    t = start
    while t <= NOW:
        out += rows_for_hour(t)
        t += timedelta(hours=1)
    return out


def _quiet(t: datetime) -> list[dict]:
    return [_row("SUPPRESSED_WAKE", t + timedelta(seconds=20), decision_basis="nothing_due")]


def test_a_refused_digest_is_a_silent_run_item() -> None:
    sent_at = NOW - timedelta(hours=3)
    rows = [
        _row("EMITTED_WAKE", sent_at, decision_basis="digest_ready", chunk=2, render_mode="templated"),
        _row(
            "FABRICATION_FILTER_TRIGGERED",
            sent_at + timedelta(seconds=40),
            tool="smd_send_message",
            marker_hits="['specific-dollar-amount']",
        ),
        _row("LLM_TURN_COMPLETED", sent_at + timedelta(seconds=55)),
    ]
    items, _ = src.silent_run_items(SEAT, rows, CFG, NOW)
    assert [i.kind for i in items] == ["silent_run"], items
    assert SKILL in items[0].what or "blocked" in items[0].what


def test_a_delivered_digest_is_no_item() -> None:
    sent_at = NOW - timedelta(hours=3)
    rows = [
        _row("EMITTED_WAKE", sent_at, decision_basis="digest_ready", chunk=2),
        _row("CONFIRM_SEND_DISPATCHED", sent_at + timedelta(seconds=30), outcome="sent"),
        _row("CONFIRM_SEND_DISPATCHED", sent_at + timedelta(seconds=31), outcome="sent"),
        _row("LLM_TURN_COMPLETED", sent_at + timedelta(seconds=55)),
    ]
    items, _ = src.silent_run_items(SEAT, rows, CFG, NOW)
    assert items == []


def test_nothing_due_is_a_recorded_no_op_not_an_item() -> None:
    items, _ = src.silent_run_items(SEAT, _hourly(_quiet), CFG, NOW)
    assert items == []


def test_a_digest_slot_that_never_ran_is_a_missed_slot_item() -> None:
    gap = (NOW - timedelta(hours=5)).replace(minute=7, second=0, microsecond=0)
    rows = _hourly(lambda t: [] if t == gap else _quiet(t))
    items = src.missed_slot_items(SEAT, rows, CFG, NOW, None, GUIDE_GIT, DEPLOYED, adoption_known=False)
    assert [i.kind for i in items] == ["missed_slot"], items
    assert SKILL in items[0].what


def test_every_hour_accounted_for_is_no_missed_slot() -> None:
    items = src.missed_slot_items(SEAT, _hourly(_quiet), CFG, NOW, None, GUIDE_GIT, DEPLOYED, adoption_known=False)
    assert items == []


def _crashed(skill: str) -> list[dict]:
    at = NOW - timedelta(hours=3)
    return [
        _row("EMITTED_WAKE", at, skill=skill, decision_basis="pre_run_crashed"),
        _row("LLM_TURN_COMPLETED", at + timedelta(seconds=40), skill=skill),
    ]


def test_a_crashed_digest_run_is_a_silent_run_item() -> None:
    items, _ = src.silent_run_items(SEAT, _crashed(SKILL), CFG, NOW)
    assert [i.kind for i in items] == ["silent_run"], items
    assert "rep-desk run ended with nothing delivered" in items[0].what


def test_without_its_binding_the_crash_would_be_invisible() -> None:
    """The fail-first shape, kept: a routine with no skill_bindings entry is
    unclassified, so the same rows produce no item."""
    items, _ = src.silent_run_items(SEAT, _crashed("rep-desk-unbound"), CFG, NOW)
    assert items == []
