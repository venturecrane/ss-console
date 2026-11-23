"""rule_record_by_smd: SMD records a firm rule a client asked for in writing.

The control that replaces the read-back is the client's own email: a rostered
sender, a received message from him in the Inbox, recorded arriving, and the
quote in that message's own text. The rule then goes in through the path a
confirmed yes takes (propose, commit, the install run) and nobody is written
to. Every value is synthetic.
"""

from __future__ import annotations

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
QUOTE = "Never restate the request in full before answering it. This should be a firm rule."
RULE = "Never restate the request in full before answering it."
UNIQUE_HTML = f"<html><body><p>Hi,</p><p>{QUOTE}</p><p>Thanks</p></body></html>"
QUOTED_ONLY = "Answer each request in one paragraph. This should be a firm rule."
BODY_HTML = UNIQUE_HTML + f"<blockquote>From: someone else<br>{QUOTED_ONLY}</blockquote>"
SUBJECT = {"output_class": "work_product", "property": "voice", "doc_kinds": ["discovery_response"]}
REASON = "The attorney asked for this in writing; the Operator refused it on reach."


def _seat(tmp_path: Path, **message_over) -> tuple[rb.Broker, rb.FakeMailbox]:
    box = rb.FakeMailbox(
        subject="Discovery responses",
        body={"contentType": "html", "content": BODY_HTML},
        uniqueBody={"contentType": "html", "content": UNIQUE_HTML},
        **message_over,
    )
    broker = rb._broker(tmp_path, box)
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=broker.audit_db_path)
    return broker, box


def _root(broker, **req) -> dict:
    return broker.handle(req, peer_pid=ROOT_PID, peer_uid=0)


def _request(**over) -> dict:
    req = {
        "action": "rule_record_by_smd",
        "sender": ATTORNEY,
        "message_id": rb.GRAPH_ID,
        "text": RULE,
        "quote": QUOTE,
        "subject": SUBJECT,
        "recorded_by": SMD,
        "reason": REASON,
    }
    req.update(over)
    return req


def _dry(broker, **over) -> dict:
    return _root(broker, **_request(dry_run=True, **over))


def _record(broker, **over) -> dict:
    digest = _dry(broker, **over)["digest"]
    return _root(broker, **_request(confirm=digest, **over))


