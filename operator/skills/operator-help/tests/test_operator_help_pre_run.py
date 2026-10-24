"""operator-help pre_run: the daily review wake and the Monday note (ss-console#3423, increment 3).

What these pin, each with the harm it prevents:

* every day wakes the review, and only Monday (or a forced proof) writes a note,
  so SMD is not mailed daily and the review never silently stops;
* the note says what changed with its counts, the proposals, and how to undo,
  and carries no em dash, no address and no date;
* a help state that cannot be read is said in the note, never rendered as
  "nothing changed";
* an unauthored recipient costs the note, never the review.

Run::

    cd operator && python3 -m pytest skills/operator-help/tests -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 10, 26, 21, 13, tzinfo=timezone.utc)  # Monday 14:13 in Phoenix
TUESDAY = datetime(2026, 10, 27, 21, 13, tzinfo=timezone.utc)
_CFG = {
    "business_hours": {"timezone": "America/Phoenix"},
    "personas": [
        {
            "slug": "operator",
            "skills": [{"name": "operator-help", "enabled": True, "settings": {"recipient": "scott@smd.services"}}],
        }
    ],
}


@pytest.fixture
def pr(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("operator_help_pre_run", _SKILL_DIR / "pre_run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SMD_HELP_STATE_DB_PATH", str(tmp_path / "smd" / "help_state.db"))
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    return module


def _run(pr, cfg=_CFG, *, at=MONDAY, force=False, dry=False) -> dict:
    out = io.StringIO()
    with redirect_stdout(out):
        assert pr.run(cfg, clock=lambda: at, force=force, dry=dry) == 0
    return json.loads(out.getvalue().strip().splitlines()[-1])


def _envelope(tmp_path) -> dict | None:
    path = tmp_path / "home" / ".smd" / "pre_run" / "operator-help.dispatch.json"
    return json.loads(path.read_text()) if path.exists() else None


def _seed(tmp_path, *, changes=(), review=None, now=MONDAY):
    path = tmp_path / "smd" / "help_state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE help_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE help_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, action TEXT NOT NULL,"
        " tip_id TEXT NOT NULL, prior_json TEXT NOT NULL, after_json TEXT NOT NULL, reason_json TEXT NOT NULL,"
        " undone_at REAL)"
    )
    for action, tip, reason, undone in changes:
        conn.execute(
            "INSERT INTO help_snapshots (ts, action, tip_id, prior_json, after_json, reason_json, undone_at)"
            " VALUES (?, ?, ?, '[]', '[]', ?, ?)",
            (now.timestamp() - 3600, action, tip, json.dumps(reason), undone),
        )
    if review is not None:
        conn.execute(
            "INSERT INTO help_meta (key, value) VALUES ('last_review', ?)",
            (json.dumps({**review, "at": now.timestamp() - 7200}),),
        )
    conn.commit()
    conn.close()


_REVIEW = {
    "requests": {"total": 12, "senders": 4, "repeat_ask_threads": 1},
    "jobs": {"held": {"medchron": 2}, "failed": {}, "hold_classes": {"another_person": 2}},
    "noted": {"vague_request": 3},
    "kinds": {
        "vague_request": {"occurrences": 3, "senders": 2, "tips": ["vague_request"], "actionable": True},
        "nothing_in_scope": {"occurrences": 3, "senders": 2, "tips": [], "actionable": True},
    },
    "actionable": ["nothing_in_scope", "vague_request"],
    "proposals": ["nothing_in_scope"],
}


def test_a_weekday_wakes_the_review_and_writes_no_note(pr, tmp_path):
    out = _run(pr, at=TUESDAY)
    assert out == {"wakeAgent": True, "mode": "review", "status": "review_due", "note": False}
    assert _envelope(tmp_path) is None


def test_monday_writes_the_note_to_the_authored_recipient_and_wakes_the_review(pr, tmp_path):
    _seed(
        tmp_path,
        changes=[("promote", "vague_request", {"kind": "vague_request", "occurrences": 3, "senders": 2}, None)],
        review=_REVIEW,
    )
    out = _run(pr)
    assert out["status"] == "note_ready" and out["wakeAgent"] is True and out["mode"] == "review"
    assert out["changes"] == 1 and out["review_seen"] is True
    env = _envelope(tmp_path)
    assert env is not None and env["skill"] == "operator-help" and env["in_turn_enforce"] is True
    [dispatch] = env["dispatches"]
    assert dispatch["recipients"] == ["scott@smd.services"]
    body = dispatch["full_body"]
    assert (
        "Put the tip vague_request first in the help answer, because vague_request came up 3 times from 2 people."
        in body
    )
    assert "- nothing_in_scope came up 3 times from 2 people and no tip covers it." in body
    assert "- Jobs held: medchron 2." in body
    assert body.rstrip().endswith("reply to this email with the word undo.")
    for text in (body, dispatch["subject"], dispatch["skeleton_body"]):
        assert "\u2014" not in text and "\u2013" not in text
        assert "2026" not in text  # no dates: nothing for the identifier gate to question
        assert "@" not in text
    assert dispatch["body_sha256_full"] == pr._H.canonical_body_sha256(body)


def test_a_quiet_week_says_nothing_changed_and_no_review_ran(pr, tmp_path):
    out = _run(pr)
    assert out["status"] == "note_ready"
    body = _envelope(tmp_path)["dispatches"][0]["full_body"]
    assert "- Nothing changed this week." in body
    assert "- No review ran in the last seven days." in body
    assert "- None this week." in body


def test_an_undone_change_is_marked(pr, tmp_path):
    _seed(tmp_path, changes=[("demote", "wants_changes", {}, MONDAY.timestamp())])
    _run(pr)
    body = _envelope(tmp_path)["dispatches"][0]["full_body"]
    assert "- Stopped leading the help answer with the tip wants_changes (since undone)." in body


def test_an_unreadable_state_is_said_never_rendered_as_quiet(pr, tmp_path):
    """FALSIFIER: map the read fault to an empty week and SMD reads 'nothing changed'."""
    path = tmp_path / "smd" / "help_state.db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage" * 300)
    out = _run(pr)
    assert out["changes"] is None
    body = _envelope(tmp_path)["dispatches"][0]["full_body"]
    assert "I could not read my help records this week" in body
    assert "Nothing changed" not in body


def test_no_recipient_costs_the_note_never_the_review(pr, tmp_path):
    cfg = {"personas": [{"slug": "operator", "skills": [{"name": "operator-help", "enabled": True}]}]}
    out = _run(pr, cfg)
    assert out["wakeAgent"] is True and out["status"] == "note_recipient_unauthored"
    assert _envelope(tmp_path) is None


def test_force_sends_the_note_on_any_day_and_dry_writes_nothing(pr, tmp_path):
    assert _run(pr, at=TUESDAY, force=True)["status"] == "note_ready"
    (tmp_path / "home" / ".smd" / "pre_run" / "operator-help.dispatch.json").unlink()
    out = _run(pr, dry=True)
    assert out["status"] == "dry_run" and out["wakeAgent"] is False
    assert _envelope(tmp_path) is None


def test_monday_is_judged_in_the_seat_zone(pr):
    # 05:00 UTC Monday is still Sunday evening in Phoenix: no note.
    early = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)
    assert _run(pr, at=early)["note"] is False
