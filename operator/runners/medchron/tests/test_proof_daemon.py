"""Proven delivery, the daemon's half (``proof/watch.py``, ``proof/gate.py``):
what the daemon records when a runner's verdict claims a delivery, and what it
watches after the DELIVER wake.

WHY (2026-10-13). "Delivered" was the runner's own claim: a dry run was
recorded delivered, and three wrong outputs reached a firm marked delivered
with no alert. Every test here is a falsifier: it was run red against the
guard removed, then green with it restored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_demand_lane as demand_t
import test_upload_daemon as ud
from medchron import resume as resume_mod
from medchron.proof import watch

SHA = "c" * 64
PROOF = {
    "readback": "confirmed",
    "files": [{"name": "Chronology - Alpha Example.docx", "file_id": "up-9", "size": 4100, "sha256": SHA}],
    "review": "passed",
    "review_cents": 37,
    "review_model": "example-review-model",
}


def _runner(*outcomes: dict) -> str:
    """A fake runner printing exactly these outcomes as its stdout verdict."""
    return f"print({json.dumps(json.dumps(list(outcomes)))})\n"


def _unit(unit: str = "alpha", outcome: str = "delivered", **kw) -> dict:
    return {
        "unit": unit,
        "outcome": outcome,
        "reason": kw.pop("reason", None),
        "stage": kw.pop("stage", None),
        "dollars": 1.0,
        "pages": 3,
        "documents": 1,
        "folder_id": "folder-9",
        "files": [{"name": "A.docx", "sha256": SHA, "bytes": 4100, "confirmed": True}],
        **kw,
    }


def _proof_calls(broker: ud.FakeBroker, action: str) -> list[dict]:
    return [r for r in broker.proof_requests if r["action"] == action]


def _codes(broker: ud.FakeBroker) -> list[str]:
    return [r["code"] for r in _proof_calls(broker, "delivery_proof_alert")]


def _run(tmp_path: Path, *outcomes: dict, envelope: dict | None = None) -> tuple[ud.Daemon, ud.FakeBroker, str]:
    d, broker = ud._daemon(tmp_path, _runner(*outcomes))
    broker.rows["01A"] = {"state": "submitted"}
    (d.queue / "01A.json").write_text(json.dumps(envelope or ud._envelope("01A")))
    return d, broker, d.tick()


# ---- settle: what a verdict may be recorded as ---------------------------------


def test_a_dry_run_verdict_is_held_on_the_chronology_lane_and_pages(tmp_path):
    d, broker, state = _run(tmp_path, _unit(outcome="dry_run"))
    assert state == "held"
    job, recorded, fields = broker.records[-1]
    assert recorded == "held" and fields["reason"].startswith("dry_run_reported")
    assert "folder_id" not in fields and "delivery" not in fields
    assert _codes(broker) == ["dry_run_reported"]
    assert not _proof_calls(broker, "delivery_proof_record")


class _ProofTwin:
    """A lane proof run's twin (``proof_run.twin``) as settle sees it: the
    proof flag in its child env, and a broker that must never be written."""

    LANE = "demand"
    child_env = {"MEDCHRON_PROOF_RUN": "1"}

    class broker:
        @staticmethod
        def _request(body):
            raise AssertionError(f"a proof run wrote to the broker: {body}")


def test_a_proof_runs_dry_run_is_its_expected_ending_and_pages_nothing():
    """2026-10-13: a lane proof run ends ``dry_run`` by design and its twin's
    broker reads through to the real one, so settling it as a stray dry run
    would page SMD falsely on every proof run."""
    fields = {"reason": None}
    state, out = watch.settle(_ProofTwin(), "01A", [{"outcome": "dry_run", "stage": "file"}], "dry_run", fields)
    assert (state, out) == ("dry_run", {"reason": None})
    assert watch.alert(_ProofTwin(), "01A", "review_held", "x") is False


def test_a_dry_run_on_a_lane_without_a_resumable_hold_is_failed(tmp_path):
    out = [{"unit": "demand", "kind": "demand", "outcome": "dry_run", "stage": "file", "dollars": 0.5}]
    lane, client = demand_t._lane(tmp_path, script=f"import json\nprint(json.dumps({out!r}))\n")
    demand_t._submit(lane, client)
    assert lane.tick() == "failed"
    assert client.records[-1][1] == "failed" and client.records[-1][2]["reason"].startswith("dry_run_reported")
    alerts = [r for r in client.requests if r["action"] == "delivery_proof_alert"]
    assert [a["code"] for a in alerts] == ["dry_run_reported"] and alerts[0]["lane"] == "demand"


def test_a_delivered_verdict_without_a_proof_block_is_failed_and_pages(tmp_path):
    d, broker, state = _run(tmp_path, _unit())
    assert state == "failed"
    fields = broker.records[-1][2]
    assert fields["reason"].startswith("proof_missing") and "delivery" not in fields
    assert _codes(broker) == ["proof_missing"]
    assert not _proof_calls(broker, "delivery_proof_record")


@pytest.mark.parametrize(
    ("proof", "code"),
    [
        ({**PROOF, "readback": "short"}, "readback_unproven"),
        ({**PROOF, "files": []}, "readback_unproven"),
        ({**PROOF, "review": None}, "review_missing"),
        ({**PROOF, "review": "held"}, "review_missing"),
    ],
)
def test_a_proof_block_that_does_not_prove_is_failed(tmp_path, proof, code):
    d, broker, state = _run(tmp_path, _unit(proof=proof))
    assert state == "failed" and broker.records[-1][2]["reason"].startswith(code)
    assert _codes(broker) == [code]


def test_a_proven_delivery_records_one_proof_row_with_ids_and_no_names(tmp_path):
    d, broker, state = _run(tmp_path, _unit(proof=PROOF))
    assert state == "delivered"
    assert broker.records[-1][2]["folder_id"] == "folder-9"
    records = _proof_calls(broker, "delivery_proof_record")
    assert len(records) == 1
    rec = records[0]
    assert rec["review_cents"] == 37 and rec["review"] == "passed" and rec["review_model"] == "example-review-model"
    assert rec["files"][0]["file_id"] == "up-9" and rec["files"][0]["sha256"] == SHA
    # The runner's proof carries the name; what the daemon hands on may too, but
    # the broker strips it (test_delivery_proof.py). Nothing else is alerted.
    assert _codes(broker) == []


def test_one_unproven_unit_fails_a_chronology_whose_other_unit_is_proven(tmp_path):
    d, broker, state = _run(tmp_path, _unit("alpha", proof=PROOF), _unit("beta"))
    assert state == "failed"
    assert broker.records[-1][2]["reason"].startswith("proof_missing: beta")
    assert not _proof_calls(broker, "delivery_proof_record")


def test_a_review_hold_pages_smd_under_review_held(tmp_path):
    held = _unit(outcome="held", reason="review_held: 1 MUST-FIX (a date after today)", stage="review")
    d, broker, state = _run(tmp_path, held)
    assert state == "held"
    assert _codes(broker) == ["review_held"]


def test_an_ordinary_hold_pages_nothing(tmp_path):
    d, broker, state = _run(tmp_path, _unit(outcome="held", reason="decision: 2 unplaced files", stage="decide"))
    assert state == "held" and _codes(broker) == []


def test_alerts_a_delivered_outcome_carries_are_raised_once_each(tmp_path):
    proof = {**PROOF, "alerts": ["review_held", "review_held", "not_a_code"]}
    d, broker, state = _run(
        tmp_path, _unit("alpha", proof=proof), _unit("beta", proof={**PROOF, "alerts": ["review_held"]})
    )
    assert state == "delivered"
    assert _codes(broker) == ["review_held"]
    assert len(_proof_calls(broker, "delivery_proof_record")) == 1


# ---- the DELIVER wake and the reply watch --------------------------------------


def _wake(tmp_path: Path, gate_url: str, *outcomes: dict, requester: str | None = "admin@example.test"):
    d, broker = ud._wake_daemon(tmp_path, gate_url, script=_runner(*outcomes))
    broker.rows["01A"] = {"state": "submitted"}
    env = ud._envelope("01A")
    if requester:
        env["requested_by"] = requester
    (d.queue / "01A.json").write_text(json.dumps(env))
    d.tick()
    return d, broker


def test_a_consumed_wake_arms_the_reply_watch_for_a_delivered_job_with_a_requester(tmp_path):
    gate = ud._FakeGate()
    d, broker = _wake(tmp_path, gate.url, _unit(proof=PROOF))
    d.dispatch_wakes()
    gate.close()
    w = d._daemon_state("01A")["reply_watch"]
    assert w["kind"] == "medchron_job" and w["state"] == "delivered" and w["attempt"] == 1
    assert w["due"] == d.clock() + watch.REPLY_DEADLINE_SECONDS


def test_a_consumed_wake_arms_the_watch_for_a_held_job(tmp_path):
    gate = ud._FakeGate()
    d, broker = _wake(tmp_path, gate.url, _unit(outcome="held", reason="decision: x", stage="decide"))
    d.dispatch_wakes()
    gate.close()
    assert d._daemon_state("01A")["reply_watch"]["state"] == "held"


def test_a_rehearsal_job_with_no_requester_arms_no_watch(tmp_path):
    gate = ud._FakeGate()
    d, broker = _wake(tmp_path, gate.url, _unit(proof=PROOF), requester=None)
    d.dispatch_wakes()
    gate.close()
    assert (d._daemon_state("01A").get("wake") or {}).get("pending") is False
    assert "reply_watch" not in d._daemon_state("01A")


def test_a_failed_job_arms_no_watch_since_no_reply_is_owed(tmp_path):
    gate = ud._FakeGate()
    d, broker = _wake(tmp_path, gate.url, _unit())  # unproven: recorded failed
    d.dispatch_wakes()
    gate.close()
    assert d._daemon_state("01A")["state"] == "failed" and "reply_watch" not in d._daemon_state("01A")


def test_a_negotiation_notice_arms_its_watch_without_a_requester(tmp_path):
    d, broker = ud._daemon(tmp_path)
    d.job_dir("N1").mkdir(parents=True)
    d._write_state("N1", state="delivered", notice=True)
    watch.arm_reply_watch(d, "N1")
    assert d._daemon_state("N1")["reply_watch"]["state"] == "delivered"


def test_five_failed_wake_attempts_page_wake_lost_once(tmp_path):
    gate = ud._FakeGate(status=404, body=b'{"error": "unknown route"}')
    d, broker = _wake(tmp_path, gate.url, _unit(proof=PROOF))
    for _ in range(7):
        d.dispatch_wakes()
    gate.close()
    assert _codes(broker) == ["wake_lost"]
    assert "reply_watch" not in d._daemon_state("01A")


class ReplyBroker(ud.FakeBroker):
    """A broker whose ``delivery_reply_status`` answers from ``self.replies``."""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[dict] = []
        self.expected_key: str | None = None

    def _request(self, req):
        if req["action"] == "delivery_reply_status":
            self.proof_requests.append(req)
            out = {"ok": True, "replies": list(self.replies)}
            if self.expected_key is not None:
                out["expected_key"] = self.expected_key
            return out
        return super()._request(req)


def _watching(tmp_path: Path, *, attempts: int = 1, state: str = "delivered"):
    d, _ = ud._daemon(tmp_path)
    broker = ReplyBroker()
    d.broker = broker
    d.job_dir("01A").mkdir(parents=True)
    d._write_state("01A", state=state, attempts=attempts)
    d._write_state(
        "01A",
        reply_watch={"kind": "medchron_job", "due": d.clock() + 1200, "attempt": attempts, "state": state},
    )
    return d, broker


def _row(key: str, outcome: str = "sent", located: bool | None = True) -> dict:
    return {"key": key, "outcome": outcome, "located": located, "claimed_at": "2026-10-13T00:00:00Z"}


def test_a_sent_and_located_reply_closes_the_watch_with_no_alert(tmp_path):
    d, broker = _watching(tmp_path)
    broker.replies = [_row("medchron_job:01A:1:delivered")]
    d.dispatch_wakes()
    assert d._daemon_state("01A")["reply_watch"]["done"] is True and _codes(broker) == []


def test_an_unknown_send_pages_reply_unproven(tmp_path):
    d, broker = _watching(tmp_path)
    broker.replies = [_row("medchron_job:01A:1:delivered", outcome="unknown", located=None)]
    d.dispatch_wakes()
    assert _codes(broker) == ["reply_unproven"] and d._daemon_state("01A")["reply_watch"]["done"] is True


def test_a_sent_reply_the_broker_could_not_locate_closes_without_a_second_page(tmp_path):
    d, broker = _watching(tmp_path)
    broker.replies = [_row("medchron_job:01A:1:delivered", located=False)]
    d.dispatch_wakes()
    assert _codes(broker) == [] and d._daemon_state("01A")["reply_watch"]["outcome"] == "unlocated"


def test_no_reply_before_the_deadline_waits_and_after_it_pages(tmp_path):
    d, broker = _watching(tmp_path)
    d.dispatch_wakes()
    assert _codes(broker) == [] and not d._daemon_state("01A")["reply_watch"].get("done")
    d._now["t"] += watch.REPLY_DEADLINE_SECONDS + 1
    d.dispatch_wakes()
    assert _codes(broker) == ["reply_unproven"]
    d.dispatch_wakes()  # the watch is closed; it never pages twice
    assert _codes(broker) == ["reply_unproven"]


@pytest.mark.parametrize(
    "key", ["medchron_job:01A:1:held", "medchron_job:01A:0:delivered", "medchron_job:01AB:1:delivered"]
)
def test_a_reply_for_another_attempt_or_outcome_does_not_satisfy_the_watch(tmp_path, key):
    d, broker = _watching(tmp_path)
    broker.replies = [_row(key)]
    d._now["t"] += watch.REPLY_DEADLINE_SECONDS + 1
    d.dispatch_wakes()
    assert _codes(broker) == ["reply_unproven"]


def test_the_broker_named_reply_key_is_what_the_watch_matches(tmp_path):
    """A resumed chronology: the daemon has started it twice, the chronology
    ledger has no attempt (its key is always attempt 1). The broker names the
    key the reply is owed under, and that row satisfies the watch; a row the
    daemon's own counter would have matched does not."""
    d, broker = _watching(tmp_path, attempts=2)
    broker.expected_key = "medchron_job:01A:1:delivered"
    broker.replies = [_row("medchron_job:01A:1:delivered")]
    d.dispatch_wakes()
    assert _codes(broker) == [] and d._daemon_state("01A")["reply_watch"]["done"] is True

    d2, broker2 = _watching(tmp_path / "second", attempts=2)
    broker2.expected_key = "medchron_job:01A:1:delivered"
    broker2.replies = [_row("medchron_job:01A:2:delivered")]
    d2._now["t"] += watch.REPLY_DEADLINE_SECONDS + 1
    d2.dispatch_wakes()
    assert _codes(broker2) == ["reply_unproven"]


