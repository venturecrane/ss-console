"""`jobs rewake`: one stopped DELIVER turn started again under a new id.

The A&P 201167 alert (2026-10-20) was held by the send check, nothing went,
and re-arming its wake did nothing because the gateway drops a reused
handoff_id. These pin the supported re-send: a numbered id, the same stored
task, a fresh reply watch that pages again if the re-send also goes unsent,
and refusals wherever a re-send would be wrong.
"""

from __future__ import annotations

import json

from jobs import rewake
from jobs.lane_proof_cli import JOBS
from jobs.proof.watch import REPLY_DEADLINE_SECONDS
from test_upload_daemon import _envelope, _FakeGate, _wake_daemon


def _submit(d, broker, job_id):
    """A real request: it has a requester, so a reply is owed and watched."""
    broker.rows[job_id] = {"state": "submitted"}
    env = {**_envelope(job_id), "requested_by": "staff@example.com", "request_ref": "msg-1"}
    (d.queue / f"{job_id}.json").write_text(json.dumps(env))


def _stopped(tmp_path):
    """A delivered job whose DELIVER turn ran and whose reply never showed."""
    gate = _FakeGate()
    d, broker = _wake_daemon(tmp_path, gate.url)
    now = [1_000_000.0]
    d.clock = lambda: now[0]
    _submit(d, broker, "01A")
    d.tick()
    d.tick()  # the wake goes
    now[0] += REPLY_DEADLINE_SECONDS + 1
    d.tick()  # the watch settles: nothing by the deadline
    return gate, d, broker, now


def _alerts(broker):
    return [r for r in broker.proof_requests if r["action"] == "delivery_proof_alert"]


def _rewake(d, *, again=False):
    return rewake.rewake(d.run_dir, "medchron", "01A", "scott", "held by the send check", again, clock=d.clock)


def test_a_stopped_turn_is_sent_again_under_a_new_id_and_watched_again(tmp_path, capsys):
    gate, d, broker, now = _stopped(tmp_path)
    assert [a["code"] for a in _alerts(broker)] == ["reply_unproven"]
    first_task = gate.requests[0]["body"]["task"]

    assert _rewake(d) == 0
    assert "medchron-01A-r1" in capsys.readouterr().out
    d.tick()
    assert [r["body"]["handoff_id"] for r in gate.requests] == ["medchron-01A", "medchron-01A-r1"]
    assert gate.requests[1]["body"]["task"] == first_task
    st = d._daemon_state("01A")
    assert st["wake"]["pending"] is False and not st["reply_watch"].get("done")
    assert st["rewakes"][0]["by"] == "scott"

    # The re-send also goes unsent: it pages again, the same day.
    now[0] += REPLY_DEADLINE_SECONDS + 1
    d.tick()
    gate.close()
    assert [a["code"] for a in _alerts(broker)] == ["reply_unproven", "reply_unproven"]

    # A second re-send takes the next number.
    assert _rewake(d) == 0
    assert rewake.handoff_id("medchron", "01A", d._daemon_state("01A")["wake"]) == "medchron-01A-r2"


def test_a_first_wake_keeps_its_original_id():
    assert rewake.handoff_id("negotiation", "01N", {"task": "t"}) == "negotiation-01N"


def test_it_refuses_while_the_reply_watch_is_still_open(tmp_path, capsys):
    gate = _FakeGate()
    d, broker = _wake_daemon(tmp_path, gate.url)
    _submit(d, broker, "01A")
    d.tick()
    d.tick()
    gate.close()
    assert _rewake(d) == 1
    assert "still open" in capsys.readouterr().out
    assert d._daemon_state("01A")["wake"].get("resend") is None


def test_it_refuses_a_wake_that_is_still_pending(tmp_path):
    gate = _FakeGate()
    d, broker = _wake_daemon(tmp_path, gate.url)
    _submit(d, broker, "01A")
    d.tick()  # delivered; the wake waits for the next tick
    gate.close()
    assert _rewake(d) == 1


def test_a_proven_reply_needs_again_to_email_the_requester_twice(tmp_path, capsys):
    gate, d, broker, now = _stopped(tmp_path)
    p = d.run_dir / JOBS["medchron"] / "01A" / "daemon.json"
    st = json.loads(p.read_text())
    st["reply_watch"].update(done=True, proven_at=now[0])
    p.write_text(json.dumps(st))
    assert _rewake(d) == 1
    assert "--again" in capsys.readouterr().out
    assert _rewake(d, again=True) == 0
    gate.close()


def test_an_unknown_job_or_one_without_a_task_is_refused(tmp_path):
    gate = _FakeGate()
    d, broker = _wake_daemon(tmp_path, gate.url)
    gate.close()
    assert rewake.rewake(d.run_dir, "medchron", "nope", "scott", "x", False) == 2


# -- 2026-11-06: a thread SMD already answered gets no second completion ------

