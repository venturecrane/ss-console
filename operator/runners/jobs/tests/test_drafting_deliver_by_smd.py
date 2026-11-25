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
from datetime import datetime, timezone
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
        #: Actions that fail once, as a broker timeout would (the next call goes through).
        self.fail_once: set[str] = set()

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        if req["action"] in self.fail_once:
            self.fail_once.discard(req["action"])
            raise BrokerError("broker socket: timed out")
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


#: The clock every --notify test reads: 2026-03-04 at 5 PM in the seat's zone
#: (America/Phoenix, UTC-7 all year).
NOW = datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc)
TODAY = "2026-03-04T23:10:00Z"  # 4:10 PM Phoenix, the same day
YESTERDAY = "2026-03-03T18:00:00Z"


class _MatterSeat:
    """The seat's Smokeball listing, read only: rows as ``seat.normalize_file`` returns them."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.read: list[str] = []

    def list_files(self, matter_id: str) -> list[dict]:
        self.read.append(matter_id)
        return list(self.rows)


def _filed(when: str = TODAY, record: dict = RECORD) -> list[dict]:
    return [
        {"id": f["file_id"], "name": f["name"], "size": f["bytes"], "modified": when, "deleted": False}
        for f in record["files"]
    ]


@pytest.fixture(autouse=True)
def _seat_zone(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TIMEZONE", "America/Phoenix")
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp_path / "no-requests.db"))


def _notify(rows: list[dict] | None = None) -> dict:
    seat = _MatterSeat(_filed() if rows is None else rows)
    return {"notify": True, "seat_factory": lambda: seat, "now": NOW}


@pytest.mark.parametrize("how", ["failed", "stopped"])
def test_an_ended_job_with_two_hand_filed_files_becomes_delivered(seat, how):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, how)
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))
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


def _audits(db: str, kind: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type=?", (kind,)).fetchone()[0]
    finally:
        conn.close()


def test_a_held_job_is_refused_until_withdrawn_and_no_failed_row_is_written(seat):
    """FALSIFIER for item 2: walking held -> failed wrote DRAFTING_JOB_FAILED,
    which the firm's portal reads as "Could not finish drafting a document"."""
    broker, _client, lane, db, _q = seat
    jid = _ended(seat, "held")
    before = _audits(db, "DRAFTING_JOB_FAILED")
    with pytest.raises(dbs.Refused, match="is held. Withdraw it first"):
        dbs.plan(lane, jid, _rec(), quiet=False)
    assert _row(broker, jid)["state"] == "held" and _audits(db, "DRAFTING_JOB_FAILED") == before


def test_the_proof_is_written_before_the_row_is_delivered(seat):
    """FALSIFIER for item 1: with the record first, a proof that times out left
    a delivered row the revise routing refuses (prior_unidentified)."""
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    client.fail_once.add("delivery_proof_record")
    with pytest.raises(BrokerError):
        dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))
    assert _row(broker, jid)["state"] == "failed" and _wakes(lane) == []
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))  # a re-run finishes it
    assert _row(broker, jid)["state"] == "delivered" and broker.drafting.ledger.proven_files(jid)
    assert _wakes(lane) == [jid]


def test_a_run_cut_short_after_the_row_moved_is_finished_by_a_re_run(seat, monkeypatch):
    """FALSIFIER for item 1: a row delivered whose daemon state never landed
    was refused, leaving no wake and no terminal state."""
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    real = lane._write_state

    def lost(job_id, **fields):
        raise OSError("disk full")

    monkeypatch.setattr(lane, "_write_state", lost)
    with pytest.raises(OSError):
        dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))
    monkeypatch.setattr(lane, "_write_state", real)
    assert _row(broker, jid)["state"] == "delivered" and lane._daemon_state(jid)["state"] == "stopped_by_smd"
    p = dbs.plan(lane, jid, _rec(), **_notify())
    assert p["records"] == []  # the ledger is not moved again
    dbs.apply(lane, p)
    assert lane._daemon_state(jid)["state"] == "delivered" and _wakes(lane) == [jid]
    other = _rec()
    other["folder_id"] = "folder-elsewhere"
    with pytest.raises(dbs.Refused, match="only a failed or withdrawn job"):
        dbs.plan(lane, jid, other, quiet=False)  # and never a delivered row with other files


def test_another_live_job_for_the_same_request_is_refused_before_any_write(seat):
    """FALSIFIER for item 3: the ledger's unique (request_ref, class) index
    refused the failed -> running move after the proof had landed."""
    broker, client, lane, db, _q = seat
    jid = _ended(seat, "stopped")
    again = broker.handle(
        {"action": "drafting_job_submit", "envelope": _envelope()}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID
    )
    assert again["accepted"]
    with pytest.raises(dbs.Refused, match=f"job {again['job_id']} is live for the same request email"):
        dbs.plan(lane, jid, _rec(), quiet=False)
    assert _row(broker, jid)["state"] == "failed" and _audits(db, "DELIVERY_PROVEN") == 0


