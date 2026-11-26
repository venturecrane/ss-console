"""A firm person still waiting two hours after the reply: the Captain hears of
it once (``bin/lib/work_health_waiting.py``, 2026-11-25). Driven through the
real ``work-health.py`` main with offline rows; only the Resend call is
replaced. Every value is invented."""

from __future__ import annotations

import json
from datetime import timedelta

from bin.lib import work_health_owed as owed_wh
from bin.lib import work_health_sources as src
from bin.lib import work_health_waiting as waiting
from bin.tests.test_work_health import CFG, NOW, SEAT, VID, _inbound, _offline, _row, cli

CAPTAIN = "scott@smd.services"


def _waiting_rows(minutes_after_reply: int) -> list[dict]:
    """A request, then a "Let me look into that" reply ``minutes_after_reply`` ago."""
    said = NOW - timedelta(minutes=minutes_after_reply)
    return [
        _inbound(said - timedelta(minutes=1)),
        _row("REPLY_SENT", said, in_reply_to=VID, could_not="looking_into", recipient="pat@firm.example"),
    ]


def _capture(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def send(subject, text, key, to=None):
        sent.append({"subject": subject, "text": text, "key": key, "to": to})
        return None

    monkeypatch.setattr(cli, "send", send)
    return sent


def _run(rows_path, state_in=None, state_out=None, now=NOW) -> int:
    argv = ["--rows", f"{SEAT}={rows_path}", "--now", now.isoformat(), "--pages-only"]
    if state_in:
        argv += ["--state-in", str(state_in)]
    if state_out:
        argv += ["--state-out", str(state_out)]
    return cli.main(argv)


def test_a_request_still_waiting_at_two_hours_emails_the_captain_once(monkeypatch, tmp_path):
    """FALSIFIER: drop the reminder from main and nothing reaches the Captain;
    drop the ``reminded`` state and the second run emails him again."""
    rows = _offline(monkeypatch, tmp_path, _waiting_rows(125))
    sent = _capture(monkeypatch)
    first, second = tmp_path / "s1.json", tmp_path / "s2.json"
    assert _run(rows, state_out=first) == 0
    to_captain = [s for s in sent if s["to"] == CAPTAIN]
    assert len(to_captain) == 1
    assert to_captain[0]["subject"].startswith(f"Still waiting 2h: {SEAT}: ")
    assert "pat@firm.example" not in to_captain[0]["subject"] + to_captain[0]["text"]
    assert "—" not in to_captain[0]["text"]
    assert json.loads(first.read_text())[waiting.REMINDED]
    later = NOW + timedelta(hours=1)
    assert _run(rows, state_in=first, state_out=second, now=later) == 0
    assert len([s for s in sent if s["to"] == CAPTAIN]) == 1
    assert json.loads(second.read_text())[waiting.REMINDED] == json.loads(first.read_text())[waiting.REMINDED]


def test_before_two_hours_nothing_goes_to_the_captain(monkeypatch, tmp_path):
    rows = _offline(monkeypatch, tmp_path, _waiting_rows(100))
    sent = _capture(monkeypatch)
    assert _run(rows) == 0
    assert [s for s in sent if s["to"] == CAPTAIN] == []


def test_a_failed_send_is_not_recorded_and_is_tried_next_run(monkeypatch, tmp_path):
    rows = _offline(monkeypatch, tmp_path, _waiting_rows(130))
    monkeypatch.setattr(cli, "send", lambda subject, text, key, to=None: "resend HTTP 500" if to else None)
    out = tmp_path / "s.json"
    assert _run(rows, state_out=out) == cli.EXIT_SEND_FAILED
    assert json.loads(out.read_text())[waiting.REMINDED] == {}


def test_a_dry_run_sends_nothing(monkeypatch, tmp_path):
    rows = _offline(monkeypatch, tmp_path, _waiting_rows(130))
    sent = _capture(monkeypatch)
    argv = ["--rows", f"{SEAT}={rows}", "--now", NOW.isoformat(), "--dry-run"]
    assert cli.main(argv) == 0
    assert [s for s in sent if s["to"] == CAPTAIN] == []


def test_the_captain_address_honours_its_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPTAIN_ALERT_EMAIL", "ops-lead@smd.services")
    rows = _offline(monkeypatch, tmp_path, _waiting_rows(130))
    sent = _capture(monkeypatch)
    assert _run(rows) == 0
    assert [s["to"] for s in sent if s["key"].startswith("work-health-waiting:")] == ["ops-lead@smd.services"]


def test_a_test_seat_and_an_old_backlog_are_not_reminded():
    items = src.could_not_items(SEAT, _waiting_rows(130), CFG, NOW)
    assert waiting.due(items, {}, NOW, frozenset({SEAT})) == []
    assert waiting.due(items, {}, NOW + timedelta(hours=80), frozenset()) == []
    assert len(waiting.due(items, {}, NOW, frozenset())) == 1


def test_a_request_owed_by_the_broker_is_listed_once_not_twice():
    """The reply plugin now records the could-not reply as an owed item; the
    could-not line for the same request steps aside. FALSIFIER: drop
    ``owed_refs`` and the request is listed (and reminded) twice."""
    owed = {"owed": [{"id": "01OWED", "kind": "reply_not_fulfilled", "request_ref": VID}], "owed_jobs": []}
    assert src.could_not_items(SEAT, _waiting_rows(130), CFG, NOW, owed=owed_wh.owed_refs(owed)) == []
    assert len(src.could_not_items(SEAT, _waiting_rows(130), CFG, NOW)) == 1
    (line,) = owed_wh.smd_owed_items(
        SEAT,
        [],
        CFG,
        NOW,
        open_owed={
            "owed": [{**owed["owed"][0], "created_at": (NOW - timedelta(hours=3)).isoformat(), "detail": "x"}],
            "owed_jobs": [],
        },
    )
    assert "look into it" in line.what and "01OWED" in line.todo
    assert len(waiting.due([line], {}, NOW, frozenset())) == 1
