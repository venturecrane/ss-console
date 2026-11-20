"""`jobs.drafting.deliver_by_smd`: a document SMD drafted and filed by hand becomes the
drafting job's delivery, so the attorney's next "please change X" in that thread
revises it instead of queuing a fresh draft (2026-11-19).

Driven against the REAL broker: the drafting verbs and ledger, and the real
``delivery_proof_record`` verb writing the ``DELIVERY_PROVEN`` row. The revise
routing is the broker's own ``DraftingVerbs._route``, called read-only. Every
name, id and address here is invented.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.drafting_ledger import DraftingLedger, validate_envelope
from workspace_broker.drafting_verbs import DraftingVerbs
from workspace_broker.server import Broker

from jobs import daemon as daemon_mod, drafting_lane
from jobs.broker_client import BrokerError
from jobs.drafting import deliver_by_smd as dbs
from jobs.drafting_lane import DraftingBroker, DraftingLane

GATEWAY_PID = 4242
AGENT_UID = 10000
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
ADMIN = "admin@firm.example"
FIRST = "<first@mail.firm.example>"
REPLY = "<reply@mail.firm.example>"
THREADS = {FIRST: "conv-1", REPLY: "conv-1"}
KLASS = "discovery_response"
YAML = """
scope:
  admins:
    - admin@firm.example
personas:
  - slug: operator
    skills:
      - name: document-drafter
        enabled: true
        settings:
          drafting_allowance_per_cycle: 25
          enabled_classes: 'mediation_brief, discovery_set, discovery_response, memo, depo_outline'
"""
#: Two hand-filed documents: a discovery request yields one per set.
RECORD = {
    "folder_id": "folder-0101",
    "files": [
        {
            "file_id": "file-set-one",
            "name": "Responses to Interrogatories Set One.docx",
            "bytes": 41000,
            "sha256": "a" * 64,
        },
        {
            "file_id": "file-set-two",
            "name": "Responses to Requests for Production Set One.docx",
            "bytes": 38000,
            "sha256": "b" * 64,
            "role": "draft",
        },
    ],
}


def _envelope(ref: str = FIRST, text: str = "Please draft our responses.") -> dict:
    return {
        "matter": {"id": MATTER, "number": "900301"},
        "file_to": None,
        "requested_by": ADMIN,
        "request_ref": ref,
        "request_text": text,
        "document_class": KLASS,
    }


class _Client:
    """The runner's broker client, over the real broker in-process (uid 0)."""

    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.replies: list[dict] = []
        self.requests: list[dict] = []

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        if req["action"] == "delivery_reply_status":
            return {"ok": True, "replies": list(self.replies)}
        try:
            resp = self.broker.handle(dict(req), peer_pid=1, peer_uid=0)
        except Exception as exc:  # the socket turns every exception into a refusal
            raise BrokerError(str(exc)) from exc
        if resp.get("ok") is not True:
            raise BrokerError(f"broker refused: {resp}")
        return resp


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(YAML)
    queue = tmp_path / "broker-drafting-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid, broker.agent_uid, broker.ledger = GATEWAY_PID, AGENT_UID, writer
    broker.drafting = DraftingVerbs(
        DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, thread_of=THREADS.get
    )
    client = _Client(broker)
    run_dir = tmp_path / "run"
    (run_dir / "drafting-queue").mkdir(parents=True)
    lane = DraftingLane(
        run_dir=run_dir,
        broker=DraftingBroker(client),
        runner_cmd=[os.environ.get("PYTHON", "python3"), "-c", "raise SystemExit(9)"],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=run_dir / "drafting-queue",
        inputs_dir=str(tmp_path / "no-inputs"),
    )
    return broker, client, lane, db, queue


