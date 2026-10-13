"""Proven delivery, the broker's half (``delivery_proof.py``): the alert row the
overlay heartbeat pages on, the evidence row a delivered job rests on, the
runner daemon's read of a job's bound replies, the bound reply's own Sent
Items proof, and the resume verbs' release / redo of a review hold.

Every test is a falsifier (run red with its guard removed, then green): the
verbs are root only, the vocabularies are closed, the evidence row carries no
file name, and a reply the broker sent but could not find pages SMD.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_reply_binding as rb
from workspace_broker import bound_replies, delivery_proof
from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.msgraph_ops import MsGraphOps

ROOT = 0
SHA = "d" * 64


def _broker(tmp_path: Path):
    return rb._broker(tmp_path, rb.FakeMailbox())


def _root(broker, action: str, **req) -> dict:
    return broker.handle({"action": action, **req}, peer_pid=999, peer_uid=ROOT)


# ---- delivery_proof_alert --------------------------------------------------------


def test_an_alert_writes_one_row_with_its_detail_clipped(tmp_path):
    broker = _broker(tmp_path)
    out = _root(
        broker, "delivery_proof_alert", lane="demand", job_id="01J", attempt=2, code="review_held", detail="x" * 900
    )
    assert out["ok"] is True and out["audit_id"]
    rows = rb._rows(broker, "DELIVERY_PROOF_FAILED")
    assert rows == [
        {
            "lane": "demand",
            "job_id": "01J",
            "attempt": 2,
            "code": "review_held",
            "detail": "x" * delivery_proof.DETAIL_MAX,
        }
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"lane": "billing", "code": "review_held"},
        {"lane": "demand", "code": "everything_is_fine"},
        {"lane": "demand", "code": "review_held", "attempt": 0},
        {"lane": "demand", "code": "review_held", "attempt": True},
        {"lane": "demand", "code": "review_held", "job_id": "../etc"},
    ],
)
def test_an_alert_outside_the_closed_vocabulary_is_refused(tmp_path, bad):
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        _root(broker, "delivery_proof_alert", **{"job_id": "01J", **bad})
    assert rb._rows(broker, "DELIVERY_PROOF_FAILED") == []


@pytest.mark.parametrize("action", delivery_proof.VERBS)
def test_the_proof_verbs_are_root_only(tmp_path, action):
    broker = _broker(tmp_path)
    req = {"action": action, "lane": "demand", "job_id": "01J", "code": "review_held", "kind": "demand_job"}
    with pytest.raises(PermissionError):
        broker.handle(req, peer_pid=999, peer_uid=rb.AGENT_UID)
    with pytest.raises(PermissionError):
        broker.handle(req, peer_pid=rb.GATEWAY_PID, peer_uid=rb.AGENT_UID)
    assert rb._rows(broker, "DELIVERY_PROOF_FAILED") == [] and rb._rows(broker, "DELIVERY_PROVEN") == []


def test_the_two_code_lists_are_one_vocabulary():
    """The daemon's codes and the broker's are two copies of one wire
    vocabulary (the runner package is not importable here); they must agree."""
    gate = Path(__file__).resolve().parents[2] / "runners/medchron/medchron/proof/gate.py"
    text = gate.read_text(encoding="utf-8")
    block = text.split("CODES = frozenset(", 1)[1].split("\n)\n", 1)[0]
    assert set(re.findall(r'"([a-z_]+)"', block)) == set(delivery_proof.CODES)


# ---- delivery_proof_record --------------------------------------------------------


def test_a_proven_delivery_row_carries_ids_sizes_and_hashes_never_names(tmp_path):
    broker = _broker(tmp_path)
    files = [
        {"name": "Demand - Alpha Example.docx", "file_id": "up-1", "size": 41000, "sha256": SHA},
        {"name": "Gap Audit - Alpha Example.docx", "file_id": "up-2", "size": "9000", "sha256": "not-a-hash"},
    ]
    _root(
        broker,
        "delivery_proof_record",
        lane="demand",
        job_id="01J",
        attempt=1,
        review="passed",
        review_cents=42,
        review_model="example-review-model",
        files=files,
    )
    (row,) = rb._rows(broker, "DELIVERY_PROVEN")
    assert row["review_cents"] == 42 and row["review"] == "passed" and row["review_model"] == "example-review-model"
    assert row["files"] == [
        {"file_id": "up-1", "size": 41000, "sha256": SHA},
        {"file_id": "up-2", "size": 9000, "sha256": ""},
    ]
    raw = json.dumps(row)
    assert "Alpha" not in raw and "name" not in raw


@pytest.mark.parametrize(
    "bad",
    [{"review": "held"}, {"review_cents": -1}, {"review_cents": 1.5}, {"lane": "billing"}, {"attempt": 0}],
)
def test_a_proof_record_with_a_bad_field_is_refused(tmp_path, bad):
    broker = _broker(tmp_path)
    req = {"lane": "demand", "job_id": "01J", "review": "passed", "review_cents": 0, "files": [], **bad}
    with pytest.raises(ValueError):
        _root(broker, "delivery_proof_record", **req)
    assert rb._rows(broker, "DELIVERY_PROVEN") == []


# ---- delivery_reply_status and the located column ----------------------------------


def _claim(db: str, key: str) -> None:
    assert bound_replies.claim(db, key, "G", "<i@x>", "sess")


def test_reply_status_reads_only_that_jobs_rows(tmp_path):
    broker = _broker(tmp_path)
    db = broker.audit_db_path
    _claim(db, "demand_job:01J:1:delivered")
    bound_replies.settle(db, "demand_job:01J:1:delivered", "sent", True)
    _claim(db, "demand_job:01JX:1:delivered")  # another job whose id starts the same
    _claim(db, "demandXjob:01J:1:delivered")  # a LIKE wildcard would match this
    _claim(db, "drafting_job:01J:1:delivered")  # another lane
    _claim(db, "message:<01J@x>")
    out = _root(broker, "delivery_reply_status", kind="demand_job", job_id="01J")
    assert [(r["key"], r["outcome"], r["located"]) for r in out["replies"]] == [
        ("demand_job:01J:1:delivered", "sent", True)
    ]


def test_reply_status_names_the_key_the_reply_is_owed_under(tmp_path):
    """The daemon's own attempt counter is not the reply key's: the broker
    names the one ``reply_binding`` claims, from the job's ledger row."""
    job = rb._job(tmp_path, ["failed"])
    ledger = DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q")
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    broker = _broker(tmp_path)
    out = _root(broker, "delivery_reply_status", kind="demand_job", job_id=job)
    assert out["expected_key"] == f"demand_job:{job}:2:delivered"
    with pytest.raises(ValueError):
        _root(broker, "delivery_reply_status", kind="message", job_id=job)


