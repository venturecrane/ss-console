"""Incident replays on the lifecycle matrix's seat (``lifecycle_matrix.py``):
the real chronology lane, the real broker, an inline child. Each scene is the
host-level shape of a defect a client once met, written generically; each
asserts the ending the fix promises, and where a scene's fix is one line of
host code, a mutation shows the scene can fail.

The incidents (``git log origin/main --grep='#NNNN'``): #2823 a resume metered
against its own spend; #3108 a deploy stop became terminal; #3615 a held update
review had no way out; #3914 a redo that did not reach the stage it named;
#2901/#2905 a duplicate set filed; #3443 the firm heard nothing for 21 hours;
#3617 four emails for one update; #2818 a stdout banner ahead of the verdict.
And the five cells the lifecycle matrix was predicted to show red.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from lifecycle_matrix import (
    ALLOWANCE,
    AUDIT_HOLD,
    Life,
    _hand,
    _kept_units,
    _resume,
    build,
    delivered,
    ended,
    observe,
    problems,
    stopped,
    teardown,
)


def _scene(tmp_path: Path, mp: Any, body: Any) -> Any:
    life, server = build(tmp_path, mp)
    try:
        with server as port:
            life.run.build(port)
            return body(life)
    finally:
        teardown(life)


def _audit(life: Life, jid: str) -> list[tuple[str, dict[str, Any]]]:
    conn = sqlite3.connect(life.seat.audit_db)
    try:
        rows = conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid").fetchall()
    finally:
        conn.close()
    out = [(a, json.loads(m or "{}")) for a, m in rows]
    return [(a, m) for a, m in out if m.get("job_id") == jid]


def test_2823_a_resume_is_never_metered_against_its_own_spend(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> int:
        jid = life.submit()
        life.children.append(ended("failed", "map", "map: a stage failed"))
        life.tick()
        _resume(life, jid)
        life.children.append(
            lambda jd: (yaml.safe_load((jd / "job.yaml").read_text())["allowance_remaining_pages"], "")
        )
        remaining: list[int] = []
        real = life.child

        def spy(lane: Any, job_id: str, argv: list[str], **kw: Any) -> tuple[int, str]:
            remaining.append(
                yaml.safe_load((lane.job_dir(job_id) / "job.yaml").read_text())["allowance_remaining_pages"]
            )
            life.children[:] = [delivered()]
            return real(lane, job_id, argv, **kw)

        monkeypatch.setattr("jobs.child.run_child", spy)
        life.settle()
        return remaining[0]

    assert _scene(tmp_path, monkeypatch, body) == ALLOWANCE


def test_2823_can_fail_a_resume_metered_against_itself_is_caught(tmp_path, monkeypatch, real_lane_gate):
    """MUTATION: the allowance read without excluding the job's own row."""
    from jobs.broker_client import BrokerClient

    real = BrokerClient.allowance
    monkeypatch.setattr(BrokerClient, "allowance", lambda self, exclude_job_id=None: real(self))
    with __import__("pytest").raises(AssertionError):
        test_2823_a_resume_is_never_metered_against_its_own_spend(tmp_path, monkeypatch, real_lane_gate)