def _ended(seat, how: str) -> str:
    """A drafting job submitted the way the gateway submits one, claimed, and
    ended ``how``: held, failed, or stopped by SMD (``jobs.stop``)."""
    broker, _client, lane, _db, queue = seat
    out = broker.handle(
        {"action": "drafting_job_submit", "envelope": _envelope()}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID
    )
    jid = out["job_id"]
    jd = lane.job_dir(jid)
    jd.mkdir(parents=True)
    (queue / f"{jid}.json").replace(jd / "envelope.json")  # the claim moves it into the job dir

    def record(state, fields):
        broker.handle(
            {"action": "drafting_job_record", "job_id": jid, "state": state, "fields": fields}, peer_pid=1, peer_uid=0
        )

    if how == "running":
        record("running", {})
        (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": "running", "attempts": 1}))
        return jid
    record("running", {})
    if how == "held":
        record("held", {"cents": 120, "reason": "review_held: two findings"})
        st = {"claimed": True, "state": "held", "attempts": 1, "finished_at": 9.0}
        st["wake"] = {"pending": False, "attempts": 0, "task": "Outcome: held.", "sent_at": 10.0}
    else:
        reason = "stopped_by_smd: by staff: answered by hand" if how == "stopped" else "stage_unfinished: compose"
        record("failed", {"cents": 120, "reason": reason})
        st = {"claimed": True, "state": "stopped_by_smd" if how == "stopped" else "failed", "attempts": 1}
    (jd / "daemon.json").write_text(json.dumps(st))
    return jid


def _rec() -> dict:
    return json.loads(json.dumps(RECORD))


def _row(broker, jid) -> dict:
    return broker.drafting.ledger.read(jid)


def _wakes(lane) -> list[str]:
    return lane._wakes_pending()


@pytest.mark.parametrize("how", ["held", "failed", "stopped"])
def test_an_ended_job_with_two_hand_filed_files_becomes_delivered(seat, how):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, how)
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=False))
    row = _row(broker, jid)
    assert row["state"] == "delivered" and row["folder_id"] == "folder-0101"
    assert row["reason"] is None  # the hold's or the stop's reason never reaches the DELIVER turn
    files = json.loads(row["delivery_json"])["files"]
    assert [(f["name"], f["size"], f["role"]) for f in files] == [
        ("Responses to Interrogatories Set One.docx", 41000, "draft"),
        ("Responses to Requests for Production Set One.docx", 38000, "draft"),
    ]
    proven = broker.drafting.ledger.proven_files(jid)
    assert [p["file_id"] for p in proven] == ["file-set-one", "file-set-two"]
    st = lane._daemon_state(jid)
    assert st["state"] == "delivered" and st["delivered_by"] == "smd"
    assert _wakes(lane) == [jid]  # the delivered reply is queued once
    task = st["wake"]["task"]
    assert task.startswith(f"Run the document-drafter skill's DELIVER mode for drafting job {jid}.")
    assert "Outcome: delivered." in task and "Folder id: folder-0101." in task
    assert "draft (41000 bytes); draft (38000 bytes)" in task
    assert "Interrogatories" not in task  # never a file name on the unfenced wake


def test_quiet_queues_no_reply(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "held")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=True))
    assert _row(broker, jid)["state"] == "delivered"
    assert _wakes(lane) == []
    st = lane._daemon_state(jid)
    assert st["state"] == "delivered" and st["wake"]["pending"] is False and st["wake"]["quiet"]


def test_a_second_run_is_refused_and_writes_nothing_twice(seat):
    broker, client, lane, db, _q = seat
    jid = _ended(seat, "held")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=False))
    before = len(client.requests)
    with pytest.raises(dbs.Refused, match="only a held, failed or stopped job"):
        dbs.plan(lane, jid, _rec(), quiet=False)
    assert all(r["action"] == "drafting_job_status" for r in client.requests[before:])

    conn = sqlite3.connect(db)
    proofs = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='DELIVERY_PROVEN'").fetchone()[0]
    conn.close()
    assert proofs == 1 and _wakes(lane) == [jid]


def test_a_running_job_is_refused(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "running")
    with pytest.raises(dbs.Refused, match="is running"):
        dbs.plan(lane, jid, _rec(), quiet=False)
    assert _row(broker, jid)["state"] == "running"