def test_reply_status_names_no_key_for_a_job_owed_no_reply(tmp_path):
    job = rb._job(tmp_path, ["running"])
    out = _root(_broker(tmp_path), "delivery_reply_status", kind="demand_job", job_id=job)
    assert out["expected_key"] == "" and out["replies"] == []


def test_an_existing_bound_replies_table_gains_the_located_column(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(bound_replies.CREATE_SQL)
    conn.execute(
        "INSERT INTO bound_replies (binding_key, graph_message_id, internet_message_id, claimed_at, outcome) "
        "VALUES ('demand_job:01J:1:delivered', 'G', '<i@x>', '2026-10-01T00:00:00Z', 'sent')"
    )
    conn.commit()
    conn.close()
    assert [r["located"] for r in delivery_proof.reply_rows(db, "demand_job", "01J")] == [None]
    _claim(db, "demand_job:01J:2:delivered")
    bound_replies.settle(db, "demand_job:01J:2:delivered", "sent", False)
    assert [r["located"] for r in delivery_proof.reply_rows(db, "demand_job", "01J")] == [None, False]


# ---- the bound reply's own Sent Items proof -----------------------------------------


@pytest.mark.parametrize(
    ("lookup", "located", "alerts"),
    [
        ({"lookup": "ok", "vendor_message_id": "<s@x>"}, True, []),
        ({"lookup": "ok: matched a message on a different conversation"}, True, []),
        ({"lookup": "failed: not on any of the newest 25 sent items"}, False, ["reply_not_in_sent_items"]),
    ],
)
def test_a_job_reply_settles_located_and_an_unlocated_one_pages(tmp_path, monkeypatch, lookup, located, alerts):
    job = rb._job(tmp_path, ["running", "delivered"])
    monkeypatch.setattr(MsGraphOps, "_locate_sent", lambda self, _audit_ref, conversation_id="": dict(lookup))
    broker = _broker(tmp_path)
    rb._send(broker, {"kind": "demand_job", "job_id": job})
    rows = delivery_proof.reply_rows(broker.audit_db_path, "demand_job", job)
    assert [(r["outcome"], r["located"]) for r in rows] == [("sent", located)]
    failed = rb._rows(broker, "DELIVERY_PROOF_FAILED")
    assert [f["code"] for f in failed] == alerts
    if alerts:
        assert failed[0]["lane"] == "demand" and failed[0]["job_id"] == job and failed[0]["attempt"] == 1


def test_a_bare_message_reply_that_is_not_located_pages_nothing(tmp_path, monkeypatch):
    """Only a job's reply is a delivery; an ordinary bound reply is not."""
    monkeypatch.setattr(
        MsGraphOps, "_locate_sent", lambda self, _audit_ref, conversation_id="": {"lookup": "failed: x"}
    )
    broker = _broker(tmp_path)
    rb._send(broker, rb.MSG)
    assert rb._rows(broker, "DELIVERY_PROOF_FAILED") == []


# ---- resume: release or redo a review hold -----------------------------------------


def test_resume_extras_carry_one_decision_and_refuse_two_or_a_non_bool():
    assert delivery_proof.resume_extras({}) == {}
    assert delivery_proof.resume_extras({"release_review": True}) == {"release_review": True}
    assert delivery_proof.resume_extras({"redo_review": True}) == {"redo": ["review"]}
    assert delivery_proof.resume_extras({"release_review": False, "redo_review": False}) == {}
    with pytest.raises(ValueError):
        delivery_proof.resume_extras({"release_review": True, "redo_review": True})
    for bad in ("yes", 1, None):
        with pytest.raises(ValueError):
            delivery_proof.resume_extras({"release_review": bad})


def _medchron(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_medchron_verbs as mt

    db = str(tmp_path / "audit.db")
    from workspace_broker.audit_ledger import LedgerWriter
    from workspace_broker.medchron_ledger import MedchronLedger
    from workspace_broker.medchron_verbs import MedchronVerbs

    ledger = LedgerWriter(db)
    (tmp_path / "customer.yaml").write_text(mt.CUSTOMER_YAML)
    queue = tmp_path / "queue"
    v = MedchronVerbs(
        MedchronLedger(db, queue),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=ledger.append,
        gateway_pid=mt.GATEWAY_PID,
        resolve_agent_uid=lambda: mt.AGENT_UID,
    )
    return mt, v, queue


@pytest.mark.parametrize(
    "reason",
    ["review_held: 1 MUST-FIX (a visit dated after today)", "dry_run_reported: a dry run reached the daemon"],
)
def test_a_chronology_held_by_its_review_or_a_dry_run_resumes_with_a_release(tmp_path, reason):
    mt, v, queue = _medchron(tmp_path)
    j = mt._held_job(v, reason)
    out = mt.call(
        v, "medchron_job_resume", peer_uid=ROOT, job_id=j, reason="read it; file it", redo=[], release_review=True
    )
    assert out["queued"] is True
    marker = json.loads((queue / f".resume-{j}.json").read_text())
    assert marker["release_review"] is True and marker["redo"] == []


def test_a_chronology_resume_with_redo_review_adds_the_review_stage_once(tmp_path):
    mt, v, queue = _medchron(tmp_path)
    j = mt._held_job(v, "review_held: 1 MUST-FIX")
    mt.call(v, "medchron_job_resume", peer_uid=ROOT, job_id=j, reason="fixed", redo=["review"], redo_review=True)
    assert json.loads((queue / f".resume-{j}.json").read_text())["redo"] == ["review"]


def test_a_chronology_resume_refuses_both_decisions_and_writes_nothing(tmp_path):
    mt, v, queue = _medchron(tmp_path)
    j = mt._held_job(v, "review_held: 1 MUST-FIX")
    with pytest.raises(ValueError):
        mt.call(
            v,
            "medchron_job_resume",
            peer_uid=ROOT,
            job_id=j,
            reason="x",
            redo=[],
            release_review=True,
            redo_review=True,
        )
    with pytest.raises(ValueError):
        mt.call(v, "medchron_job_resume", peer_uid=ROOT, job_id=j, reason="x", redo=[], release_review="true")
    assert not (queue / f".resume-{j}.json").exists()


def test_an_ordinary_hold_is_still_not_resumable(tmp_path):
    mt, v, queue = _medchron(tmp_path)
    j = mt._held_job(v, "refused: the firm must decide which folder")
    with pytest.raises(ValueError):
        mt.call(v, "medchron_job_resume", peer_uid=ROOT, job_id=j, reason="x", redo=[], release_review=True)


def test_a_failed_demand_resume_carries_the_release_into_its_marker(tmp_path):
    import test_demand_verbs as dt
    from workspace_broker.audit_ledger import LedgerWriter
    from workspace_broker.demand_verbs import DemandVerbs
    from workspace_broker.server import Broker

    db = str(tmp_path / "audit.db")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(dt.YAML.format(allowance=25))
    queue = tmp_path / "demand-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = dt.GATEWAY_PID
    broker.agent_uid = dt.AGENT_UID
    broker.ledger = LedgerWriter(db)
    broker.demand = DemandVerbs(
        DemandLedger(db, queue), customer_yaml=str(yaml_path), audit_append=broker.ledger.append
    )
    job = rb._job(tmp_path, ["running"])
    DemandLedger(db, queue).record(job, "failed", {"reason": "review_held: a false declaration"})
    out = dt.call(broker, "demand_job_resume", uid=ROOT, job_id=job, reason="read; file it", release_review=True)
    assert out["queued"] is True
    assert json.loads((queue / f".resume-{job}.json").read_text())["release_review"] is True
    with pytest.raises(ValueError):
        dt.call(broker, "demand_job_resume", uid=ROOT, job_id=job, reason="x", release_review=True, redo_review=True)


# ---- AgentMail seats: dispatched sends are the reply evidence ---------------------


def _dispatched(broker, recipients: list[str]) -> None:
    broker.ledger.append(
        {
            "action_type": "CONFIRM_SEND_DISPATCHED",
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps({"outcome": "sent", "recipients": recipients, "message_id": "m"}),
        }
    )


def test_a_graph_seat_reports_its_channel_and_counts_no_sends(tmp_path):
    out = _root(_broker(tmp_path), "delivery_reply_status", kind="demand_job", job_id="01J", requester="a@x.test")
    assert out["channel"] == "msgraph" and "sends_to_requester" not in out


def test_an_agentmail_seat_counts_only_sends_to_the_requester_since_the_watch(tmp_path):
    broker = _broker(tmp_path)
    broker.msgraph = None
    _dispatched(broker, ["Admin@Example.Test"])
    _dispatched(broker, ["someone@else.test"])
    base = {"kind": "demand_job", "job_id": "01J"}
    out = _root(broker, "delivery_reply_status", **base, requester="admin@example.test", since="2000-01-01T00:00:00Z")
    assert out["channel"] == "agentmail" and out["sends_to_requester"] == 1
    later = _root(broker, "delivery_reply_status", **base, requester="admin@example.test", since="2999-01-01T00:00:00Z")
    assert later["sends_to_requester"] == 0
    nobody = _root(broker, "delivery_reply_status", **base, requester="", since="2000-01-01T00:00:00Z")
    assert nobody["sends_to_requester"] == 0