def test_alerts_deduplicate_per_code_and_attempt_and_re_raise_after_a_day(tmp_path):
    d, broker = ud._daemon(tmp_path)
    d.job_dir("01A").mkdir(parents=True)
    d._write_state("01A", state="held", attempts=1)
    assert watch.alert(d, "01A", "review_held", "x") is True
    assert watch.alert(d, "01A", "review_held", "x") is False
    assert watch.alert(d, "01A", "readback_mismatch", "x") is True  # another code
    d._write_state("01A", attempts=2)
    assert watch.alert(d, "01A", "review_held", "x") is True  # another attempt
    d._now["t"] += watch.REALERT_SECONDS + 1
    assert watch.alert(d, "01A", "review_held", "x") is True  # still open a day on
    assert _codes(broker) == ["review_held", "readback_mismatch", "review_held", "review_held"]
    with pytest.raises(ValueError):
        watch.alert(d, "01A", "not_a_code")


def test_an_alert_the_broker_cannot_take_is_logged_never_raised(tmp_path):
    d, broker = ud._daemon(tmp_path)
    d.job_dir("01A").mkdir(parents=True)
    broker.down = True

    def refuse(_req):
        raise ud.BrokerError("down")

    broker._request = refuse  # type: ignore[method-assign]
    assert watch.alert(d, "01A", "review_held", "x") is False
    broker._request = ud.FakeBroker._request.__get__(broker)  # type: ignore[method-assign]
    assert watch.alert(d, "01A", "review_held", "x") is True  # retried on the next occasion


