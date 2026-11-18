"""A chronology ending the broker did not take (``jobs/medchron/unrecorded.py``).

One path, whatever the broker answered: the ending is kept and retried from
``poll_requests``; the firm's message never waits for it; past the bound SMD
is paged ONCE (across a restart and past the re-alert window) and the record
is still tried; at the wipe it is paged once more, naming the job, and the
running row is closed by the dead-row sweep. Every page says what is true of
the job: its ending, whether a wake was owed, and whether it went.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.broker_client import BrokerError
from jobs.medchron import unrecorded
from jobs.proof import watch
from test_upload_daemon import FakeBroker, _daemon, _submit

INTERNAL = "broker refused: internal_error: internal error; see broker log"


class ErringBroker(FakeBroker):
    """Answers a job's ENDING record with the broker's ``internal_error`` (a
    locked database) ``fail`` times, then takes it; every other call works,
    the dead-row sweep's close among them."""

    def __init__(self, fail: int) -> None:
        super().__init__()
        self.fail = fail

    def record(self, job_id, state, fields):
        run = fields.get("run") or {}
        ending = run.get("event") in ("end", "stop") and run.get("code") != "ending_unrecorded"
        if ending and self.fail > 0:
            self.fail -= 1
            raise BrokerError(INTERNAL)
        return super().record(job_id, state, fields)


def _lane(tmp_path: Path, fail: int):
    d, _ = _daemon(tmp_path, wipe_hours=72)
    broker = ErringBroker(fail)
    d.broker = broker
    _submit(d, broker, "01A")
    return d, broker


def _pages(broker: FakeBroker, unit: str = "") -> list[dict]:
    return [
        r
        for r in broker.proof_requests
        if r.get("code") == unrecorded.CODE and (not unit or r["job_id"] and unit in json.dumps(r))
    ]


def _polls(d, n: int) -> None:
    for _ in range(n):
        d._now["t"] += unrecorded.RETRY_SECONDS + 1
        d.poll_requests()


def test_an_internal_error_once_then_accepted_is_recorded_unaided_with_no_page(tmp_path):
    d, broker = _lane(tmp_path, fail=1)
    assert d.tick() == "delivered"
    st = d._daemon_state("01A")
    # The firm's message is not held for our bookkeeping: it is pending to go now.
    assert st["record_pending"]["state"] == "delivered" and st["wake"]["pending"]
    assert "01A" in d._wakes_pending()
    _polls(d, 1)
    assert d._daemon_state("01A").get("record_pending") is None
    ended = [(s, f.get("run", {}).get("event")) for j, s, f in broker.records if s == "delivered"]
    assert ended == [("delivered", "end")]  # no record_late note: the run is unaided
    assert _pages(broker) == []


def test_past_the_bound_smd_is_paged_once_across_a_restart_and_the_record_still_lands(tmp_path):
    d, broker = _lane(tmp_path, fail=10**6)
    assert d.tick() == "delivered"
    _polls(d, unrecorded.MAX_ATTEMPTS + 3)
    pending = d._daemon_state("01A")["record_pending"]
    assert pending["paged"] and pending["attempts"] >= unrecorded.MAX_ATTEMPTS
    assert len(_pages(broker)) == 1
    # A restarted lane on the same seat, a day later (past the re-alert
    # window): the kept record is still tried, and SMD is not paged again.
    d2, _ = _daemon(tmp_path, wipe_hours=72)
    d2.broker = broker
    d2._now["t"] = d._now["t"] + watch.REALERT_SECONDS + 1
    _polls(d2, 5)
    assert len(_pages(broker)) == 1
    broker.fail = 0
    _polls(d2, 1)
    assert d2._daemon_state("01A").get("record_pending") is None
    events = [f.get("run", {}).get("event") for j, s, f in broker.records if s == "delivered"]
    assert events == ["end", "record_late"]  # paged for it, so the run is aided
    assert len(_pages(broker)) == 1


def test_inside_the_bound_nothing_is_paged(tmp_path):
    d, broker = _lane(tmp_path, fail=10**6)
    d.tick()
    _polls(d, unrecorded.MAX_ATTEMPTS - 2)
    assert _pages(broker) == [] and not d._daemon_state("01A")["record_pending"].get("paged")


