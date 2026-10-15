"""`medchron.note_by_smd` and the note lane: SMD's approved words reach a firm
colleague in her own thread, through the smd-note skill's DELIVER wake.

WHY (2026-10-15). The Operator filed a document on SMD's request and could not
tell the colleague who asked. Each test below names the failure it stands
against; the wake secret is never printed, whichever path finds it.
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


def _run(tmp_path: Path, client: FakeClient, lane=None, posts: list | None = None, **over) -> int:
    posts = posts if posts is not None else []

    def post(url, secret, handoff_id, task):
        posts.append((url, secret, handoff_id, task))
        return "delivered"

    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s

    return nbs.run(
        _args(**over),
        client=client,
        lane=lane or _lane(tmp_path, client),
        secret=lambda: SECRET,
        post=post,
        sleep=sleep,
        clock=lambda: clock["t"],
        audit_db=str(tmp_path / "none.db"),
    )


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


# -- the live run -----------------------------------------------------------------


def test_the_live_run_records_wakes_and_arms_the_watch(tmp_path: Path, capsys) -> None:
    client = FakeClient(replies=[[], _sent()])
    lane = _lane(tmp_path, client)
    posts: list = []
    assert _run(tmp_path, client, lane, posts, confirm=DIGEST) == 0
    assert client.calls[0] == {
        "action": "note_record",
        "graph_message_id": GID,
        "text": "Hi,\n\nMatter 900417 is filed.",
        "approved_by": "smd",
        "confirm": DIGEST,
        "recorded_by": "root",
    }
    # Exactly the code-only wake, to the gate, under the daemon's own handoff id.
    [(url, secret, handoff, task)] = posts
    assert (url, secret, handoff) == ("http://127.0.0.1:8643", SECRET, f"note-{NOTE}")
    assert task == f"Run the smd-note skill's DELIVER mode for note job {NOTE}.\nKind: note."
    assert WAKE_JOB.search(task).groups() == ("note", NOTE)
    st = lane._daemon_state(NOTE)
    assert st["state"] == "delivered" and st["wake"]["pending"] is False
    assert st["reply_watch"]["kind"] == "note_job" and not st["reply_watch"].get("done")
    assert json.loads((lane.job_dir(NOTE) / "envelope.json").read_text())["requested_by"] == REQUESTER
    assert "reply: sent+located" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rows", "printed", "rc"),
    [
        (_sent("sent", True), "reply: sent+located", 0),
        (_sent("sent", False), "reply: sent-not-located", 1),
        (_sent("unknown", None), "reply: unknown", 1),
        ([], "reply: not sent", 1),
    ],
)
def test_each_reply_outcome_is_named(tmp_path: Path, capsys, rows, printed, rc) -> None:
    assert _run(tmp_path, FakeClient(replies=[rows]), confirm=DIGEST, wait_min=1.0) == rc
    assert printed in capsys.readouterr().out


def test_a_reply_under_another_key_is_not_this_notes(tmp_path: Path, capsys) -> None:
    other = [{"key": f"note_job:{NOTE}:2:delivered", "outcome": "sent", "located": True}]
    assert _run(tmp_path, FakeClient(replies=[other]), confirm=DIGEST, wait_min=0.5) == 1
    assert "reply: not sent" in capsys.readouterr().out


def test_a_wake_that_never_left_stays_pending_for_the_lane(tmp_path: Path) -> None:
    client = FakeClient()
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE))
    out = nbs.wake(lane, NOTE, SECRET, post=lambda *a: "unreachable (ConnectionRefusedError)")
    assert out.startswith("left pending")
    assert lane._daemon_state(NOTE)["wake"]["pending"] is True
    assert "reply_watch" not in lane._daemon_state(NOTE)
    assert lane._wakes_pending() == [NOTE]


def test_no_secret_leaves_the_wake_for_the_lane(tmp_path: Path) -> None:
    client = FakeClient()
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE))
    called: list = []
    assert nbs.wake(lane, NOTE, "", post=lambda *a: called.append(a) or "delivered").startswith("left pending")
    assert called == [] and lane._daemon_state(NOTE)["wake"]["pending"] is True


# -- the secret --------------------------------------------------------------------


def _proc(tmp_path: Path, pid: str, cmdline: bytes, environ: dict[str, str]) -> None:
    d = tmp_path / "proc" / pid
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmdline)
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()) + b"\0")


def test_the_secret_is_read_from_the_daemons_environ(tmp_path: Path) -> None:
    _proc(tmp_path, "1", b"bash\0/app/bootstrap.sh", {})
    _proc(tmp_path, "77", b"/opt/medchron/.venv/bin/python\0-m\0medchron.daemon", {"WEBHOOK_SECRET_MCP": SECRET})
    assert nbs.wake_secret(env={}, proc=tmp_path / "proc") == SECRET


def test_the_secret_falls_back_to_the_gateway(tmp_path: Path) -> None:
    _proc(tmp_path, "9", b"hermes\0gateway", {"WEBHOOK_SECRET_MCP": SECRET})
    assert nbs.wake_secret(env={"SMD_GATEWAY_PID": "9"}, proc=tmp_path / "proc") == SECRET
    assert nbs.wake_secret(env={}, proc=tmp_path / "nothing") == ""


def test_the_secret_never_reaches_stdout_or_stderr(tmp_path: Path, capsys, monkeypatch) -> None:
    """FALSIFIER: print the bearer anywhere (a debug line, the wake's outcome)
    and this goes red, on every path: dry run, live run, a failed wake."""
    _run(tmp_path, FakeClient(), dry_run=True)
    _run(tmp_path, FakeClient(replies=[_sent()]), confirm=DIGEST)
    lane = _lane(tmp_path, FakeClient())
    nbs._write_job(lane, "01M4NOTEBBBBBBBBBBBBBBBBBB", REQUESTER, GID, "t")
    nbs.wake(lane, "01M4NOTEBBBBBBBBBBBBBBBBBB", SECRET, post=lambda *a: "status 401")
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err
    for p in (tmp_path / "run").rglob("*"):
        if p.is_file():
            assert SECRET not in p.read_text(encoding="utf-8", errors="replace")


# -- the lane: the daemon keeps watching after the command exits --------------------


def test_the_note_lane_pages_when_no_reply_is_proven(tmp_path: Path) -> None:
    client = FakeClient(replies=[[]])
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE))
    lane._write_state(NOTE, wake={"pending": False, "task": "t"})
    proof_watch.arm_reply_watch(lane, NOTE)
    lane._now["t"] += proof_watch.REPLY_DEADLINE_SECONDS + 1  # type: ignore[attr-defined]
    lane.tick()
    alerts = [c for c in client.calls if c["action"] == "delivery_proof_alert"]
    assert [(a["lane"], a["job_id"], a["code"]) for a in alerts] == [("note", NOTE, "reply_unproven")]


def test_the_note_lane_closes_a_proven_watch_quietly(tmp_path: Path) -> None:
    client = FakeClient(replies=[_sent()])
    lane = _lane(tmp_path, client)
    nbs._write_job(lane, NOTE, REQUESTER, GID, note_lane.wake_task(NOTE))
    lane._write_state(NOTE, wake={"pending": False, "task": "t"})
    proof_watch.arm_reply_watch(lane, NOTE)
    lane.tick()
    assert lane._daemon_state(NOTE)["reply_watch"]["done"] is True
    assert "delivery_proof_alert" not in client.actions()


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
