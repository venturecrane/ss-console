"""`medchron.note_by_smd` and the note lane: SMD's approved words reach a firm
colleague in her own thread, through the smd-note skill's DELIVER wake.

WHY (2026-10-15). The Operator filed a document on SMD's request and could not
tell the colleague who asked. Each test below names the failure it stands
against. The command records the note and polls; only the note lane sends
the wake, and the command holds no wake secret.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from medchron import note_by_smd as nbs
from medchron import note_lane
from medchron.proof import watch as proof_watch

NOTE = "01M4NXTEAAAAAAAAAAAAAAAAAA"
GID = "AAMkNOTEANCHORMESSAGE0001="
REQUESTER = "colleague@firm.example"
SECRET = "s3cr3t-handoff-bearer-value"
DIGEST = "d" * 64
#: The overlay's wake pin (hermes-smd-overlay shared/turn_sources.py WAKE_JOB,
#: with ``note`` added in overlay#458): the turn binds to the job this names.
WAKE_JOB = re.compile(r"\b(demand|drafting|chronology|litigation|negotiation|note) job ([0-9A-HJKMNP-TV-Z]{26})\b")


class FakeClient:
    def __init__(self, *, accept: bool = True, replies: list[list[dict]] | None = None) -> None:
        self.accept = accept
        self.calls: list[dict] = []
        self.replies = replies or [[]]

    def _request(self, req: dict) -> dict:
        self.calls.append(req)
        action = req["action"]
        if action == "note_preview":
            if not self.accept:
                return {
                    "ok": True,
                    "accepted": False,
                    "reason": "the note names an identifier that email does not carry",
                }
            return {
                "ok": True,
                "accepted": True,
                "sender": REQUESTER,
                "cc": ["office@firm.example"],
                "subject": "Matter 900417",
                "received_at": "2026-10-14T17:20:00Z",
                "text": req["text"],
                "digest": DIGEST,
            }
        if action == "note_record":
            if not self.accept:
                return {"ok": True, "accepted": False, "reason": "confirm does not match"}
            return {"ok": True, "accepted": True, "job_id": NOTE, "requester": REQUESTER, "digest": DIGEST}
        if action == "delivery_reply_status":
            rows = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            return {"ok": True, "replies": rows, "expected_key": f"note_job:{NOTE}:1:delivered"}
        if action == "delivery_proof_alert":
            return {"ok": True, "audit_id": "x"}
        raise AssertionError(f"unexpected broker call {action}")

    def actions(self) -> list[str]:
        return [c["action"] for c in self.calls]


def _lane(tmp_path: Path, client: FakeClient) -> note_lane.NoteLane:
    now = {"t": 1_000_000.0}
    lane = note_lane.NoteLane(
        run_dir=tmp_path / "run",
        broker=note_lane.NoteBroker(client),  # type: ignore[arg-type]
        runner_cmd=[],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cgroup",
        clock=lambda: now["t"],
        gate_url="http://127.0.0.1:8643",
        wake_secret=SECRET,
    )
    lane._now = now  # type: ignore[attr-defined]
    return lane


def _args(**over) -> argparse.Namespace:
    base = nbs.parser().parse_args([GID, "--text", "Hi,\n\nMatter 900417 is filed.", "--approved-by", "smd"])
    for k, v in over.items():
        setattr(base, k, v)
    return base


def _sent(key_outcome: str = "sent", located: bool | None = True) -> list[dict]:
    return [{"key": f"note_job:{NOTE}:1:delivered", "outcome": key_outcome, "located": located}]


class NoNetwork:
    """Any attempt to open a connection is recorded and refused: the command
    must never POST the wake (the lane alone does)."""

    def __init__(self, monkeypatch) -> None:
        self.attempts: list = []
        import http.client
        import socket

        def refuse(*a, **k):
            self.attempts.append((a, k))
            raise OSError("network refused in this test")

        monkeypatch.setattr(http.client, "HTTPConnection", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)


def _run(tmp_path: Path, client: FakeClient, lane=None, *, on_sleep=None, **over) -> int:
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        if on_sleep:
            on_sleep()

    return nbs.run(
        _args(**over),
        client=client,
        lane=lane or _lane(tmp_path, client),
        sleep=sleep,
        clock=lambda: clock["t"],
        audit_db=str(tmp_path / "none.db"),
    )


class FakeGate:
    """The gateway's handoff route, for the note lane's own dispatch."""

    def __init__(self, monkeypatch, status: int = 202) -> None:
        self.posts: list[dict] = []
        gate = self

        class Conn:
            def __init__(self, host, port, timeout=None):
                pass

            def connect(self):
                pass

            def request(self, method, path, body=None, headers=None):
                gate.posts.append({"path": path, "body": json.loads(body), "auth": headers["Authorization"]})

            def getresponse(self):
                class R:
                    status = gate.status

                    def read(self):
                        return b""

                return R()

            def close(self):
                pass

        self.status = status
        import http.client

        monkeypatch.setattr(http.client, "HTTPConnection", Conn)


# -- the dry run ------------------------------------------------------------------


def test_a_dry_run_shows_the_reply_and_records_nothing(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    lane = _lane(tmp_path, client)
    assert _run(tmp_path, client, lane, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "To: colleague@firm.example" in out and "Cc: office@firm.example" in out
    assert f"digest: {DIGEST}" in out and "Matter 900417 is filed." in out
    assert client.actions() == ["note_preview"]
    assert not lane.jobs.exists()


def test_a_refused_dry_run_is_a_refusal(tmp_path: Path) -> None:
    with pytest.raises(nbs.Refused):
        _run(tmp_path, FakeClient(accept=False), dry_run=True)


def test_main_maps_a_refusal_to_exit_2(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeClient(accept=False)
    monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", str(tmp_path / "sock"))
    monkeypatch.setenv("SMD_MEDCHRON_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(nbs, "BrokerClient", lambda _p: client)
    assert nbs.main([GID, "--text", "x", "--approved-by", "smd", "--confirm", DIGEST]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not (tmp_path / "run" / "note-jobs").exists()


def test_a_live_run_needs_the_dry_runs_digest(tmp_path: Path) -> None:
    client = FakeClient()
    with pytest.raises(nbs.Refused):
        _run(tmp_path, client, confirm=None)
    assert client.calls == []


# -- the live run: the command records and polls; it never sends the wake -----------


def test_the_live_run_records_and_queues_the_wake_and_never_posts(tmp_path: Path, monkeypatch, capsys) -> None:
    """FALSIFIER: have the command POST the wake itself and the note lane, which
    scans the same dir every tick, sends a second one (two DELIVER turns)."""
    net = NoNetwork(monkeypatch)
    client = FakeClient(replies=[[], _sent()])
    lane = _lane(tmp_path, client)
    assert _run(tmp_path, client, lane, confirm=DIGEST) == 0
    assert net.attempts == []
    assert client.calls[0] == {
        "action": "note_record",
        "graph_message_id": GID,
        "text": "Hi,\n\nMatter 900417 is filed.",
        "approved_by": "smd",
        "confirm": DIGEST,
        "recorded_by": "root",
    }
    st = lane._daemon_state(NOTE)
    task = st["wake"]["task"]
    assert st["state"] == "delivered" and st["wake"]["pending"] is True and st["finished_at"] == 0.0
    assert task == f"Run the smd-note skill's DELIVER mode for note job {NOTE}.\nKind: note."
    assert WAKE_JOB.search(task).groups() == ("note", NOTE)
    assert lane._wakes_pending() == [NOTE] and "reply_watch" not in st
    assert json.loads((lane.job_dir(NOTE) / "envelope.json").read_text())["requested_by"] == REQUESTER
    out = capsys.readouterr().out
    assert "the note lane sends it on its next tick and arms the reply watch then" in out
    assert "reply: sent+located" in out


def test_the_lane_sends_the_wake_once_and_arms_the_watch(tmp_path: Path, monkeypatch) -> None:
    gate = FakeGate(monkeypatch)
    client = FakeClient()
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE), created=lane.clock())
    lane.tick()
    lane.tick()
    assert [(p["path"], p["body"]["handoff_id"]) for p in gate.posts] == [("/webhooks/handoff", f"note-{NOTE}")]
    assert gate.posts[0]["body"]["task"] == note_lane.wake_task(NOTE)
    assert gate.posts[0]["auth"] == f"Bearer {SECRET}"
    st = lane._daemon_state(NOTE)
    assert st["wake"]["pending"] is False and st["reply_watch"]["kind"] == "note_job"


@pytest.mark.parametrize(
    ("rows", "printed", "rc"),
    [
        (_sent("sent", True), "reply: sent+located", 0),
        # proof/watch.py closes a watch proven on a sent row whose located is not
        # False, None included; mirrored, but never called "located".
        (_sent("sent", None), "reply: sent (no Sent Items lookup reported)", 0),
        (_sent("sent", False), "reply: sent-not-located", 1),
        (_sent("unknown", None), "reply: unknown", 1),
    ],
)
def test_each_reply_outcome_is_named(tmp_path: Path, capsys, rows, printed, rc) -> None:
    assert _run(tmp_path, FakeClient(replies=[rows]), confirm=DIGEST, wait_min=1.0) == rc
    assert printed in capsys.readouterr().out


def test_located_none_is_judged_as_the_watch_judges_it() -> None:
    """The command and the reply watch must never disagree on what is proven."""
    for located in (True, None, False):
        rows = _sent("sent", located)
        watch_proven = any(r.get("outcome") == "sent" and r.get("located") is not False for r in rows)
        assert (nbs.reply_outcome(rows) in nbs.PROVEN) is watch_proven


def test_a_wake_still_pending_at_the_end_is_named(tmp_path: Path, capsys) -> None:
    assert _run(tmp_path, FakeClient(replies=[[]]), confirm=DIGEST, wait_min=1.0) == 1
    assert "reply: wake still pending" in capsys.readouterr().out


def test_a_wake_that_went_with_no_reply_is_not_sent(tmp_path: Path, capsys) -> None:
    client = FakeClient(replies=[[]])
    lane = _lane(tmp_path, client)

    def lane_sent_it():
        if lane.job_dir(NOTE).is_dir():
            lane._write_state(NOTE, wake={"pending": False, "outcome": "delivered", "task": "t"})

    assert _run(tmp_path, client, lane, on_sleep=lane_sent_it, confirm=DIGEST, wait_min=1.0) == 1
    assert "reply: not sent: the wake went" in capsys.readouterr().out


def test_the_window_starts_at_the_jobs_creation(tmp_path: Path) -> None:
    client = FakeClient(replies=[[]])
    clock = {"t": 0.0}
    calls: list[float] = []

    def sleep(s):
        calls.append(s)
        clock["t"] += s

    assert nbs.poll(client, NOTE, created=0.0, wait_min=1.0, sleep=sleep, clock=lambda: clock["t"]) == "not sent"
    assert sum(calls) == 60.0
    calls.clear()
    clock["t"] = 600.0
    assert nbs.poll(client, NOTE, created=0.0, wait_min=1.0, sleep=sleep, clock=lambda: clock["t"]) == "not sent"
    assert calls == []


def test_a_reply_under_another_key_is_not_this_notes(tmp_path: Path, capsys) -> None:
    other = [{"key": f"note_job:{NOTE}:2:delivered", "outcome": "sent", "located": True}]
    assert _run(tmp_path, FakeClient(replies=[other]), confirm=DIGEST, wait_min=0.5) == 1
    assert "reply: sent+located" not in capsys.readouterr().out


def test_the_command_reads_no_secret_and_prints_none(tmp_path: Path, capsys, monkeypatch) -> None:
    """The command holds no wake secret at all; with one in its environment it
    still never appears in its output or in any file it writes."""
    monkeypatch.setenv("WEBHOOK_SECRET_MCP", SECRET)
    NoNetwork(monkeypatch)
    _run(tmp_path, FakeClient(), dry_run=True)
    _run(tmp_path, FakeClient(replies=[_sent()]), confirm=DIGEST)
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err
    for p in (tmp_path / "run").rglob("*"):
        if p.is_file():
            assert SECRET not in p.read_text(encoding="utf-8", errors="replace")
    src = Path(nbs.__file__).read_text(encoding="utf-8")
    assert "WEBHOOK_SECRET_MCP" not in src and "/proc" not in src


# -- the lane: the daemon keeps watching after the command exits --------------------


def test_the_note_lane_pages_when_no_reply_is_proven(tmp_path: Path) -> None:
    client = FakeClient(replies=[[]])
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE), created=lane.clock())
    lane._write_state(NOTE, wake={"pending": False, "task": "t"})
    proof_watch.arm_reply_watch(lane, NOTE)
    lane._now["t"] += proof_watch.REPLY_DEADLINE_SECONDS + 1  # type: ignore[attr-defined]
    lane.tick()
    alerts = [c for c in client.calls if c["action"] == "delivery_proof_alert"]
    assert [(a["lane"], a["job_id"], a["code"]) for a in alerts] == [("note", NOTE, "reply_unproven")]


def test_the_note_lane_closes_a_proven_watch_quietly(tmp_path: Path) -> None:
    client = FakeClient(replies=[_sent()])
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE), created=lane.clock())
    lane._write_state(NOTE, wake={"pending": False, "task": "t"})
    proof_watch.arm_reply_watch(lane, NOTE)
    lane.tick()
    assert lane._daemon_state(NOTE)["reply_watch"]["done"] is True
    assert "delivery_proof_alert" not in client.actions()


def test_a_tick_touches_the_note_tick_the_boot_smoke_reads(tmp_path: Path) -> None:
    lane = _lane(tmp_path, FakeClient())
    lane.jobs.mkdir(parents=True)
    lane.tick()
    assert (tmp_path / "run" / "note-tick").is_file()


def test_the_lane_is_started_with_the_others() -> None:
    from medchron import lanes

    assert "note_lane" in lanes.LANES and lanes._module("note_lane") is note_lane


# -- --recent ------------------------------------------------------------------------


def test_recent_lists_the_last_two_weeks_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "requests.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE requests (vendor_message_id TEXT PRIMARY KEY, internet_message_id TEXT, "
        "received_at REAL NOT NULL, sender TEXT, subject TEXT, reply_opening TEXT, opening_at REAL)"
    )
    now = time.time()
    for gid, age_days, subject in (("OLD", 20, "old"), ("A", 2, "Vasic 900417"), ("B", 1, "Other")):
        conn.execute(
            "INSERT INTO requests (vendor_message_id, received_at, sender, subject) VALUES (?,?,?,?)",
            (gid, now - age_days * 86400, REQUESTER, subject),
        )
    conn.commit()
    conn.close()
    assert [r["graph_message_id"] for r in nbs.recent(str(db), now=now)] == ["B", "A"]
    assert [r["graph_message_id"] for r in nbs.recent(str(db), match="900417", now=now)] == ["A"]
