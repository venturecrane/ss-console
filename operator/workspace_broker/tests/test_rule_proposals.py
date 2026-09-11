"""establish_propose / establish_pending / establish_submit(firm_adjust).

The propose-read-back-confirm path (ss-console#2529, ADR 0085 §4 as amended
2026-08-21). A firm establishes how its letters read by saying so; the Operator
states the rule back with a tag; the person answers; the rule commits. The
compilers cannot gate any of that — every one of them refuses an empty corpus,
and a sentence in an email has no corpus — so the controls are the admin allow
list (seat-side), sender attribution, the untainted turn, and the readback.

WHAT THE READBACK IS WORTH, and therefore what these tests are about: the
person said yes to ONE sentence, and that yes means nothing unless the bytes
committed are the bytes they were shown. So the load-bearing assertions here are

* the committed text and subject come out of the pending ROW, never off the
  wire, and a request carrying a different text is REFUSED rather than quietly
  substituted (the substitution is the attack, and it would leave the firm
  holding a rule it never agreed to with a ledger row saying it confirmed one);
* a proposal commits exactly once, enforced by a conditional UPDATE rather than
  a read-then-write the caller could interleave;
* an expired proposal refuses BY NAME ("state it again"), never "in effect";
* a forged or unknown id installs nothing;
* a personal rule's subject is the person stating it, and only that person can
  confirm it;
* no row this path writes ever carries the rule's TEXT.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.establishment import (
    ACT_COMMITTED_ACTION_TYPE,
    ACT_PROPOSED_ACTION_TYPE,
    ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
    MAX_RULE_TEXT_BYTES,
    PROPOSAL_TTL_SECONDS,
    RULE_PROPOSED_ACTION_TYPE,
    EstablishmentStore,
    EstablishmentValidationError,
    normalize_rule_text,
    readback_for,
)
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42

ADMIN = "christa@firm.com"
PARALEGAL = "sarah@firm.com"
RULE = "In client letters, be more formal and shorter; no pleasantries."


def _broker(tmp_path: Path) -> Broker:
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    db_path = str(tmp_path / "audit.db")
    broker.ledger = LedgerWriter(db_path)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=db_path)
    broker.db_path = db_path
    return broker


def _call(broker: Broker, **request):
    return broker.handle(request, peer_pid=9999, peer_uid=AGENT_UID)


def _propose(broker: Broker, **over):
    request = {
        "action": "establish_propose",
        "scope": "firm_adjust",
        "subject": {"output_class": "outbound", "property": "voice"},
        "text": RULE,
        "instructed_by": ADMIN,
        "source_ref": "msg-41",
        "for_admin": False,
    }
    request.update(over)
    return _call(broker, **request)


def _rows(broker: Broker, action_type: str) -> list[dict]:
    conn = sqlite3.connect(broker.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE action_type=? ORDER BY id", (action_type,))
        ]
    finally:
        conn.close()


def _submission(broker: Broker, run_id: str) -> dict:
    return json.loads((broker.establishment.runs_dir / run_id / "submission.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def test_propose_returns_the_readback_the_seat_must_send(tmp_path):
    broker = _broker(tmp_path)
    result = _propose(broker)
    assert result["ok"] is True
    assert len(result["proposal_id"]) == 8
    assert result["readback"] == f"[rule {result['proposal_id']}] {RULE}"
    assert result["scope"] == "firm_adjust"
    assert result["subject"] == {"output_class": "outbound", "property": "voice"}
    assert result["expires_at"] > time.time()


def test_nothing_is_installed_by_a_proposal(tmp_path):
    """A proposal is a question, not a change. Nothing reaches the spool until
    a person has answered it."""
    broker = _broker(tmp_path)
    _propose(broker)
    assert list(broker.establishment.runs_dir.iterdir()) == []


def test_the_proposal_row_carries_the_digest_and_never_the_sentence(tmp_path):
    broker = _broker(tmp_path)
    result = _propose(broker)
    rows = _rows(broker, RULE_PROPOSED_ACTION_TYPE)
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["proposal_id"] == result["proposal_id"]
    assert metadata["scope"] == "firm_adjust"
    assert metadata["output_class"] == "outbound"
    assert metadata["property"] == "voice"
    assert metadata["instructed_by"] == ADMIN
    assert metadata["text_sha256"] == hashlib.sha256(RULE.encode()).hexdigest()
    assert RULE not in rows[0]["metadata"]
    assert "formal" not in rows[0]["metadata"]


def test_the_rule_text_is_folded_to_one_line_before_anything_sees_it(tmp_path):
    """The readback is one quoted line and the rendered adjustment is one
    bullet. A rule that renders differently from the sentence the person
    confirmed defeats the point of asking them."""
    broker = _broker(tmp_path)
    result = _propose(broker, text="Be formal.\r\n  Be short.\n")
    assert result["readback"].endswith("Be formal. Be short.")
    assert "\n" not in result["readback"]


def test_a_personal_rules_subject_must_be_the_person_stating_it(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="subject must be the person stating it"):
        _propose(
            broker,
            scope="person",
            subject={"person": PARALEGAL},
            instructed_by=ADMIN,
        )


def test_a_personal_rule_cannot_be_marked_for_an_admin(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="for_admin must be false"):
        _propose(
            broker,
            scope="person",
            subject={"person": ADMIN},
            for_admin=True,
        )


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"scope": "firm"}, "scope must be one of"),
        ({"scope": "team"}, "scope must be one of"),
        ({"subject": "outbound"}, "subject must be an object"),
        ({"subject": {"output_class": "Outbound", "property": "voice"}}, "output_class must match"),
        ({"subject": {"output_class": "outbound", "property": "gates"}}, "property must be one of"),
        ({"text": "   "}, "text must not be empty"),
        ({"text": 7}, "text must be a string"),
        ({"text": "x" * (MAX_RULE_TEXT_BYTES + 1)}, "the ceiling is"),
        ({"instructed_by": "christa"}, "single person email address"),
        ({"for_admin": "yes"}, "for_admin must be a boolean"),
        ({"source_ref": ""}, "source_ref must not be empty"),
    ],
)
def test_malformed_proposals_are_refused_by_name(tmp_path, over, needle):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match=needle):
        _propose(broker, **over)
    assert _rows(broker, RULE_PROPOSED_ACTION_TYPE) == []


# ---------------------------------------------------------------------------
# pending
# ---------------------------------------------------------------------------


def test_pending_returns_the_senders_own_open_rules(tmp_path):
    broker = _broker(tmp_path)
    mine = _propose(broker, instructed_by=ADMIN)
    _propose(broker, instructed_by=PARALEGAL, for_admin=True)
    result = _call(broker, action="establish_pending", sender=ADMIN)
    assert [p["proposal_id"] for p in result["pending"]] == [mine["proposal_id"]]
    assert result["pending"][0]["readback"] == mine["readback"]
    assert result["pending"][0]["text"] == RULE


def test_for_admin_rules_surface_only_when_the_caller_asks(tmp_path):
    """A paralegal's firm-level remark waits for an admin. The broker cannot
    tell who is an admin — customer.yaml is not readable at this uid — so the
    seat passes that decision in rather than the broker guessing at it."""
    broker = _broker(tmp_path)
    theirs = _propose(broker, instructed_by=PARALEGAL, for_admin=True)
    assert _call(broker, action="establish_pending", sender=ADMIN)["pending"] == []
    with_admin = _call(broker, action="establish_pending", sender=ADMIN, include_for_admin=True)
    assert [p["proposal_id"] for p in with_admin["pending"]] == [theirs["proposal_id"]]
    assert with_admin["pending"][0]["for_admin"] is True
    assert with_admin["pending"][0]["instructed_by"] == PARALEGAL


def test_pending_by_id_returns_that_row_and_writes_no_audit_row(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    before = len(_rows(broker, RULE_PROPOSED_ACTION_TYPE))
    result = _call(broker, action="establish_pending", proposal_id=proposed["proposal_id"])
    assert [p["proposal_id"] for p in result["pending"]] == [proposed["proposal_id"]]
    assert len(_rows(broker, RULE_PROPOSED_ACTION_TYPE)) == before


def test_pending_by_an_unknown_id_is_empty_not_an_error(tmp_path):
    broker = _broker(tmp_path)
    assert _call(broker, action="establish_pending", proposal_id="deadbeef")["pending"] == []


def test_a_forged_id_shape_is_refused(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="eight lowercase hex"):
        _call(broker, action="establish_pending", proposal_id="../../etc/passwd")


# ---------------------------------------------------------------------------
# submit: the committed rule comes from the row
# ---------------------------------------------------------------------------


def _confirm(broker: Broker, proposal_id: str, **over):
    request = {
        "action": "establish_submit",
        "scope": "firm_adjust",
        "proposal_id": proposal_id,
        "instructed_by": ADMIN,
        "source_ref": "msg-42",
    }
    request.update(over)
    return _call(broker, **request)


def test_a_confirmed_rule_materializes_the_adjustment_from_the_row(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    result = _confirm(broker, proposed["proposal_id"])
    assert result["status"] == "queued"
    submission = _submission(broker, result["run_id"])
    assert submission["scope"] == "firm_adjust"
    assert submission["output_class"] == "outbound"
    assert submission["property"] == "voice"
    adjustment = submission["adjustment"]
    assert adjustment["id"] == proposed["proposal_id"]
    assert adjustment["text"] == RULE
    assert adjustment["sha256"] == hashlib.sha256(RULE.encode()).hexdigest()
    assert adjustment["instructed_by"] == ADMIN
    assert adjustment["applied_by"] == ADMIN
    assert adjustment["at"].endswith("Z")


def test_a_paralegals_rule_records_who_stated_it_and_who_applied_it(tmp_path):
    """The non-admin leg. The firm can read who asked for a rule and who put it
    in force; both render in the spec file the model reads."""
    broker = _broker(tmp_path)
    proposed = _propose(broker, instructed_by=PARALEGAL, for_admin=True)
    result = _confirm(broker, proposed["proposal_id"], instructed_by=ADMIN)
    adjustment = _submission(broker, result["run_id"])["adjustment"]
    assert adjustment["instructed_by"] == PARALEGAL
    assert adjustment["applied_by"] == ADMIN


def test_a_submit_carrying_a_different_text_is_refused_not_substituted(tmp_path):
    """THE assertion this mechanism exists for. Silently taking the request's
    text would leave the firm holding a rule it never agreed to, with a ledger
    row saying it confirmed one."""
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    with pytest.raises(EstablishmentValidationError, match="does not match rule"):
        _confirm(
            broker,
            proposed["proposal_id"],
            spec_body="In client letters, be warm and chatty.",
        )
    assert list(broker.establishment.runs_dir.iterdir()) == []


def test_a_submit_carrying_a_different_output_class_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    with pytest.raises(EstablishmentValidationError, match="does not match rule"):
        _confirm(broker, proposed["proposal_id"], output_class="staff")


def test_echoing_the_proposals_own_fields_is_allowed(tmp_path):
    """A seat that reads its pending list and passes the fields back through is
    doing nothing wrong; only a CHANGE is refused."""
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    result = _confirm(
        broker,
        proposed["proposal_id"],
        output_class="outbound",
        property="voice",
        spec_body=RULE,
    )
    assert result["status"] == "queued"


def test_a_rule_commits_exactly_once(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    _confirm(broker, proposed["proposal_id"])
    with pytest.raises(EstablishmentValidationError, match="already committed"):
        _confirm(broker, proposed["proposal_id"])
    runs = list(broker.establishment.runs_dir.iterdir())
    assert len(runs) == 1


def test_consume_is_conditional_so_a_race_cannot_commit_twice(tmp_path):
    """The SECOND layer, asserted where it actually lives.

    The test above is satisfied by the read in ``_claim_proposal``, which sees a
    consumed row and refuses — so it passes even if ``consume`` is an
    unconditional UPDATE, and the read-then-write window it leaves open is
    exactly the one two confirmations arriving together would drive through.
    This drives the store directly: the second call must lose, and the first
    caller's run id must be the one on record.
    """
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    pending = broker.establishment.pending
    assert pending.consume(proposed["proposal_id"], "run-first") is True
    assert pending.consume(proposed["proposal_id"], "run-second") is False
    assert pending.get(proposed["proposal_id"])["consumed_run_id"] == "run-first"


def test_an_unknown_proposal_id_refuses_and_installs_nothing(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="state the rule again"):
        _confirm(broker, "0123abcd")
    assert list(broker.establishment.runs_dir.iterdir()) == []


def test_a_firm_adjust_submit_without_a_proposal_id_is_refused(tmp_path):
    """There is no route by which a firm-wide rule installs without a person
    having been shown it and having said yes."""
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="proposal_id must be a string"):
        _call(
            broker,
            action="establish_submit",
            scope="firm_adjust",
            output_class="outbound",
            property="voice",
            spec_body=RULE,
            instructed_by=ADMIN,
            source_ref="msg-42",
        )


def test_a_proposal_confirmed_under_the_wrong_scope_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker, scope="person", subject={"person": ADMIN})
    with pytest.raises(EstablishmentValidationError, match="was proposed as 'person'"):
        _confirm(broker, proposed["proposal_id"])


def test_the_submitted_row_carries_the_proposal_id_and_no_text(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    _confirm(broker, proposed["proposal_id"])
    rows = _rows(broker, ESTABLISHMENT_SUBMITTED_ACTION_TYPE)
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["scope"] == "firm_adjust"
    assert metadata["proposal_id"] == proposed["proposal_id"]
    assert metadata["spec_sha256"] == hashlib.sha256(RULE.encode()).hexdigest()
    assert metadata["applied_by"] == ADMIN
    assert RULE not in rows[0]["metadata"]


# ---------------------------------------------------------------------------
# expiry
# ---------------------------------------------------------------------------


def _age_out(broker: Broker, proposal_id: str) -> None:
    """Push one proposal past its TTL, using the broker's own clock."""
    conn = sqlite3.connect(broker.db_path)
    try:
        conn.execute(
            "UPDATE pending_rules SET created_at=?, expires_at=? WHERE proposal_id=?",
            (
                time.time() - PROPOSAL_TTL_SECONDS - 60,
                time.time() - 60,
                proposal_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_an_expired_proposal_refuses_by_name_and_never_claims_effect(tmp_path):
    """ "That rule expired, state it again" and "that rule is in effect" are
    different sentences, and a person who confirmed a rule is owed the true one."""
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    _age_out(broker, proposed["proposal_id"])
    # Read the row directly: the sweep on the next verb would remove it first.
    with pytest.raises(EstablishmentValidationError, match="expired; state it again"):
        broker.establishment._claim_proposal({"proposal_id": proposed["proposal_id"]}, "firm_adjust")


def test_an_expired_proposal_is_swept_into_a_lapse_and_never_commits(tmp_path):
    """ss-console#2546 changed this test's subject, and the change IS the issue.

    The sweep used to DELETE an expired row, and that deletion is why a lapse
    was silent: the row that would have let anyone tell the person who asked was
    gone, and the table's honest answer became "nobody ever proposed that". It
    is now marked instead, so the seat has something true to report, and a late
    "yes" still commits nothing.
    """
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    _age_out(broker, proposed["proposal_id"])
    broker.establishment.sweep()
    row = broker.establishment.pending.get(proposed["proposal_id"])
    assert row is not None
    assert row["lapsed_at"] is not None
    assert row["consumed_at"] is None
    with pytest.raises(EstablishmentValidationError, match="lapsed unanswered"):
        _confirm(broker, proposed["proposal_id"])


def test_an_expired_proposal_is_not_offered_as_pending(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    live = _propose(broker, text="Name the deadline in the first paragraph.")
    _age_out(broker, proposed["proposal_id"])
    result = _call(broker, action="establish_pending", sender=ADMIN)
    assert [p["proposal_id"] for p in result["pending"]] == [live["proposal_id"]]


def test_a_committed_rule_is_kept_briefly_so_a_retry_gets_the_true_answer(tmp_path):
    """Deleting it on commit would answer a second "yes" with "unknown
    proposal", which reads to the firm as though the rule was lost."""
    broker = _broker(tmp_path)
    proposed = _propose(broker)
    _confirm(broker, proposed["proposal_id"])
    broker.establishment.sweep()
    row = broker.establishment.pending.get(proposed["proposal_id"])
    assert row is not None and row["consumed_at"] is not None


# ---------------------------------------------------------------------------
# person scope with a proposal
# ---------------------------------------------------------------------------


def test_a_person_confirms_their_own_preference_from_the_row(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(
        broker,
        scope="person",
        subject={"person": PARALEGAL},
        instructed_by=PARALEGAL,
        text="Give me the deadline first, then the detail.",
    )
    result = _call(
        broker,
        action="establish_submit",
        scope="person",
        proposal_id=proposed["proposal_id"],
        instructed_by=PARALEGAL,
        source_ref="msg-9",
        append=True,
    )
    submission = _submission(broker, result["run_id"])
    assert submission["scope"] == "person"
    assert submission["person"] == PARALEGAL
    assert submission["spec_body"] == "Give me the deadline first, then the detail."
    assert submission["append"] is True


def test_nobody_else_can_confirm_a_persons_preference(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(
        broker,
        scope="person",
        subject={"person": PARALEGAL},
        instructed_by=PARALEGAL,
        text="Bullets, under 150 words.",
    )
    with pytest.raises(EstablishmentValidationError, match="cannot confirm a preference for"):
        _call(
            broker,
            action="establish_submit",
            scope="person",
            proposal_id=proposed["proposal_id"],
            instructed_by=ADMIN,
            source_ref="msg-9",
        )


def test_the_direct_person_submit_still_works_without_a_proposal(tmp_path):
    """The pre-#2529 path is unchanged: a proposal is offered on this scope,
    not required."""
    broker = _broker(tmp_path)
    result = _call(
        broker,
        action="establish_submit",
        scope="person",
        person=PARALEGAL,
        spec_body="Bullets, under 150 words.",
        instructed_by=PARALEGAL,
        source_ref="msg-9",
    )
    submission = _submission(broker, result["run_id"])
    assert submission["person"] == PARALEGAL
    assert submission["append"] is False


def test_a_non_boolean_append_is_refused(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="append must be a boolean"):
        _call(
            broker,
            action="establish_submit",
            scope="person",
            person=PARALEGAL,
            spec_body="Bullets.",
            instructed_by=PARALEGAL,
            source_ref="msg-9",
            append="yes",
        )


# ---------------------------------------------------------------------------
# a broker with no rule store
# ---------------------------------------------------------------------------


def test_a_broker_with_no_rule_store_refuses_by_name(tmp_path):
    """Fail-closed and NAMED. A rule the firm cannot be shown back is a rule it
    cannot confirm, and committing one without the readback is the thing this
    whole path exists to avoid."""
    broker = _broker(tmp_path)
    broker.establishment.pending = None
    with pytest.raises(EstablishmentValidationError, match="no rule store configured"):
        _propose(broker)


# ---------------------------------------------------------------------------
# the helpers, directly
# ---------------------------------------------------------------------------


def test_readback_is_the_tag_then_the_sentence():
    assert readback_for("7f3a2c1d", RULE) == f"[rule 7f3a2c1d] {RULE}"


def test_normalize_rule_text_folds_every_line_break():
    assert normalize_rule_text("a\r\nb\rc\n\nd") == "a b c d"


# ---------------------------------------------------------------------------
# act_propose / act_commit  (ss-console#2536)
# ---------------------------------------------------------------------------
#
# WHAT AN ACT PROPOSAL IS WORTH, and therefore what these tests are about. A
# rule proposal asks a person to agree to a sentence. An act proposal asks them
# to agree to a CHANGE IN THEIR OWN RECORD, and the only thing that makes that
# safe is that the model contributes nothing to it: every value comes out of the
# seat's authored config, the sentence the person reads is rendered from those
# values by this uid, and a payload that differs from the authored one by a
# single character is refused rather than committed.
#
# So the load-bearing assertions are: the payload is the authored block or the
# proposal does not happen; the readback names every value and is rendered here;
# a seat with nothing authored can propose nothing; the act commits exactly once
# and only under its own tag; and no row this path writes carries the firm's
# values, only their digest.

ACT_TOOL = "mcp_smokeball_create_matter"
ACT_NUMBER = "OPS-OPERATOR-LIBRARY"
ACT_DESCRIPTION = "Operator Library"
ACT_CONTACT_ID = "11111111-2222-3333-4444-555555555555"
ACT_TYPE_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa_CA"
CONTACT_NAME = "Example and Partners"
TYPE_NAME = "Personal Injury Plaintiff"

# The exposure half of an authored seat. The broker refuses to write an act row
# for a seat that authorizes no confirmable act at all, so the fixture has to
# carry it; the test below removes it and asserts the refusal.
AUTHORED_EXPOSURE = """\
personas:
  - slug: operator
    entitlements:
      exposure:
        internal_write: autonomous
        commitment: confirm
"""

AUTHORED_MATTER_BLOCK = f"""\
self_initiation:
  document_library:
    operator_matter:
      number: '{ACT_NUMBER}'
      description: '{ACT_DESCRIPTION}'
      client_contact_id: '{ACT_CONTACT_ID}'
      matter_type_id: '{ACT_TYPE_ID}'
"""

AUTHORED_YAML = (
    AUTHORED_EXPOSURE
    + f"""\
self_initiation:
  sequence:
    - operator-self-test
  document_library:
    folder_name: 'Document Library'
    operator_matter:
      number: '{ACT_NUMBER}'
      description: '{ACT_DESCRIPTION}'
      client_contact_id: '{ACT_CONTACT_ID}'
      matter_type_id: '{ACT_TYPE_ID}'
"""
)

AUTHORED_PAYLOAD = {
    "description": ACT_DESCRIPTION,
    "matter_type_id": ACT_TYPE_ID,
    "client_contact_id": ACT_CONTACT_ID,
    "number": ACT_NUMBER,
}


def _act_broker(tmp_path: Path, customer_yaml: str | None = AUTHORED_YAML) -> Broker:
    """A broker whose establishment store can read a seat config.

    Separate from ``_broker`` on purpose: the rule path has never needed the
    seat's config and must keep working without one, so the two fixtures differ only
    in the handle the act path requires.
    """
    broker = _broker(tmp_path)
    if customer_yaml is None:
        broker.establishment.customer_path = None
        return broker
    path = tmp_path / "customer.yaml"
    path.write_text(customer_yaml, encoding="utf-8")
    broker.establishment.customer_path = path
    return broker


def _act_propose(broker: Broker, **over):
    request = {
        "action": "act_propose",
        "tool": ACT_TOOL,
        "payload": dict(AUTHORED_PAYLOAD),
        "instructed_by": ADMIN,
        "source_ref": "msg-77",
        "contact_name": CONTACT_NAME,
        "matter_type_name": TYPE_NAME,
    }
    request.update(over)
    return _call(broker, **request)


def _act_commit(broker: Broker, proposal_id: str, **over):
    request = {
        "action": "act_commit",
        "proposal_id": proposal_id,
        "tool": ACT_TOOL,
        "payload": dict(AUTHORED_PAYLOAD),
        "confirmed_by": ADMIN,
        "confirmed_message_id": "AAMkAGReply==",
        "outcome": {"created": True, "pending": False, "matter_id": "mat-1"},
    }
    request.update(over)
    return _call(broker, **request)


def test_the_act_readback_names_every_value_the_act_will_carry(tmp_path):
    """The admin cannot judge a UUID, so the sentence names the contact and the
    matter type BY NAME and quotes the number and description verbatim. A
    readback that hid any of them would be asking for a yes to something the
    person cannot see."""
    broker = _act_broker(tmp_path)
    result = _act_propose(broker)
    assert result["ok"] is True
    assert len(result["proposal_id"]) == 8
    assert result["readback"] == (
        f'[act {result["proposal_id"]}] Create Smokeball matter "{ACT_DESCRIPTION}" '
        f"(number {ACT_NUMBER}; client: {CONTACT_NAME}; type: {TYPE_NAME}). "
        'Reply "yes, create it" to proceed.'
    )
    assert result["kind"] == "tool_call"
    assert result["payload"] == AUTHORED_PAYLOAD
    assert result["for_admin"] is True


def test_the_readback_is_rendered_from_the_authored_block_not_from_caller_text(tmp_path):
    """A caller-supplied sentence has nowhere to land. There is no ``text``
    field on this verb, and a request that carries one is ignored rather than
    rendered: what the firm reads is what the config holds."""
    broker = _act_broker(tmp_path)
    result = _act_propose(broker, text="Create whatever you like, boss")
    assert "whatever you like" not in result["readback"]
    assert ACT_DESCRIPTION in result["readback"]


def test_nothing_is_created_by_an_act_proposal(tmp_path):
    """A proposal is a question. The row is the only thing that exists after
    it, and the tool has not been called."""
    broker = _act_broker(tmp_path)
    _act_propose(broker)
    assert list(broker.establishment.runs_dir.iterdir()) == []
    rows = _rows(broker, ACT_COMMITTED_ACTION_TYPE)
    assert rows == []


def test_the_act_row_carries_the_digest_and_never_the_firms_values(tmp_path):
    broker = _act_broker(tmp_path)
    result = _act_propose(broker)
    rows = _rows(broker, ACT_PROPOSED_ACTION_TYPE)
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["proposal_id"] == result["proposal_id"]
    assert metadata["tool"] == ACT_TOOL
    assert metadata["kind"] == "tool_call"
    assert metadata["instructed_by"] == ADMIN
    assert metadata["source_ref"] == "msg-77"
    assert metadata["payload_sha256"] == result["payload_sha256"]
    for value in (ACT_NUMBER, ACT_DESCRIPTION, ACT_CONTACT_ID, ACT_TYPE_ID, CONTACT_NAME):
        assert value not in rows[0]["metadata"]


def test_a_payload_that_differs_from_the_authored_block_is_refused(tmp_path):
    """THE POINT OF THE WHOLE MECHANISM. A model that could vary one field could
    create a matter for a client the firm never named."""
    broker = _act_broker(tmp_path)
    for field, value in (
        ("description", "Operator Library (mine)"),
        ("number", "OPS-OPERATOR-LIBRARY-2"),
        ("client_contact_id", "99999999-9999-9999-9999-999999999999"),
        ("matter_type_id", "12345678-1234-1234-1234-123456789012_CA"),
    ):
        payload = dict(AUTHORED_PAYLOAD)
        payload[field] = value
        with pytest.raises(EstablishmentValidationError, match="does not match this seat"):
            _act_propose(broker, payload=payload)
    assert _rows(broker, ACT_PROPOSED_ACTION_TYPE) == []


def test_the_refusal_names_the_field_and_never_the_two_values(tmp_path):
    """A refusal that printed both sides would put a caller-supplied string into
    the reply, which is what the comparison exists to keep out."""
    broker = _act_broker(tmp_path)
    payload = dict(AUTHORED_PAYLOAD)
    payload["description"] = "Not The Authored Description"
    with pytest.raises(EstablishmentValidationError) as excinfo:
        _act_propose(broker, payload=payload)
    assert "description" in str(excinfo.value)
    assert "Not The Authored Description" not in str(excinfo.value)


def test_a_tool_outside_the_closed_vocabulary_cannot_be_proposed(tmp_path):
    broker = _act_broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="not an act this broker can propose"):
        _act_propose(broker, tool="mcp_smokeball_delete_file")


def test_a_payload_field_the_tool_does_not_take_is_refused(tmp_path):
    broker = _act_broker(tmp_path)
    payload = dict(AUTHORED_PAYLOAD)
    payload["status"] = "Closed"
    with pytest.raises(EstablishmentValidationError, match="does not take"):
        _act_propose(broker, payload=payload)


def test_an_oversize_payload_field_is_refused_before_anything_is_compared(tmp_path):
    broker = _act_broker(tmp_path)
    payload = dict(AUTHORED_PAYLOAD)
    payload["description"] = "x" * 5000
    with pytest.raises(EstablishmentValidationError, match="ceiling"):
        _act_propose(broker, payload=payload)


def test_a_seat_with_no_authored_block_can_propose_nothing(tmp_path):
    """ADR 0056's direction. Unconfigured is a safety state, never permission:
    the firm authors the matter it wants, and until it does there is nothing to
    put in front of anybody."""
    broker = _act_broker(tmp_path, customer_yaml=AUTHORED_EXPOSURE + "self_initiation:\n  sequence: []\n")
    with pytest.raises(EstablishmentValidationError, match="no authored"):
        _act_propose(broker)
    assert _rows(broker, ACT_PROPOSED_ACTION_TYPE) == []


def test_a_seat_that_authorizes_no_confirmable_act_can_write_no_act_row(tmp_path):
    """The seat-level precondition, and it is deliberately the WEAKER of the two
    exposure checks. The persona-aware clamp belongs to the seat's enforcement
    hook, which knows which persona is running this turn; this one only says
    whether the seat authorizes confirmable acts at all. A seat that authorizes
    none must not be able to write an act row through any path, including a hook
    with a bug in it."""
    no_commitment = AUTHORED_EXPOSURE.replace("        commitment: confirm\n", "")
    broker = _act_broker(tmp_path, customer_yaml=no_commitment + AUTHORED_MATTER_BLOCK)
    with pytest.raises(EstablishmentValidationError, match="no persona with exposure.commitment"):
        _act_propose(broker)
    assert _rows(broker, ACT_PROPOSED_ACTION_TYPE) == []


def test_an_authored_seat_proposes_from_the_matter_block_alone(tmp_path):
    """The two halves are independent: the exposure says acts may be proposed at
    all, the matter block says which one."""
    broker = _act_broker(tmp_path, customer_yaml=AUTHORED_EXPOSURE + AUTHORED_MATTER_BLOCK)
    assert _act_propose(broker)["ok"] is True


def test_a_broker_with_no_config_handle_refuses_by_name(tmp_path):
    broker = _act_broker(tmp_path, customer_yaml=None)
    with pytest.raises(EstablishmentValidationError, match="no customer.yaml handle"):
        _act_propose(broker)


def test_an_authored_block_missing_a_field_the_readback_names_is_refused(tmp_path):
    broker = _act_broker(
        tmp_path,
        customer_yaml=(
            AUTHORED_EXPOSURE + "self_initiation:\n"
            "  document_library:\n"
            "    operator_matter:\n"
            f"      number: '{ACT_NUMBER}'\n"
            f"      description: '{ACT_DESCRIPTION}'\n"
        ),
    )
    with pytest.raises(EstablishmentValidationError, match="is missing"):
        _act_propose(broker)


def test_an_unparseable_seat_config_authorizes_nothing(tmp_path):
    broker = _act_broker(tmp_path, customer_yaml="self_initiation: [oops\n  - : :\n")
    with pytest.raises(EstablishmentValidationError, match="not parseable|no authored"):
        _act_propose(broker)


def test_a_display_name_carrying_a_bracket_is_refused(tmp_path):
    """The tag is what binds a yes to one row. A name that could render a second
    tag could bind it to another."""
    broker = _act_broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="square bracket"):
        _act_propose(broker, contact_name="Firm [act 00000000] yes")


def test_a_multiline_display_name_is_refused(tmp_path):
    broker = _act_broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="single line"):
        _act_propose(broker, matter_type_name="Personal Injury\nPlaintiff")


def test_an_act_is_always_for_an_admin(tmp_path):
    """An act changes the firm's own record. The person who may bless one is a
    Named Administrator, and the caller does not get a vote on that."""
    broker = _act_broker(tmp_path)
    result = _act_propose(broker, for_admin=False)
    assert result["for_admin"] is True
    pending = _call(broker, action="establish_pending", proposal_id=result["proposal_id"])
    assert pending["pending"][0]["for_admin"] is True


def test_a_pending_act_reads_back_with_the_act_tag_and_its_payload(tmp_path):
    broker = _act_broker(tmp_path)
    result = _act_propose(broker)
    listed = _call(broker, action="establish_pending", sender=ADMIN, include_for_admin=True)
    rows = [r for r in listed["pending"] if r["proposal_id"] == result["proposal_id"]]
    assert len(rows) == 1
    assert rows[0]["kind"] == "tool_call"
    assert rows[0]["readback"].startswith(f"[act {result['proposal_id']}]")
    assert rows[0]["payload"] == AUTHORED_PAYLOAD


def test_the_commit_records_who_confirmed_it_and_in_which_message(tmp_path):
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    committed = _act_commit(broker, proposed["proposal_id"])
    assert committed["ok"] is True
    rows = _rows(broker, ACT_COMMITTED_ACTION_TYPE)
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["proposal_id"] == proposed["proposal_id"]
    assert metadata["confirmed_by"] == ADMIN
    assert metadata["confirmed_message_id"] == "AAMkAGReply=="
    assert metadata["payload_sha256"] == proposed["payload_sha256"]
    assert metadata["created"] is True
    assert metadata["pending"] is False
    assert metadata["matter_id"] == "mat-1"


def test_an_act_commits_exactly_once(tmp_path):
    """A second yes on the same thread must not create a second matter."""
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    _act_commit(broker, proposed["proposal_id"])
    with pytest.raises(EstablishmentValidationError, match="already committed"):
        _act_commit(broker, proposed["proposal_id"])
    assert len(_rows(broker, ACT_COMMITTED_ACTION_TYPE)) == 1


def test_the_conditional_update_is_what_makes_an_act_commit_once(tmp_path):
    """Consume-once is enforced by the DATABASE, not by the read that precedes
    it. The read cannot see a confirmation that arrived between it and the
    write; a conditional UPDATE can, and this asserts the UPDATE and not the
    read (the read is separately covered by the second-yes test above, which
    would still pass with the condition removed)."""
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    store = broker.establishment.pending
    assert store.consume(proposed["proposal_id"], "run-a") is True
    assert store.consume(proposed["proposal_id"], "run-b") is False
    row = store.get(proposed["proposal_id"])
    assert row["consumed_run_id"] == "run-a"


def test_an_act_that_loses_the_consume_race_writes_no_ledger_row(tmp_path):
    """The interleave the conditional UPDATE exists for: two confirmations, both
    past the read, one write. The loser must record nothing, because a
    committed row is what says the act happened."""
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    broker.establishment.pending.consume = lambda *_args, **_kwargs: False
    with pytest.raises(EstablishmentValidationError, match="already committed"):
        _act_commit(broker, proposed["proposal_id"])
    assert _rows(broker, ACT_COMMITTED_ACTION_TYPE) == []


def test_a_commit_restating_a_different_payload_is_refused(tmp_path):
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    payload = dict(AUTHORED_PAYLOAD)
    payload["number"] = "SOMETHING-ELSE"
    with pytest.raises(EstablishmentValidationError, match="does not match"):
        _act_commit(broker, proposed["proposal_id"], payload=payload)
    assert _rows(broker, ACT_COMMITTED_ACTION_TYPE) == []


def test_a_confirmation_for_one_act_cannot_commit_a_different_tool(tmp_path):
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    with pytest.raises(EstablishmentValidationError, match="not an act this broker can propose"):
        _act_commit(broker, proposed["proposal_id"], tool="mcp_smokeball_delete_file")


def test_an_expired_act_refuses_by_name_and_commits_nothing(tmp_path):
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    _age_out(broker, proposed["proposal_id"])
    with pytest.raises(EstablishmentValidationError, match="expired|no act was proposed"):
        _act_commit(broker, proposed["proposal_id"])
    assert _rows(broker, ACT_COMMITTED_ACTION_TYPE) == []


def test_an_act_row_cannot_be_committed_through_the_rule_submit_path(tmp_path):
    """The two paths share a table and never share a door. A firm_adjust submit
    naming an act proposal is refused by scope, not by luck."""
    broker = _act_broker(tmp_path)
    proposed = _act_propose(broker)
    with pytest.raises(EstablishmentValidationError, match="was proposed as 'act'"):
        _call(
            broker,
            action="establish_submit",
            scope="firm_adjust",
            proposal_id=proposed["proposal_id"],
            instructed_by=ADMIN,
            confirmed_by=ADMIN,
            source_ref="msg-78",
        )


def test_a_rule_cannot_be_committed_through_the_act_path(tmp_path):
    broker = _act_broker(tmp_path)
    proposed = _propose(broker)
    with pytest.raises(EstablishmentValidationError, match="was proposed as 'firm_adjust'"):
        _act_commit(broker, proposed["proposal_id"])


def test_establish_propose_cannot_mint_an_act_row(tmp_path):
    """The scope is closed on that verb: an act comes through act_propose, where
    the authored-block comparison lives."""
    broker = _act_broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="act is proposed with act_propose"):
        _propose(broker, scope="act")


def test_a_2529_shaped_table_is_migrated_in_place_and_keeps_its_rules(tmp_path):
    """The additive migration, against the exact CREATE ss-console#2529 shipped.
    A rule proposed last week survives and reads back as a rule."""
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE pending_rules ("
        "proposal_id TEXT PRIMARY KEY, scope TEXT NOT NULL, subject_json TEXT NOT NULL, "
        "text TEXT NOT NULL, text_sha256 TEXT NOT NULL, instructed_by TEXT NOT NULL, "
        "for_admin INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, "
        "expires_at REAL NOT NULL, consumed_at REAL, consumed_run_id TEXT)"
    )
    conn.execute(
        "INSERT INTO pending_rules (proposal_id, scope, subject_json, text, text_sha256, "
        "instructed_by, for_admin, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "deadbeef",
            "firm_adjust",
            json.dumps({"output_class": "outbound", "property": "voice"}),
            RULE,
            hashlib.sha256(RULE.encode()).hexdigest(),
            ADMIN,
            0,
            time.time(),
            time.time() + PROPOSAL_TTL_SECONDS,
        ),
    )
    conn.commit()
    conn.close()

    spool = tmp_path / "spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    store = EstablishmentStore(spool, LedgerWriter(db_path), pending_db_path=db_path)
    row = store.pending.get("deadbeef")
    assert row is not None
    assert row["kind"] == "rule"
    assert row["payload"] is None
    assert row["text"] == RULE

    columns = {c[1] for c in sqlite3.connect(db_path).execute("PRAGMA table_info(pending_rules)")}
    assert {"kind", "payload_json"} <= columns