ANSWERED = (
    "drafting job 01A's thread already holds a reply sent from this mailbox after the job finished "
    "(an SMD hand reply counts); a second completion reply is refused and nothing was sent."
)


class _Thread:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def __call__(self, kind, ident):
        self.asked.append((kind, ident))
        return self.answer


def test_a_rewake_after_an_smd_reply_in_the_thread_is_refused_in_one_line(tmp_path, capsys):
    """FALSIFIER: on origin/main the rewake re-arms, and #3810's code-written
    completion goes to the attorney a second time."""
    gate, d, _broker, _now = _stopped(tmp_path)
    gate.close()
    thread = _Thread({"checked": True, "answered": ANSWERED})
    assert rewake.rewake(d.run_dir, "medchron", "01A", "scott", "why", False, clock=d.clock, thread=thread) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [f"refused: {ANSWERED}"]
    assert thread.asked == [("medchron_job", "01A")]
    assert d._daemon_state("01A")["wake"].get("resend") is None and "rewakes" not in d._daemon_state("01A")


def test_a_rewake_that_could_not_check_the_thread_re_arms_nothing(tmp_path, capsys):
    gate, d, _broker, _now = _stopped(tmp_path)
    gate.close()
    thread = _Thread({"checked": False, "answered": "", "why": "broker down"})
    assert rewake.rewake(d.run_dir, "medchron", "01A", "scott", "why", False, clock=d.clock, thread=thread) == 1
    assert "could not check" in capsys.readouterr().out
    assert d._daemon_state("01A")["wake"].get("resend") is None


def test_a_rewake_in_a_clear_thread_still_re_arms(tmp_path):
    gate, d, _broker, _now = _stopped(tmp_path)
    gate.close()
    thread = _Thread({"checked": True, "answered": ""})
    assert rewake.rewake(d.run_dir, "medchron", "01A", "scott", "why", False, clock=d.clock, thread=thread) == 0


def test_the_cli_asks_the_broker_about_the_thread(tmp_path, monkeypatch, capsys):
    """``_run`` always checks: no broker socket is a refusal, never a skip."""
    from types import SimpleNamespace

    gate, d, _broker, _now = _stopped(tmp_path)
    gate.close()
    args = SimpleNamespace(run_dir=str(d.run_dir), lane="medchron", job_id="01A", by="scott", why="why", again=False)
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    assert rewake._run(args) == 2
    asked = []

    class Client:
        def __init__(self, sock):
            pass

        def _request(self, req):
            asked.append(req)
            return {"ok": True, "thread": {"checked": True, "answered": ANSWERED}}

    monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", "/tmp/sock")
    monkeypatch.setattr("jobs.broker_client.BrokerClient", Client)
    assert rewake._run(args) == 1
    assert asked == [{"action": "delivery_reply_status", "kind": "medchron_job", "job_id": "01A", "thread": True}]


def _drafting_like(tmp_path, gate, answer):
    """A daemon whose lane is drafting, so its DELIVER wake asks first."""
    d, broker = _wake_daemon(tmp_path, gate.url)
    d.LANE = "drafting"
    calls = []

    def _request(req):
        broker.proof_requests.append(req)
        if req.get("thread"):
            calls.append(req)
            return {"ok": True, "replies": [], "thread": answer}
        return {"ok": True, "replies": []}

    broker._request = _request
    return d, broker, calls


def test_a_drafting_deliver_wake_into_an_answered_thread_is_not_sent(tmp_path, caplog):
    """FALSIFIER: on origin/main the wake is posted and the completion turn
    sends the job's reply after SMD's."""
    gate = _FakeGate()
    d, broker, calls = _drafting_like(tmp_path, gate, {"checked": True, "answered": ANSWERED})
    _submit(d, broker, "01A")
    d.tick()
    d.tick()
    d.tick()
    gate.close()
    assert gate.requests == []
    assert [c["kind"] for c in calls] == ["drafting_job"]  # asked once, then closed
    wake = d._daemon_state("01A")["wake"]
    assert wake["pending"] is False and wake["outcome"] == "refused: thread_answered"
    assert any("DELIVER wake not sent" in r.getMessage() for r in caplog.records)


def test_a_drafting_deliver_wake_into_a_clear_thread_goes_and_asks_once(tmp_path):
    gate = _FakeGate()
    d, broker, calls = _drafting_like(tmp_path, gate, {"checked": True, "answered": ""})
    _submit(d, broker, "01A")
    d.tick()
    d.tick()
    gate.close()
    assert [r["body"]["handoff_id"] for r in gate.requests] == ["drafting-01A"]
    assert len(calls) == 1


def test_a_drafting_wake_goes_when_the_broker_could_not_check(tmp_path):
    """The broker's bind re-checks the thread at the send; an unanswered
    check never holds the completion back."""
    gate = _FakeGate()
    d, broker, _calls = _drafting_like(tmp_path, gate, {"checked": False, "answered": ""})
    _submit(d, broker, "01A")
    d.tick()
    d.tick()
    gate.close()
    assert len(gate.requests) == 1