def test_a_write_that_fails_part_way_exits_3_not_a_traceback(seat, monkeypatch, capsys, tmp_path):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: None)
    monkeypatch.setattr(drafting_lane, "build_lane", lambda _d: lane)
    monkeypatch.setattr(dbs.hand_delivery, "daemon_env", lambda _names: {})
    assert dbs.main([jid, str(rec), "--dry-run"]) == 0
    out = capsys.readouterr().out
    digest = json.loads(out[: out.rindex("}") + 1])["digest"]
    client.fail_once.add("drafting_job_record")
    assert dbs.main([jid, str(rec), "--confirm", digest]) == 3
    assert "FAILED PARTWAY" in capsys.readouterr().err


def test_the_daemon_env_is_read_by_name_only(tmp_path):
    proc = tmp_path / "proc"
    (proc / "7").mkdir(parents=True)
    (proc / "7" / "cmdline").write_bytes(b"/usr/bin/python3\0-c\0pass\0")
    (proc / "7" / "environ").write_bytes(b"CUSTOMER_SLUG=wrong\0")
    (proc / "42").mkdir()
    (proc / "42" / "cmdline").write_bytes(b"/opt/jobs/.venv/bin/python\0-m\0jobs.daemon\0")
    (proc / "42" / "environ").write_bytes(b"CUSTOMER_SLUG=example\0SECRET_KEY=s3cr3t\0SMD_JOBS_RUN_DIR=/run/x\0")
    got = dbs.hand_delivery.daemon_env(["CUSTOMER_SLUG", "SMD_JOBS_RUN_DIR"], proc=proc)
    assert got == {"CUSTOMER_SLUG": "example", "SMD_JOBS_RUN_DIR": "/run/x"}
    assert dbs.hand_delivery.daemon_env(["CUSTOMER_SLUG"], proc=tmp_path / "none") == {}


def test_the_chronology_keeps_its_own_order(seat):
    """The shared write order is per lane: the chronology (live) still records
    the ledger before its proof; only the drafting plan sets proof_first."""
    calls: list[str] = []

    class _Lane:
        LANE = "medchron"

        class broker:
            @staticmethod
            def record(job_id, state, fields):
                calls.append(f"record:{state}")

            @staticmethod
            def _request(req):
                calls.append(req["action"])

        def clock(self):
            return 1.0

        def _write_state(self, job_id, **fields):
            calls.append("state")

    plan = {"job_id": "J", "records": [("running", {}), ("delivered", {})], "proof": {"action": "proof"}, "state": {}}
    dbs.hand_delivery.apply(_Lane(), plan)
    assert calls == ["record:running", "record:delivered", "proof", "state"]
    calls.clear()
    dbs.hand_delivery.apply(_Lane(), {**plan, "proof_first": True})
    assert calls == ["proof", "record:running", "record:delivered", "state"]


def test_quiet_queues_no_reply(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=True))
    assert _row(broker, jid)["state"] == "delivered"
    assert _wakes(lane) == []
    st = lane._daemon_state(jid)
    assert st["state"] == "delivered" and st["wake"]["pending"] is False and st["wake"]["quiet"]


def test_a_second_run_is_refused_and_writes_nothing_twice(seat):
    broker, client, lane, db, _q = seat
    jid = _ended(seat, "stopped")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))
    before = len(client.requests)
    with pytest.raises(dbs.Refused, match="only a failed or withdrawn job"):
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
    jid = _ended(seat, "stopped")
    rec = _rec()
    change(rec)
    with pytest.raises(dbs.Refused, match=match):
        dbs.plan(lane, jid, rec, quiet=False)
    assert _row(broker, jid)["state"] == "failed"