def test_ensure_schema_is_idempotent_over_an_already_migrated_table(tmp_path):
    broker = _act_broker(tmp_path)
    broker.establishment.pending.ensure_schema()
    broker.establishment.pending.ensure_schema()
    result = _act_propose(broker)
    assert result["ok"] is True


# ---- the shape the overlay hook actually sends (hermes-smd-overlay#303/#305) ----
#
# The hook sends the authored block WHOLE as ``payload`` (identifiers and the
# two display names together) and nothing at the request top level. Found on
# 2026-08-21 by reading both halves after they merged: the broker accepted four
# payload keys and required the names top-level, so every live proposal would
# have been refused. These pin the contract from the hook's side.

AUTHORED_YAML_WITH_NAMES = (
    AUTHORED_YAML
    + f"""\
      client_contact_name: '{CONTACT_NAME}'
      matter_type_name: '{TYPE_NAME}'
"""
)

HOOK_PAYLOAD = {
    **AUTHORED_PAYLOAD,
    "client_contact_name": CONTACT_NAME,
    "matter_type_name": TYPE_NAME,
}


def _hook_propose(broker: Broker, payload: dict):
    """Exactly the hook's request: six-key payload, no top-level names."""
    return _call(
        broker,
        action="act_propose",
        tool=ACT_TOOL,
        payload=payload,
        instructed_by=ADMIN,
        source_ref="msg-77",
    )