def _proposals(broker) -> list[dict]:
    conn = sqlite3.connect(broker.audit_db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pending_rules")]
    except sqlite3.OperationalError:
        return []
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


def test_a_quoted_instruction_is_recorded_and_installs_through_the_yes_path(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    out = _record(broker)
    assert out["accepted"] is True and out["already_recorded"] is False
    proposal_id, run_id = out["proposal_id"], out["run_id"]

    row = broker.establishment.pending.get(proposal_id)
    assert (row["scope"], row["subject"], row["text"], row["instructed_by"]) == ("firm_adjust", SUBJECT, RULE, ATTORNEY)
    assert row["consumed_run_id"] == run_id and row["for_admin"] is False

    # The submission the root intake installs: the confirmed-yes shape.
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
    assert audit["source_message_id"] == rb.GRAPH_ID and len(audit["quote_sha256"]) == 64
    flat = json.dumps(audit)
    assert RULE not in flat and "firm rule" not in flat and REASON not in flat
    assert len(rb._rows(broker, "ESTABLISHMENT_SUBMITTED")) == 1
    assert box.replies() == [] and box.bodies == []

    # The intake installs it; the probe reads the result and stamps installed.
    (broker.establishment.results_dir / f"{run_id}.json").write_text(
        json.dumps({"status": "installed", "phase": "install", "scope": "firm_adjust"})
    )
    probe = _root(broker, action="rule_record_probe", proposal_id=proposal_id)
    assert probe["installed_at"] is not None and probe["install_failed"] is None
    # Nobody is owed a letter about it: the seat's sweeper query lists nothing.
    pending = broker.handle(
        {"action": "establish_pending", "include_outcomes": True}, peer_pid=9999, peer_uid=rb.AGENT_UID
    )
    assert pending["pending"] == []


def test_the_dry_run_shows_the_rule_and_writes_nothing(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _dry(broker)
    assert out["accepted"] is True and out["dry_run"] is True
    assert (out["text"], out["subject"], out["sender"]) == (RULE, SUBJECT, ATTORNEY)
    assert out["email"]["graph_message_id"] == rb.GRAPH_ID and len(out["digest"]) == 64
    _nothing_written(broker)


def test_a_failed_install_is_owed_to_smd(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _record(broker)
    (broker.establishment.results_dir / f"{out['run_id']}.json").write_text(json.dumps({"status": "rejected"}))
    probe = _root(broker, action="rule_record_probe", proposal_id=out["proposal_id"])
    assert probe["installed_at"] is None and "rejected" in probe["install_failed"]


# -- the client's email is the control ------------------------------------------


def test_a_sender_off_the_roster_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, **{"from": {"emailAddress": {"address": rb.STRANGER}}})
    out = _dry(broker, sender=rb.STRANGER)
    assert out["accepted"] is False and "roster" in out["reason"]
    out = _root(broker, **_request(sender=rb.STRANGER, confirm="0" * 64))
    assert out["accepted"] is False
    _nothing_written(broker)


def test_an_email_from_someone_else_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, **{"from": {"emailAddress": {"address": "paralegal@firm.example"}}})
    out = _dry(broker)
    assert out["accepted"] is False and "paralegal@firm.example" in out["reason"]
    _nothing_written(broker)


def test_a_quote_not_in_the_email_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _dry(broker, quote="Always attach the full medical chronology to every response.")
    assert out["accepted"] is False and "quote" in out["reason"]
    _nothing_written(broker)


def test_words_only_in_quoted_earlier_mail_are_not_his(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    assert QUOTED_ONLY.split(".")[0] in BODY_HTML
    out = _dry(broker, quote=QUOTED_ONLY)
    assert out["accepted"] is False and "quote" in out["reason"]


def test_an_unknown_message_id_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.present = False
    out = _dry(broker)
    assert out["accepted"] is False and "Inbox" in out["reason"]
    _nothing_written(broker)


def test_an_email_the_seat_never_logged_arriving_is_refused(tmp_path: Path) -> None:
    box = rb.FakeMailbox(uniqueBody={"contentType": "html", "content": UNIQUE_HTML})
    broker = rb._broker(tmp_path, box, inbound_row=False)
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=broker.audit_db_path)
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
            broker.handle({**_request(action=action), "proposal_id": "0" * 8}, peer_pid=9999, peer_uid=rb.AGENT_UID)


# -- once -------------------------------------------------------------------------


def test_the_same_rule_twice_is_a_no_op_answering_the_first(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker)
    again = _record(broker)
    assert again["accepted"] is True and again["already_recorded"] is True
    assert (again["proposal_id"], again["run_id"]) == (first["proposal_id"], first["run_id"])
    assert len(_proposals(broker)) == 1 and len(_runs(broker)) == 1
    assert len(rb._rows(broker, RULE_RECORDED_BY_SMD_ACTION_TYPE)) == 1


def test_an_open_unconfirmed_twin_is_ended_and_replaced(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    twin = broker.handle(
        {
            "action": "establish_propose",
            "scope": "firm_adjust",
            "subject": SUBJECT,
            "text": RULE,
            "instructed_by": ATTORNEY,
            "source_ref": "msg-1",
            "for_admin": True,
        },
        peer_pid=9999,
        peer_uid=rb.AGENT_UID,
    )["proposal_id"]
    out = _record(broker)
    assert out["replaced"] == twin and out["proposal_id"] != twin
    assert broker.establishment.pending.get(twin)["smd_closed_at"] is not None
    assert len(rb._rows(broker, "RULE_CLOSED_BY_SMD")) == 1


# -- the command's wording-store check --------------------------------------------


def _specs(tmp_path: Path, rules: list[dict]) -> Path:
    import hashlib

    root = tmp_path / "specs"
    target = root / rule_record.WORDING_REL
    target.parent.mkdir(parents=True)
    blob = json.dumps({"schema_version": 1, "rules": rules}).encode()
    target.write_bytes(blob)
    manifest = {"wordings": {rule_record.WORDING_REL: {"sha256": hashlib.sha256(blob).hexdigest()}}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_the_command_stops_on_words_already_in_the_wording_store(tmp_path: Path) -> None:
    rule = {"rule_id": "1a2b3c4d", "text": RULE, "doc_kinds": ["discovery_response", "memo"]}
    rules, why = rule_record.wording_rules(_specs(tmp_path, [rule]))
    assert why == "ok" and rule_record.standing(rules, f"  {RULE.upper()} ", ["discovery_response"]) == rule
    assert rule_record.standing(rules, RULE, ["demand_letter"]) is None
    code = rule_record.main(
        [
            "--specs",
            str(tmp_path / "specs"),
            "--sender",
            ATTORNEY,
            "--message-id",
            rb.GRAPH_ID,
            "--text",
            RULE,
            "--quote",
            QUOTE,
            "--doc-kinds",
            "discovery_response",
            "--by",
            SMD,
            "--reason",
            REASON,
            "--dry-run",
            "--socket",
            str(tmp_path / "no-broker.sock"),
        ]
    )
    assert code == 0


def test_a_wording_store_its_manifest_does_not_vouch_for_is_untrusted(tmp_path: Path) -> None:
    root = _specs(tmp_path, [])
    (root / rule_record.WORDING_REL).write_text('{"rules": []} ')
    rules, why = rule_record.wording_rules(root)
    assert rules is None and "digest" in why