def test_after_it_a_two_draft_hand_delivery_is_revised_in_every_document(seat):
    """A hand delivery of two drafts is recorded as delivered, and a change
    request in its thread revises BOTH filed documents by id (``revise.units``,
    revise/set_run.py), never the first only and never a fresh draft.
    FALSIFIER: keep the one-draft guard for responses and this queues a draft."""
    broker, _client, lane, _db, _queue = seat
    jid = _ended(seat, "stopped")
    change = validate_envelope(_envelope(REPLY, "Please apply the new rules to the responses."))
    # Before it: held for SMD's hand, never a fresh draft of the whole file (2026-11-24).
    assert broker.drafting._route(change, "", GATEWAY_PID).code == "prior_filed_by_smd"

    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=True))

    rev = broker.drafting._route(change, "", GATEWAY_PID)
    assert [u["file_id"] for u in rev["units"]] == ["file-set-one", "file-set-two"]
    # "Interrogatories" alone names no set type: the file's own headings decide in the runner.
    assert [u["set_type"] for u in rev["units"]] == ["", "requests_for_production"]
    out = broker.handle(
        {"action": "drafting_job_submit", "envelope": _envelope(REPLY, "Please apply the new rules to the responses.")},
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == jid


def test_dry_run_prints_the_digest_and_the_live_run_must_repeat_it(seat, monkeypatch, capsys, tmp_path):
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: None)
    monkeypatch.setattr(drafting_lane, "build_lane", lambda _d: lane)
    assert dbs.main([jid, str(rec), "--dry-run"]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out[: out.rindex("}") + 1])
    assert plan["dry_run"] is True and plan["records"][-1][0] == "delivered"
    assert _row(broker, jid)["state"] == "failed" and _wakes(lane) == []
    assert dbs.main([jid, str(rec)]) == 2  # no digest
    assert dbs.main([jid, str(rec), "--confirm", "0" * 16]) == 2  # the wrong one
    assert _row(broker, jid)["state"] == "failed"
    assert dbs.main([jid, str(rec), "--confirm", plan["digest"]]) == 0
    # Recorded, and by default nobody is emailed (2026-11-24).
    assert _row(broker, jid)["state"] == "delivered" and _wakes(lane) == []
    assert "REFUSED" in capsys.readouterr().err


# -- recording never emails unless asked, and asking is checked (2026-11-24) ---------


def _task(lane, jid) -> str:
    return lane._daemon_state(jid)["wake"]["task"]


def test_the_default_records_the_delivery_with_no_wake(seat):
    """FALSIFIER: the default armed the DELIVER wake, and at 6:02 PM the Operator
    told an attorney his responses were filed; they were filed at 8:40 PM."""
    broker, client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    p = dbs.plan(lane, jid, _rec())
    assert p["email"].startswith("no email will be sent")
    dbs.apply(lane, p)
    assert _row(broker, jid)["state"] == "delivered" and _wakes(lane) == []
    wake = lane._daemon_state(jid)["wake"]
    assert wake["pending"] is False and wake["quiet"] == dbs.QUIET


def test_quiet_is_still_accepted_and_still_no_wake(seat, monkeypatch, capsys, tmp_path):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: None)
    monkeypatch.setattr(drafting_lane, "build_lane", lambda _d: lane)
    monkeypatch.setattr(dbs.hand_delivery, "daemon_env", lambda _names: {})
    assert dbs.main([jid, str(rec), "--quiet", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "EMAIL: no email will be sent" in out
    digest = json.loads(out[: out.rindex("}") + 1])["digest"]
    assert dbs.main([jid, str(rec), "--quiet", "--confirm", digest]) == 0
    assert _row(broker, jid)["state"] == "delivered" and _wakes(lane) == []


def test_todays_case_no_notify_and_files_not_yet_filed_arms_no_wake(seat):
    """The replay: recorded before the hand draft reached the matter, without
    --notify. The matter is never read, and nothing wakes."""
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    empty = _MatterSeat([])
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), seat_factory=lambda: empty, now=NOW))
    assert empty.read == [] and _wakes(lane) == []


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        (lambda: _filed()[:1], "file-set-two .* is not on matter"),
        (lambda: [], "file-set-one .* is not on matter"),
        (lambda: [{**r, "deleted": True} for r in _filed()], "is not on matter"),
        (lambda: [{**r, "size": 7} for r in _filed()], "7 bytes on the matter"),
        (lambda: _filed(YESTERDAY), "not today in the seat's timezone"),
    ],
)
def test_notify_refuses_a_missing_or_old_file_and_writes_nothing(seat, rows, match):
    broker, client, lane, db, _q = seat
    jid = _ended(seat, "stopped")
    before = len(client.requests)
    with pytest.raises(dbs.Refused, match=match):
        dbs.plan(lane, jid, _rec(), **_notify(rows()))
    assert _row(broker, jid)["state"] == "failed" and _wakes(lane) == []
    assert _audits(db, "DELIVERY_PROVEN") == 0
    assert all(r["action"] in ("drafting_job_status", "delivery_reply_status") for r in client.requests[before:])