def test_the_hooks_six_key_payload_proposes_and_renders_the_authored_names(tmp_path):
    broker = _act_broker(tmp_path, AUTHORED_YAML_WITH_NAMES)
    result = _hook_propose(broker, dict(HOOK_PAYLOAD))
    assert result["ok"] is True
    assert CONTACT_NAME in result["readback"] and TYPE_NAME in result["readback"]
    assert ACT_CONTACT_ID not in result["readback"]


def test_names_authored_in_the_block_suffice_when_the_payload_carries_only_identifiers(tmp_path):
    broker = _act_broker(tmp_path, AUTHORED_YAML_WITH_NAMES)
    result = _hook_propose(broker, dict(AUTHORED_PAYLOAD))
    assert result["ok"] is True
    assert CONTACT_NAME in result["readback"]


def test_no_names_anywhere_is_refused_by_name(tmp_path):
    broker = _act_broker(tmp_path)  # block without names
    with pytest.raises(EstablishmentValidationError, match="client_contact_name"):
        _hook_propose(broker, dict(AUTHORED_PAYLOAD))


def test_a_payload_name_that_differs_from_the_authored_name_is_refused(tmp_path):
    broker = _act_broker(tmp_path, AUTHORED_YAML_WITH_NAMES)
    payload = dict(HOOK_PAYLOAD)
    payload["client_contact_name"] = "Somebody Else LLP"
    with pytest.raises(EstablishmentValidationError, match="client_contact_name"):
        _hook_propose(broker, payload)


def test_the_hooks_six_key_payload_commits_against_the_four_key_row(tmp_path):
    broker = _act_broker(tmp_path, AUTHORED_YAML_WITH_NAMES)
    proposal_id = _hook_propose(broker, dict(HOOK_PAYLOAD))["proposal_id"]
    result = _act_commit(broker, proposal_id, payload=dict(HOOK_PAYLOAD))
    assert result["ok"] is True


def test_the_pending_view_names_the_tool_at_the_top_level_for_an_act(tmp_path):
    """The seat reads ``row["tool"]`` when an administrator confirms an act. Read
    live on pilot-smokeball 2026-08-22: with the tool only inside ``subject``,
    the confirmation came back "no longer holding that as something to do"."""
    broker = _act_broker(tmp_path, AUTHORED_YAML_WITH_NAMES)
    proposal_id = _hook_propose(broker, dict(HOOK_PAYLOAD))["proposal_id"]
    listing = _call(broker, action="establish_pending", proposal_id=proposal_id)
    rows = [r for r in listing["pending"] if r["proposal_id"] == proposal_id]
    assert rows and rows[0]["tool"] == ACT_TOOL
    assert rows[0]["kind"] == "tool_call" and rows[0]["payload"]["number"] == ACT_NUMBER
