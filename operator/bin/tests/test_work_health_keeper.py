"""The mailbox keeper and the one-machine rule in the hourly triage
(bin/lib/work_health_keeper.py, work-health.py --machines, seat-readiness.py).

Each item has its falsifier beside it: the fixture where it went wrong (the
item appears) and the nearest one where it did not (it does not).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health_keeper as kw

UTC = timezone.utc
# 2026-11-16 16:30 UTC is 09:30 in Phoenix: the 06:00 list is overdue from 08:00.
NOW = datetime(2026, 11, 16, 16, 30, tzinfo=UTC)
SEAT = "example-office"
CONFIG = {
    "personas": [
        {
            "skills": [
                {
                    "name": "mailbox-keeper",
                    "enabled": True,
                    "settings": {"send_after_hour": 6, "timezone": "America/Phoenix"},
                }
            ]
        }
    ]
}


def _row(at: datetime, **md) -> dict:
    return {
        "id": f"r{at.timestamp()}",
        "ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action_type": "SUPPRESSED_WAKE",
        "skill_name": "mailbox-keeper",
        "metadata": json.dumps(md),
    }


def _ok(at: datetime, mode: str = "hourly", **md) -> dict:
    return _row(at, decision_basis="mailbox_keeper_ran", status="done", mode=mode, **md)


def _kinds(items) -> list[str]:
    return [i.kind for i in items]


def test_a_healthy_day_is_quiet() -> None:
    rows = [_ok(NOW - timedelta(hours=3, minutes=10), mode="daily", classify="ok"), _ok(NOW - timedelta(minutes=40))]
    assert kw.keeper_items(SEAT, rows, NOW, CONFIG) == []


def test_a_failed_newest_run_is_an_item() -> None:
    rows = [
        _ok(NOW - timedelta(hours=3, minutes=10), mode="daily"),
        _row(
            NOW - timedelta(minutes=40),
            decision_basis="mailbox_keeper_failed",
            status="send_check_failed",
            mode="daily",
        ),
    ]
    (found,) = kw.keeper_items(SEAT, rows, NOW, CONFIG)
    assert found.kind == "keeper_failed" and "AgentMail" in found.what


def test_a_stopped_reading_is_an_item() -> None:
    for word, expect in (("fence", ["keeper_classify"]), ("failed", ["keeper_classify"]), ("off", [])):
        rows = [_ok(NOW - timedelta(hours=3, minutes=10), mode="daily", classify=word)]
        assert _kinds(kw.keeper_items(SEAT, rows, NOW, CONFIG)) == expect, word


def test_no_list_by_two_hours_after_the_hour_is_an_item() -> None:
    hourly_only = [_ok(NOW - timedelta(minutes=40))]
    assert _kinds(kw.keeper_items(SEAT, hourly_only, NOW, CONFIG)) == ["keeper_missed"]
    # Before 08:00 Phoenix it is not due yet.
    early = NOW - timedelta(hours=2)
    assert kw.keeper_items(SEAT, [_ok(early - timedelta(minutes=40))], early, CONFIG) == []
    # Sent by another machine counts as sent.
    elsewhere = [
        _row(NOW - timedelta(hours=3), decision_basis="mailbox_keeper_ran", status="already_sent_today", mode="daily")
    ]
    assert kw.keeper_items(SEAT, elsewhere, NOW, CONFIG) == []


def test_a_seat_without_the_keeper_gets_nothing() -> None:
    assert kw.keeper_items(SEAT, [], NOW, {"personas": [{"skills": [{"name": "workspace"}]}]}) == []


def _machine(state: str, created: datetime) -> dict:
    return {"id": f"m{created.minute}", "state": state, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_two_started_machines_are_an_item_after_the_grace() -> None:
    old = NOW - timedelta(days=3)
    assert kw.machine_items(SEAT, [_machine("started", old)], NOW) == []
    assert kw.machine_items(SEAT, [_machine("started", old), _machine("stopped", old)], NOW) == []
    (found,) = kw.machine_items(SEAT, [_machine("started", old), _machine("started", NOW - timedelta(hours=2))], NOW)
    assert found.kind == "machines" and found.what.startswith("2 machines are started")
    # A deploy replacing the machine: two for a moment is not an item.
    assert (
        kw.machine_items(SEAT, [_machine("started", old), _machine("started", NOW - timedelta(minutes=3))], NOW) == []
    )


def _cli():
    path = Path(__file__).resolve().parents[1] / "work-health.py"
    spec = importlib.util.spec_from_file_location("work_health_cli_keeper", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["work_health_cli_keeper"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_machine_check_runs_only_when_asked_and_an_unreadable_count_is_a_gap(monkeypatch) -> None:
    cli = _cli()
    calls: list[str] = []

    def unreadable(seat, repo):
        calls.append(seat)
        return None, "no fly CLI on this host"

    monkeypatch.setattr(cli.keeper_wh, "read_machines", unreadable)
    items: list = []
    gaps: dict = {}
    cli._machine_check(SEAT, NOW, items, gaps)
    assert items == [] and gaps == {SEAT: ["machine count not checked: no fly CLI on this host."]}
    old = NOW - timedelta(days=1)
    monkeypatch.setattr(
        cli.keeper_wh, "read_machines", lambda s, r: ([_machine("started", old), _machine("started", old)], "")
    )
    cli._machine_check(SEAT, NOW, items, gaps)
    assert _kinds(items) == ["machines"]
    assert "--machines" in cli._parser().format_help()


def test_seat_readiness_fails_on_two_started_machines(monkeypatch) -> None:
    path = Path(__file__).resolve().parents[1] / "seat-readiness.py"
    spec = importlib.util.spec_from_file_location("seat_readiness_keeper", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seat_readiness_keeper"] = mod
    spec.loader.exec_module(mod)
    for states, status in (
        (["started"], mod.PASS),
        (["started", "started"], mod.FAIL),
        (["started", "stopped"], mod.PASS),
    ):
        listing = json.dumps([{"state": s} for s in states])
        monkeypatch.setattr(mod, "_run", lambda cmd, timeout=60, out=listing: (0, out))
        rep = mod.Report(SEAT)
        mod.check_machine(rep, "hermes-example-office", False)
        row = next(r for r in rep.rows if r.check == "one machine started")
        assert row.status == status, states
