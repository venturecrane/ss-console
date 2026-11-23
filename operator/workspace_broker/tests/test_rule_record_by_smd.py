"""rule_record_by_smd: SMD records a firm rule a client asked for in writing.

The control that replaces the read-back is the client's own email: a rostered
sender, a received message from him in the Inbox (not a forward), recorded
arriving, and the rule's own text in what he wrote. The rule then goes in
through the path a confirmed yes takes (propose, commit, the install run) and
nobody is written to. Every value is synthetic.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_reply_binding as rb
from workspace_broker import rule_record
from workspace_broker.establishment import EstablishmentStore
from workspace_broker.rule_record_verbs import RULE_RECORDED_BY_SMD_ACTION_TYPE

ROOT_PID = 7777
ATTORNEY = rb.ADMIN
SMD = "captain@smd.example"
RULE = "Never restate the request in full before answering it."
STOCK = "This should be a firm rule."
UNIQUE_HTML = f"<html><body><p>Hi,</p><p>{RULE} {STOCK}</p><p>Thanks</p></body></html>"
QUOTED_ONLY = "Answer each request in one short paragraph only."
BODY_HTML = UNIQUE_HTML + f"<blockquote>From: someone else<br>{QUOTED_ONLY}</blockquote>"
SUBJECT = {"output_class": "work_product", "property": "voice", "doc_kinds": ["discovery_response"]}
REASON = "The attorney asked for this in writing; the Operator refused it on reach."


def _spool(broker, tmp_path: Path) -> None:
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=broker.audit_db_path)


def _seat(tmp_path: Path, unique: str = UNIQUE_HTML, **message_over) -> tuple[rb.Broker, rb.FakeMailbox]:
    fields = {
        "subject": "Discovery responses",
        "body": {"contentType": "html", "content": BODY_HTML},
        "uniqueBody": {"contentType": "html", "content": unique},
        **message_over,
    }
    box = rb.FakeMailbox(**fields)
    broker = rb._broker(tmp_path, box)
    _spool(broker, tmp_path)
    return broker, box


def _root(broker, **req) -> dict:
    return broker.handle(req, peer_pid=ROOT_PID, peer_uid=0)


def _agent(broker, **req) -> dict:
    return broker.handle(req, peer_pid=9999, peer_uid=rb.AGENT_UID)


def _request(**over) -> dict:
    req = {
        "action": "rule_record_by_smd",
        "sender": ATTORNEY,
        "message_id": rb.GRAPH_ID,
        "text": RULE,
        "subject": SUBJECT,
        "recorded_by": SMD,
        "reason": REASON,
    }
    req.update(over)
    return req


def _dry(broker, **over) -> dict:
    return _root(broker, **_request(dry_run=True, **over))


def _record(broker, **over) -> dict:
    dry = _dry(broker, **over)
    assert dry["accepted"] is True, dry
    return _root(broker, **_request(confirm=dry["digest"], **over))


def _installed(broker, out: dict, status: str = "installed") -> dict:
    (broker.establishment.results_dir / f"{out['run_id']}.json").write_text(
        json.dumps({"status": status, "phase": "install", "scope": "firm_adjust"})
    )
    return _root(broker, action="rule_record_probe", proposal_id=out["proposal_id"])


def _proposals(broker) -> list[dict]:
    conn = sqlite3.connect(broker.audit_db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pending_rules")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _actors(broker, action_type: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        return list(conn.execute("SELECT actor, actor_role FROM audit_log WHERE action_type=?", (action_type,)))
    finally:
        conn.close()


def _runs(broker) -> list[dict]:
    runs = broker.establishment.runs_dir
    return [json.loads((d / "submission.json").read_text()) for d in sorted(runs.iterdir()) if d.is_dir()]


def _nothing_written(broker) -> None:
    assert _proposals(broker) == []
    assert _runs(broker) == []
    assert rb._rows(broker, RULE_RECORDED_BY_SMD_ACTION_TYPE) == []
    assert rb._rows(broker, "ESTABLISHMENT_SUBMITTED") == []


# -- the path that works (a refusal means nothing if nothing passes) -----------


def test_the_clients_own_words_are_recorded_and_install_through_the_yes_path(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    out = _record(broker)
    assert out["accepted"] is True and out["already_recorded"] is False
    proposal_id, run_id = out["proposal_id"], out["run_id"]

    row = broker.establishment.pending.get(proposal_id)
    assert (row["scope"], row["subject"], row["text"], row["instructed_by"]) == ("firm_adjust", SUBJECT, RULE, ATTORNEY)
    assert row["consumed_run_id"] == run_id and row["for_admin"] is False

    [sub] = _runs(broker)
    assert (sub["scope"], sub["phase"], sub["output_class"], sub["property"]) == (
        "firm_adjust",
        "install",
        "work_product",
        "voice",
    )
    adj = sub["adjustment"]
    assert (adj["id"], adj["text"], adj["instructed_by"], adj["applied_by"]) == (proposal_id, RULE, ATTORNEY, SMD)
    assert adj["doc_kinds"] == ["discovery_response"]

    [audit] = rb._rows(broker, RULE_RECORDED_BY_SMD_ACTION_TYPE)
    assert (audit["proposal_id"], audit["run_id"], audit["recorded_by"]) == (proposal_id, run_id, SMD)
    assert audit["source_message_id"] == rb.GRAPH_ID and len(audit["text_sha256"]) == 64
    flat = json.dumps(audit)
    assert RULE not in flat and REASON not in flat
    assert box.replies() == [] and box.bodies == []

    probe = _installed(broker, out)
    assert probe["installed_at"] is not None and probe["install_failed"] is None
    # Nobody is owed a letter about it: the seat's sweeper query lists nothing.
    assert _agent(broker, action="establish_pending", include_outcomes=True)["pending"] == []


def test_the_proposed_and_committed_rows_name_smd_not_the_operator(tmp_path: Path) -> None:
    """The feed reads the actor: an SMD-recorded rule was never read back and
    never confirmed by the firm (activity-language.ts)."""
    broker, _ = _seat(tmp_path)
    _record(broker)
    assert _actors(broker, "RULE_PROPOSED") == [("smd", "captain")]
    assert _actors(broker, "ESTABLISHMENT_SUBMITTED") == [("smd", "captain")]


def test_the_agent_path_still_writes_operator_rows(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    _agent(
        broker,
        action="establish_propose",
        scope="firm_adjust",
        subject=SUBJECT,
        text=RULE,
        instructed_by=ATTORNEY,
        source_ref="msg-1",
        by_smd=True,
    )
    assert _actors(broker, "RULE_PROPOSED") == [("operator", "agent")]


def test_the_dry_run_shows_the_rule_and_writes_nothing(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _dry(broker)
    assert out["accepted"] is True and out["dry_run"] is True
    assert (out["text"], out["subject"], out["sender"]) == (RULE, SUBJECT, ATTORNEY)
    assert out["email"]["graph_message_id"] == rb.GRAPH_ID and len(out["digest"]) == 64
    _nothing_written(broker)


def test_the_root_intake_installs_what_smd_submits(tmp_path: Path) -> None:
    """applied_by is the SMD address, not anyone on the roster. Run the real
    root intake on the run the broker wrote and read the result back."""
    from establish_intake import gates
    from establish_intake.intake import EstablishIntake, spec_object_key

    class _S3:
        def __init__(self, spec_dir: Path) -> None:
            self.objects: dict[str, bytes] = {}
            self.spec_dir = spec_dir

        def get_object(self, *, Bucket, Key):
            if Key not in self.objects:
                err = Exception(Key)
                err.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
                raise err
            return {"Body": io.BytesIO(self.objects[Key])}

        def put_object(self, *, Bucket, Key, Body, **_kw):
            self.objects[Key] = bytes(Body)
            if Key == spec_object_key("firm"):
                self.spec_dir.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256(self.objects[Key]).hexdigest()
                (self.spec_dir / "manifest.json").write_text(json.dumps({"source_digest": digest}))

    class _Config:
        output_classes = ["work_product"]

        def sender_on_roster(self, _addr):
            raise AssertionError("a firm_adjust install never asks the roster")

    broker, _ = _seat(tmp_path)
    out = _record(broker)
    s3 = _S3(tmp_path / "specs")
    intake = EstablishIntake(
        spool_dir=broker.establishment.root,
        s3_client=s3,
        bucket="b",
        slug="firm",
        spec_dir=tmp_path / "specs",
        broker_uid=None,
        broker_gid=None,
        sleep_fn=lambda _s: None,
        converge_timeout=0.0,
        customer_config_fn=_Config,
    )
    original = gates.missing_compilers
    gates.missing_compilers = lambda *a, **k: []
    try:
        intake.process_run(broker.establishment.runs_dir / out["run_id"])
    finally:
        gates.missing_compilers = original
    probe = _root(broker, action="rule_record_probe", proposal_id=out["proposal_id"])
    assert probe["installed_at"] is not None, probe
    stored = json.loads(s3.objects[spec_object_key("firm")])["classes"]["work_product"]["voice"]
    assert [(a["id"], a["applied_by"]) for a in stored["adjustments"]] == [(out["proposal_id"], SMD)]


# -- the client's email is the control ------------------------------------------


def test_a_sender_off_the_roster_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, **{"from": {"emailAddress": {"address": rb.STRANGER}}})
    out = _dry(broker, sender=rb.STRANGER)
    assert out["accepted"] is False and "roster" in out["reason"]
    _nothing_written(broker)


def test_an_email_from_someone_else_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, **{"from": {"emailAddress": {"address": "paralegal@firm.example"}}})
    out = _dry(broker)
    assert out["accepted"] is False and "paralegal@firm.example" in out["reason"]
    _nothing_written(broker)


def test_a_rule_whose_words_are_not_in_the_email_is_refused(tmp_path: Path) -> None:
    """The text IS the evidence: a rule SMD wrote that the client did not is refused,
    however much of his email (the stock line included) it sits beside."""
    broker, _ = _seat(tmp_path)
    out = _dry(broker, text="Always attach the full medical chronology to every response.")
    assert out["accepted"] is False and "does not appear" in out["reason"]
    _nothing_written(broker)


def test_the_stock_line_alone_is_not_a_rule(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    for text in (STOCK, f"Yes. {STOCK}", "This should be a firm rule, please."):
        out = _dry(broker, text=text)
        assert out["accepted"] is False and "beyond" in out["reason"], text
    _nothing_written(broker)


def test_words_only_in_quoted_earlier_mail_are_not_his(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _dry(broker, text=QUOTED_ONLY)
    assert out["accepted"] is False and "does not appear" in out["reason"]


@pytest.mark.parametrize("subject", ["FW: Discovery responses", "Fwd: rules", "fw:rules", "  FWD: x"])
def test_a_forwarded_email_is_refused(tmp_path: Path, subject: str) -> None:
    broker, _ = _seat(tmp_path, subject=subject)
    out = _dry(broker)
    assert out["accepted"] is False and "forward" in out["reason"]


def test_text_below_a_forwarded_header_is_not_the_senders(tmp_path: Path) -> None:
    forwarded = (
        "<p>See below.</p><p>---------- Forwarded message ---------</p>"
        f"<p>From: Opposing Counsel</p><p>Date: today</p><p>{RULE}</p>"
    )
    broker, _ = _seat(tmp_path, unique=forwarded, subject="Discovery")
    out = _dry(broker)
    assert out["accepted"] is False and "does not appear" in out["reason"]
    outlook = f"<p>See below.</p><p>From: Opposing Counsel</p><p>Sent: today</p><p>{RULE}</p>"
    (tmp_path / "o").mkdir()
    broker, _ = _seat(tmp_path / "o", unique=outlook, subject="Discovery")
    assert _dry(broker)["accepted"] is False


def test_an_unknown_message_id_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.present = False
    out = _dry(broker)
    assert out["accepted"] is False and "Inbox" in out["reason"]
    _nothing_written(broker)


def test_an_email_the_seat_never_logged_arriving_is_refused(tmp_path: Path) -> None:
    box = rb.FakeMailbox(uniqueBody={"contentType": "html", "content": UNIQUE_HTML})
    broker = rb._broker(tmp_path, box, inbound_row=False)
    _spool(broker, tmp_path)
    out = _dry(broker)
    assert out["accepted"] is False and "audit log" in out["reason"]


def test_a_confirm_that_does_not_match_records_nothing(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _root(broker, **_request(confirm="0" * 64))
    assert out["accepted"] is False and "confirm" in out["reason"]
    _nothing_written(broker)


def test_the_agent_uid_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    for action in ("rule_record_by_smd", "rule_record_probe"):
        with pytest.raises(PermissionError):
            _agent(broker, **{**_request(action=action), "proposal_id": "0" * 8})


# -- once -------------------------------------------------------------------------


def test_an_installed_rule_twice_is_a_no_op_answering_the_first(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker)
    _installed(broker, first)
    again = _record(broker, subject={**SUBJECT, "doc_kinds": ["discovery_response"]})
    assert again["accepted"] is True and again["already_recorded"] is True
    assert (again["proposal_id"], again["run_id"]) == (first["proposal_id"], first["run_id"])
    assert len(_proposals(broker)) == 1 and len(_runs(broker)) == 1
    assert len(rb._rows(broker, RULE_RECORDED_BY_SMD_ACTION_TYPE)) == 1


def test_kinds_in_another_order_are_the_same_subject(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker, subject={**SUBJECT, "doc_kinds": ["memo", "discovery_response"]})
    _installed(broker, first)
    again = _record(broker, subject={**SUBJECT, "doc_kinds": ["discovery_response", "memo"]})
    assert again["already_recorded"] is True and again["proposal_id"] == first["proposal_id"]


def test_a_committed_rule_not_yet_installed_is_not_a_duplicate(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker)
    again = _dry(broker)
    assert again["accepted"] is False and "not yet installed" in again["reason"]
    assert first["run_id"] in again["reason"]
    assert len(_runs(broker)) == 1


def test_a_committed_rule_whose_install_failed_is_not_a_duplicate(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker)
    probe = _installed(broker, first, status="rejected")
    assert probe["installed_at"] is None and "rejected" in probe["install_failed"]
    again = _dry(broker)
    assert again["accepted"] is False and "failed to install" in again["reason"]
    # ...and found failed on the record call itself, before any probe read it.
    (tmp_path / "b").mkdir()
    broker, _ = _seat(tmp_path / "b")
    first = _record(broker)
    (broker.establishment.results_dir / f"{first['run_id']}.json").write_text(json.dumps({"status": "rejected"}))
    assert "failed to install" in _dry(broker)["reason"]


def _open_twin(broker, subject=SUBJECT, for_admin=False) -> str:
    return _agent(
        broker,
        action="establish_propose",
        scope="firm_adjust",
        subject=subject,
        text=RULE,
        instructed_by=ATTORNEY,
        source_ref="msg-1",
        for_admin=for_admin,
    )["proposal_id"]


def test_an_open_twin_of_the_same_rule_is_the_row_committed(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    twin = _open_twin(broker)
    out = _record(broker)
    assert out["proposal_id"] == twin and out["open_twin_committed"] is True
    assert len(_proposals(broker)) == 1 and len(_runs(broker)) == 1
    assert rb._rows(broker, "RULE_CLOSED_BY_SMD") == []


@pytest.mark.parametrize(
    "subject,for_admin",
    [({**SUBJECT, "doc_kinds": ["memo"]}, False), (SUBJECT, True)],
)
def test_an_open_twin_under_another_subject_or_on_an_admin_is_refused_untouched(
    tmp_path: Path, subject: dict, for_admin: bool
) -> None:
    broker, _ = _seat(tmp_path)
    twin = _open_twin(broker, subject=subject, for_admin=for_admin)
    out = _record(broker)
    assert out["accepted"] is False and twin in out["reason"] and "rule_close" in out["reason"]
    row = broker.establishment.pending.get(twin)
    assert row["consumed_at"] is None and row["smd_closed_at"] is None and row["lapsed_at"] is None
    assert _runs(broker) == []


# -- the command ---------------------------------------------------------------


def test_the_command_has_no_quote_and_no_wording_store_precheck(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        rule_record.main(["--quote", "x"])
    assert not hasattr(rule_record, "standing")


def _specs(tmp_path: Path, rules: list[dict]) -> Path:
    root = tmp_path / "specs"
    target = root / rule_record.WORDING_REL
    target.parent.mkdir(parents=True)
    blob = json.dumps({"schema_version": 1, "rules": rules}).encode()
    target.write_bytes(blob)
    manifest = {"wordings": {rule_record.WORDING_REL: {"sha256": hashlib.sha256(blob).hexdigest()}}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_the_probe_reads_the_wording_store_the_lanes_read(tmp_path: Path) -> None:
    rules, why = rule_record.wording_rules(_specs(tmp_path, [{"rule_id": "1a2b3c4d", "text": RULE}]))
    assert why == "ok" and rules == [{"rule_id": "1a2b3c4d", "text": RULE}]
    root = tmp_path / "specs"
    (root / rule_record.WORDING_REL).write_text('{"rules": []} ')
    rules, why = rule_record.wording_rules(root)
    assert rules is None and "digest" in why
