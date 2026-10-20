"""`medchron rewake`: one stopped DELIVER turn started again under a new id.

The A&P 201167 alert (2026-10-20) was held by the send check, nothing went,
and re-arming its wake did nothing because the gateway drops a reused
handoff_id. These pin the supported re-send: a numbered id, the same stored
task, a fresh reply watch that pages again if the re-send also goes unsent,
and refusals wherever a re-send would be wrong.
"""

from __future__ import annotations

import json

from medchron import rewake
from medchron.lane_proof_cli import JOBS
from medchron.proof.watch import REPLY_DEADLINE_SECONDS
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