def test_3108_a_deploy_stop_leaves_the_job_for_the_next_boot(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> tuple[dict[str, Any], list[str]]:
        jid = life.submit()
        life.children.append(stopped(life, -15))
        life.tick()
        life.lane.stopping = lambda: False
        life.settle()
        return observe(life, jid), [a for a, _m in _audit(life, jid)]

    got, kinds = _scene(tmp_path, monkeypatch, body)
    assert got["state"] == "delivered" and "MEDCHRON_JOB_FAILED" not in kinds


def test_3615_a_held_update_review_has_a_way_out(tmp_path, monkeypatch, real_lane_gate):
    from workspace_broker.medchron_verbs import UPDATE_REVIEW_HOLD_PREFIX

    def body(life: Life) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        jid = life.submit()
        life.children.append(ended("held", "update_review", f"{UPDATE_REVIEW_HOLD_PREFIX} kept a finding"))
        life.tick()
        held = observe(life, jid)
        assert "medchron_job_resume" in held["way_out"], held
        _resume(life, jid)
        life.settle()
        return observe(life, jid), life.acts

    got, acts = _scene(tmp_path, monkeypatch, body)
    assert ("medchron_job_resume", "accepted") in acts and got["state"] == "delivered"


def test_3914_a_redo_reaches_the_stage_it_names(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> list[list[str]]:
        jid = life.submit()
        life.children.append(ended("failed", "update_select", "update_select: a stage failed"))
        life.tick()
        _resume(life, jid, redo=["update_select"])
        life.settle()
        return [row["argv"] for row in life.run.launches.rows]

    argvs = _scene(tmp_path, monkeypatch, body)
    assert "--redo" not in argvs[0]
    assert argvs[1][-2:] == ["--redo", "update_select"]


def test_2901_2905_an_upload_retried_after_a_short_read_back_files_one_set(tmp_path, monkeypatch, real_lane_gate):
    from jobs.requeue import UPLOAD_SHORT

    def body(life: Life) -> dict[str, Any]:
        jid = life.submit()
        life.children.append(ended("held", "upload", f"{UPLOAD_SHORT} (3 of 4 files)"))
        life.tick()
        life.settle()
        return observe(life, jid)

    got = _scene(tmp_path, monkeypatch, body)
    assert (got["state"], got["filed_sets"], got["firm_messages"]) == ("delivered", 1, 1)


def test_3443_every_hold_reaches_smd_and_the_firm_hears_once(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> tuple[dict[str, Any], list[str]]:
        jid = life.submit()
        life.children.append(ended("held", "audit", AUDIT_HOLD))
        life.tick()
        _resume(life, jid)
        life.children.append(ended("held", "review", "review_held: the pre-delivery review found a claim to check"))
        life.settle()
        _resume(life, jid, release_review=True)
        life.settle()
        pages = [m.get("code") for a, m in _audit(life, jid) if a == "DELIVERY_PROOF_FAILED"]
        return observe(life, jid), pages

    got, pages = _scene(tmp_path, monkeypatch, body)
    assert "gate_held" in pages and "review_held" in pages
    assert (got["state"], got["firm_messages"]) == ("delivered", 1)


def test_3617_an_update_gets_one_message(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> dict[str, Any]:
        jid = life.submit()
        env = life.lane.queue / f"{jid}.json"
        doc = json.loads(env.read_text(encoding="utf-8"))
        update = {**doc, "update": {"mode": "append"}}
        env.write_text(json.dumps(update), encoding="utf-8")
        from jobs import lane_proof

        key = lane_proof.lane_key("medchron", update)
        lane_proof.append_event(
            life.seat.run_dir, {"event": "proven", "key": key, "verify_id": "vfy_01KTJ0BX0000000000000000AA"}
        )
        life.children.append(delivered())
        life.settle()
        return observe(life, jid)

    got = _scene(tmp_path, monkeypatch, body)
    assert (got["state"], got["firm_messages"], got["filed_sets"]) == ("delivered", 1, 1)


def test_2818_a_banner_on_stdout_does_not_hide_the_verdict(tmp_path, monkeypatch, real_lane_gate):
    def body(life: Life) -> dict[str, Any]:
        jid = life.submit()
        make = delivered()
        life.children.append(lambda jd: (make(jd)[0], "a deprecation notice on stdout\n"))
        life.settle()
        return observe(life, jid)

    assert _scene(tmp_path, monkeypatch, body)["state"] == "delivered"


# -- the predicted red cells (lifecycle_matrix.py header) ------------------------------------------


def test_a_resume_of_a_wiped_hold_is_recorded_refused_and_the_job_can_be_resubmitted(
    tmp_path, monkeypatch, real_lane_gate
):
    """Predicted red: ``take_requests`` removed the marker and recorded nothing.
    The row was already closed failed by the wipe (paged); the refusal now
    leaves a ``resume_refused`` note, and a fresh submission is the way out."""

    def body(life: Life) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        jid = life.submit()
        life.children.append(ended("held", "audit", AUDIT_HOLD))
        life.tick()
        life.seat.now["t"] += (life.lane.held_wipe_hours + 1) * 3600
        life.tick()
        _resume(life, jid)
        life.tick()
        blocks = [m["run"] for _a, m in _audit(life, jid) if "run" in m]
        return observe(life, jid), blocks

    got, blocks = _scene(tmp_path, monkeypatch, body)
    assert got["state"] == "failed" and "medchron_job_submit" in got["way_out"]
    assert {"v": 1, "event": "resume_refused", "by": "smd", "code": "resume"} in blocks
    assert problems("scene", got) == []


def test_a_hand_delivery_whose_second_record_failed_can_be_run_again(tmp_path, monkeypatch, real_lane_gate):
    """Predicted red, refuted: the two records are not atomic, but the tool
    accepts its own half-finished write (``deliver_by_smd.plan``: a ``running``
    row the daemon still holds as ended), so running it again finishes it."""
    from jobs.broker_client import BrokerClient, BrokerError

    def body(life: Life) -> tuple[dict[str, Any], dict[str, Any]]:
        jid = life.submit()
        life.children.append(ended("held", "audit", AUDIT_HOLD))
        life.tick()
        real = BrokerClient.record

        def drop_delivered(self: Any, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
            if state == "delivered":
                raise BrokerError("broker socket: the second record was lost")
            return real(self, job_id, state, fields)

        monkeypatch.setattr(BrokerClient, "record", drop_delivered)
        try:
            _hand(life, jid)
        except BrokerError:
            pass
        half = life.client().status(jid) or {}
        monkeypatch.setattr(BrokerClient, "record", real)
        _hand(life, jid)
        life.settle()
        return half, observe(life, jid)

    half, got = _scene(tmp_path, monkeypatch, body)
    assert half["state"] == "running"
    assert (got["state"], got["firm_messages"]) == ("delivered", 1)
    assert got["run"]["event"] == "hand_delivery" and got["run"]["by"] == "smd"


def test_a_running_rows_status_names_its_client_kind_and_stage(tmp_path, monkeypatch, real_lane_gate):
    """2026-11-19: a running row read 0/0/0 for hours and the status read
    could not say whose job it was or how far it had got. The driver records
    each stage's start in its run state file; the lane's heartbeat reports
    the stage to the broker, and the status read names the client, the kind
    and the stage, never the expected finish. A stage note that arrives after
    the job held never moves the row back to running."""
    from jobs.medchron.state import RunState, state_path

    def body(life: Life) -> tuple[dict[str, Any], dict[str, Any]]:
        jid = life.submit()
        _kept_units(life, jid)
        seen: dict[str, Any] = {}

        def during() -> None:
            st = RunState.load_or_new(
                state_path(life.lane.job_dir(jid) / "data", life.lane.customer_slug, "alpha"),
                slug=life.lane.customer_slug,
                unit="alpha",
            )
            st.expected_finish = {"at": "2026-11-20T03:00:00Z"}
            st.start("compose", input_sha=None)
            life.lane.heartbeat(running=jid)
            seen.update(life.client()._request({"action": "medchron_job_status", "job_id": jid})["job"])
            st.start("review", input_sha=None)

        life.during.append(during)
        life.children.append(ended("held", "review", "review_held: the pre-delivery review found a claim to check"))
        life.tick()
        life.lane.heartbeat(running=jid)  # a late beat: "review" runs in the file, the row is held
        return seen, life.client()._request({"action": "medchron_job_status", "job_id": jid})["job"]

    during, after = _scene(tmp_path, monkeypatch, body)
    assert during["state"] == "running" and during["stage"] == "compose"
    assert during["clients"] == ["Alpha Example"] and during["kind"] == "run"
    assert "expected_finish" not in during
    assert after["state"] == "held" and after["stage"] == "compose"