# ---- resume markers -------------------------------------------------------------


def _resumable(tmp_path: Path, marker: dict):
    d, broker = ud._daemon(tmp_path)
    jd = d.job_dir("01A")
    jd.mkdir(parents=True)
    (jd / "envelope.json").write_text(json.dumps(ud._envelope("01A")))
    d._write_state("01A", state="held", attempts=1)
    (d.queue / ".resume-01A.json").write_text(json.dumps({"job_id": "01A", "reason": "checked", **marker}))
    assert resume_mod.take_requests(d) == ["01A"]
    return d


def test_a_release_writes_the_release_marker_for_exactly_the_next_attempt(tmp_path):
    d = _resumable(tmp_path, {"redo": [], "release_review": True})
    argv = resume_mod.start_run(d, "01A", ["medchron", "run"])
    marker = d.job_dir("01A") / resume_mod.RELEASE_MARKER
    assert json.loads(marker.read_text())["reason"] == "checked" and argv == ["medchron", "run"]
    assert not (d.job_dir("01A") / resume_mod.REDO_MARKER).exists()
    resume_mod.start_run(d, "01A", ["medchron", "run"])  # the attempt after: consumed
    assert not marker.exists()


def test_a_redo_of_the_review_writes_its_marker_and_never_reaches_argv(tmp_path):
    d = _resumable(tmp_path, {"redo": ["review"]})
    assert resume_mod.start_run(d, "01A", ["medchron", "run"]) == ["medchron", "run"]
    assert (d.job_dir("01A") / resume_mod.REDO_MARKER).exists()
    assert not (d.job_dir("01A") / resume_mod.RELEASE_MARKER).exists()


def test_a_redo_of_review_and_a_stage_passes_only_the_stage(tmp_path):
    d = _resumable(tmp_path, {"redo": ["condense", "review"]})
    assert resume_mod.start_run(d, "01A", ["medchron", "run"]) == ["medchron", "run", "--redo", "condense"]


def test_a_plain_resume_leaves_neither_marker(tmp_path):
    d = _resumable(tmp_path, {"redo": []})
    for name in (resume_mod.RELEASE_MARKER, resume_mod.REDO_MARKER):
        (d.job_dir("01A") / name).write_text("{}")  # left from an earlier attempt
    resume_mod.start_run(d, "01A", ["medchron", "run"])
    assert not (d.job_dir("01A") / resume_mod.RELEASE_MARKER).exists()
    assert not (d.job_dir("01A") / resume_mod.REDO_MARKER).exists()