def _said(d, state: str, reason=None, wake=None) -> str:
    d.job_dir("01X").mkdir(parents=True, exist_ok=True)
    d._write_state("01X", wake=wake)
    return unrecorded.wake_said(d, "01X", state, reason)


def test_every_endings_page_says_what_is_true_of_it(tmp_path):
    d, _ = _daemon(tmp_path)
    assert _said(d, "delivered", wake={"pending": False, "sent_at": 1.0, "outcome": "delivered"}) == (
        "its wake was owed and went (the firm is told it was delivered)"
    )
    assert _said(d, "delivered", wake={"pending": True}) == (
        "its wake is owed and has not gone yet (the firm is told it was delivered when it does)"
    )
    assert _said(d, "delivered", wake={"pending": False, "outcome": "failed: status 503"}) == (
        "its wake was owed and did not go (failed: status 503)"
    )
    held = _said(d, "held", reason="gate_held: an audit check", wake=None)
    assert held == "no wake is owed for a held chronology and none was sent (the firm hears nothing of it)"
    failed = _said(d, "failed", reason="no verdict", wake=None)
    assert failed == "no wake is owed for a failed chronology and none was sent (the firm hears nothing of it)"
    rerouted = "refused: list_matter: the matter already holds a delivered chronology"
    assert _said(d, "held", reason=rerouted, wake={"pending": False, "sent_at": 1.0}) == (
        "its wake was owed and went (the work is rerouted; the firm is sent nothing)"
    )
    for text in (held, failed):
        assert "went anyway" not in text and "message went" not in text


def test_the_bound_page_names_the_ending_and_its_wake(tmp_path):
    d, broker = _lane(tmp_path, fail=10**6)
    d.tick()
    _polls(d, unrecorded.MAX_ATTEMPTS)
    (page,) = _pages(broker)
    assert page["detail"].startswith("its delivered ending is not recorded after ")
    assert "internal_error" in page["detail"] and "its wake is owed and has not gone yet" in page["detail"]
    assert "already running" in page["detail"]


def test_at_the_wipe_it_is_paged_once_more_and_the_running_row_is_closed(tmp_path):
    from jobs import retention

    d, broker = _lane(tmp_path, fail=10**6)
    d.tick()
    d._write_state("01A", wake={**d._daemon_state("01A")["wake"], "pending": False, "sent_at": d.clock()})
    _polls(d, unrecorded.MAX_ATTEMPTS)
    assert broker.rows["01A"]["state"] == "running"
    broker.rows["01A"]["updated_at"] = watch._iso(d.clock())  # when it started running (the fake clock)
    d._now["t"] += 73 * 3600
    d._dead_holds_swept_at = 0.0
    assert d.wipe_expired() == ["01A"] and not (d.jobs / "01A").exists()
    wiped = [p for p in _pages(broker) if p["detail"].startswith("its workdir is wiped on schedule")]
    assert len(wiped) == 1 and "its delivered ending still not recorded" in wiped[0]["detail"]
    assert "its wake was owed and went" in wiped[0]["detail"]
    # The dead-row sweep closed the row, so the same work can be submitted again.
    assert broker.rows["01A"]["state"] == "failed"
    (closed,) = [f for j, s, f in broker.records if s == "failed"]
    assert closed["reason"] == retention.DEAD_RUN_REASON and closed["run"]["code"] == "ending_unrecorded"


def test_a_wipe_page_that_cannot_be_written_keeps_the_workdir_only_for_the_grace(tmp_path):
    from jobs import retention

    d, broker = _lane(tmp_path, fail=10**6)
    d.tick()
    d._write_state("01A", wake={**d._daemon_state("01A")["wake"], "pending": False, "sent_at": d.clock()})
    real = broker._request

    def down(req):
        if req["action"] == "delivery_proof_alert":
            raise BrokerError("broker socket: down")
        return real(req)

    broker._request = down
    d._now["t"] += 73 * 3600
    assert d.wipe_expired() == [] and (d.jobs / "01A").exists()
    d._now["t"] += retention.WAKE_WIPE_GRACE_HOURS * 3600
    assert d.wipe_expired() == ["01A"]