def test_a_queued_job_is_refused(seat):
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "failed")
    (lane.queue / f"{jid}.json").write_text("{}")
    with pytest.raises(dbs.Refused, match="still queued"):
        dbs.plan(lane, jid, _rec(), quiet=False)


def test_a_delivered_reply_already_claimed_is_refused(seat):
    _broker, client, lane, _db, _q = seat
    jid = _ended(seat, "failed")
    client.replies = [{"key": f"drafting_job:{jid}:2:delivered"}]
    with pytest.raises(dbs.Refused, match="second send"):
        dbs.plan(lane, jid, _rec(), quiet=False)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda r: r.pop("folder_id"), "folder_id"),
        (lambda r: r.pop("files"), "files"),
        (lambda r: r["files"][1].pop("sha256"), "sha256"),
        (lambda r: r["files"][0].update(sha256="A" * 64), "sha256"),
        (lambda r: r["files"][0].pop("bytes"), "size in bytes"),
        (lambda r: r["files"][1].pop("file_id"), "file_id"),
        (lambda r: r["files"][0].pop("name"), "name"),
        (lambda r: r["files"][0].update(role="exhibit"), "role"),
        (lambda r: [f.update(role="attorney_notes") for f in r["files"]], "at least one filed draft"),
        (lambda r: r["files"][1].update(file_id="file-set-one"), "twice"),
    ],
)
def test_an_incomplete_file_triple_is_refused(seat, change, match):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "held")
    rec = _rec()
    change(rec)
    with pytest.raises(dbs.Refused, match=match):
        dbs.plan(lane, jid, rec, quiet=False)
    assert _row(broker, jid)["state"] == "held"


def test_after_it_the_revise_routing_finds_the_hand_delivered_draft(seat):
    """The winning line. Before: the broker's own routing finds no delivered
    draft of the class on the matter, so the change request would be a fresh
    draft. After: it revises the hand-filed files in place, by id."""
    broker, _client, lane, _db, queue = seat
    jid = _ended(seat, "stopped")
    change = validate_envelope(_envelope(REPLY, "Please change the answer to number four."))
    assert broker.drafting._route(change, "", GATEWAY_PID) is None

    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=True))

    route = broker.drafting._route(change, "", GATEWAY_PID)
    assert route == {
        "prior_job_id": jid,
        "folder_id": "folder-0101",
        "files": [
            {
                "role": "draft",
                "file_id": "file-set-one",
                "name": "Responses to Interrogatories Set One.docx",
                "size": 41000,
                "sha256": "a" * 64,
            },
            {
                "role": "draft",
                "file_id": "file-set-two",
                "name": "Responses to Requests for Production Set One.docx",
                "size": 38000,
                "sha256": "b" * 64,
            },
        ],
    }
    out = broker.handle(
        {"action": "drafting_job_submit", "envelope": _envelope(REPLY, "Please change the answer to number four.")},
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == jid
    assert json.loads((queue / f"{out['job_id']}.json").read_text())["revise"] == route


def test_dry_run_prints_the_digest_and_the_live_run_must_repeat_it(seat, monkeypatch, capsys, tmp_path):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "held")
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: None)
    monkeypatch.setattr(drafting_lane, "build_lane", lambda _d: lane)
    assert dbs.main([jid, str(rec), "--dry-run"]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out[: out.rindex("}") + 1])
    assert plan["dry_run"] is True and plan["records"][-1][0] == "delivered"
    assert _row(broker, jid)["state"] == "held" and _wakes(lane) == []
    assert dbs.main([jid, str(rec)]) == 2  # no digest
    assert dbs.main([jid, str(rec), "--confirm", "0" * 16]) == 2  # the wrong one
    assert _row(broker, jid)["state"] == "held"
    assert dbs.main([jid, str(rec), "--confirm", plan["digest"]]) == 0
    assert _row(broker, jid)["state"] == "delivered" and _wakes(lane) == [jid]
    assert "REFUSED" in capsys.readouterr().err