def test_notify_takes_an_old_file_only_with_a_recent_filed_at(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    old = {**_rec(), "filed_at": "2026-03-04T03:00:00Z"}  # 21 hours before NOW
    dbs.apply(lane, dbs.plan(lane, jid, old, **_notify(_filed(YESTERDAY))))
    assert _wakes(lane) == [jid]


def test_notify_refuses_an_old_file_whose_filed_at_is_older_than_a_day(seat):
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    with pytest.raises(dbs.Refused, match="no filed_at within the last 24 hours"):
        dbs.plan(lane, jid, {**_rec(), "filed_at": "2026-03-03T23:00:00Z"}, **_notify(_filed(YESTERDAY)))
    assert _wakes(lane) == []


@pytest.mark.parametrize("hermes", [None, "Not/AZone"])
def test_with_no_timezone_in_the_env_the_authored_zone_dates_today(seat, monkeypatch, tmp_path, hermes):
    """FALSIFIER (review of 61d331b1c): the jobs daemon runs with neither
    HERMES_TIMEZONE nor TZ (templates/entrypoint.sh), so "filed today" never
    passed on a seat. The live customer.yaml's business_hours.timezone answers;
    an invalid HERMES_TIMEZONE falls through to it."""
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    monkeypatch.delenv("TZ", raising=False)
    if hermes is None:
        monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    else:
        monkeypatch.setenv("HERMES_TIMEZONE", hermes)
    cfg = tmp_path / "customer.yaml"
    cfg.write_text("business_hours:\n  timezone: America/Phoenix\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    # 2026-03-05T05:30Z is 10:30 PM on the 4th in Phoenix: the file's day, though UTC has turned over.
    late = datetime(2026, 3, 5, 5, 30, tzinfo=timezone.utc)
    seat_rows = _MatterSeat(_filed())
    p = dbs.plan(lane, jid, _rec(), notify=True, seat_factory=lambda: seat_rows, now=late)
    assert p["email"].startswith("the Operator WILL email the attorney")
    cfg.write_text("business_hours: {}\n")  # nothing authored: no zone, so no "today"
    with pytest.raises(dbs.Refused, match="not today in the seat's timezone"):
        dbs.plan(lane, jid, _rec(), notify=True, seat_factory=lambda: seat_rows, now=late)


def test_notify_with_no_smokeball_read_is_refused_never_faked(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")

    def unavailable():
        raise KeyError("SMOKEBALL_CLIENT_ID")

    with pytest.raises(dbs.Refused, match="none is available here"):
        dbs.plan(lane, jid, _rec(), notify=True, seat_factory=unavailable, now=NOW)
    assert _row(broker, jid)["state"] == "failed" and _wakes(lane) == []


def test_notify_with_a_valid_record_arms_the_wake_and_names_the_email(seat):
    broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    kw = _notify()
    p = dbs.plan(lane, jid, _rec(), **kw)
    assert p["email"].startswith("the Operator WILL email the attorney: ")
    assert [c["file_id"] for c in p["checked"]] == ["file-set-one", "file-set-two"]
    assert kw["seat_factory"]().read == [MATTER]  # read by id on the job's own matter
    dbs.apply(lane, p)
    assert _wakes(lane) == [jid] and lane._daemon_state(jid)["wake"]["pending"] is True


def test_the_dry_run_names_the_subject_the_operator_will_send(seat, tmp_path, monkeypatch):
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    db = tmp_path / "requests.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE requests (vendor_message_id TEXT PRIMARY KEY, internet_message_id TEXT, received_at REAL, "
        "sender TEXT, subject TEXT)"
    )
    conn.execute("INSERT INTO requests VALUES ('v-1', ?, 1.0, ?, 'Discovery responses')", (FIRST, ADMIN))
    conn.commit()
    conn.close()
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(db))
    p = dbs.plan(lane, jid, _rec(), **_notify())
    assert p["email"] == "the Operator WILL email the attorney: Re: Discovery responses"


def test_a_hand_delivery_wake_never_licenses_a_firm_rules_claim(seat):
    """FALSIFIER: the delivery reply said the responses were "checked against
    all of your firm rules"; no check ran on a hand-filed draft."""
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), **_notify()))
    task = _task(lane, jid)
    assert "Delivered by: SMD, by hand." in task and "Firm rules check: not run." in task
    assert "checked against all of your firm rules" not in task.lower()


def test_the_wake_says_a_rules_check_ran_only_when_the_record_says_so(seat):
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    with pytest.raises(dbs.Refused, match="firm_rules_checked must be true or false"):
        dbs.plan(lane, jid, {**_rec(), "firm_rules_checked": "yes"})
    dbs.apply(lane, dbs.plan(lane, jid, {**_rec(), "firm_rules_checked": True}))
    assert "Firm rules check: run." in _task(lane, jid)


def test_a_lane_delivery_wake_carries_no_hand_lines(seat):
    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "stopped")
    task = lane._compose_wake(jid, "delivered", {"cents": 1, "folder_id": "f", "files": []})
    assert "Delivered by" not in task and "Firm rules check" not in task
