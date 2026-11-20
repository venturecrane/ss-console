"""The whole loop once, through the real send record and the real broker.

Review 2026-11-20, MUST-FIX 1: the read-back binding formed only in
``relay_sent.audit_sent``, which nothing on the live path calls; the live send
record ``relay_sent.sent`` (the reply plugin's ``_relay_sent``, called by
``_relay_transmit_or_hold``) committed, audited and cleared drafts but never
told ``shared.reply_sent``. So every "Yes" was unbound, got the numbered list,
"all" got the list again, forever.

These tests go THROUGH ``_relay_sent`` (the real function) and the real
``workspace_broker`` (its ``Broker.handle``, the verbs, the tables): a rule is
proposed, its read-back is sent by the relay's send record, and the attorney's
"Yes." whose ``In-Reply-To`` is that sent message commits it and gets "Got it".
Names, addresses and ids are invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from shared import inbound, outbound_gate, readback_send
from tests.conftest import load_plugin
from tests.rule_turns import bound_turn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import (
    readback_bindings as rb,  # noqa: E402 - after the operator tree is on the path
)
from workspace_broker.tests import test_readback_bindings as broker_kit  # noqa: E402 - the same

PAT = broker_kit.ATTORNEY
SENT = "<CAlive1@mail.firm.example>"
FIRST, REPLY = "AAMkFirst=", broker_kit.GRAPH_ID
RULE = "In memos, no headings."


def _prompt(body: str, message_id: str) -> str:
    return (
        f"An inbound email arrived.\nfrom: Pat Q. Doe <{PAT}>\nsubject: Re: memos\n"
        f"message_id: {message_id}\n{inbound.UNTRUSTED_EMAIL_DELIMITER}\n{body}"
    )


class _Cfg:
    connectors: dict = {}
    customer_name = "Doe & Roe LLP"
    personas: list = []

    @property
    def admins(self):
        return [PAT]

    def sender_is_admin(self, sender):
        return str(sender or "").strip().lower() == PAT

    def sender_on_roster(self, sender):
        return True

    def authored_person_name(self, _address):
        return None


@pytest.fixture
def loop(monkeypatch, tmp_path):
    """The reply plugin (its send record, and its register published), the
    establishment plugin, and one real broker behind both."""
    broker = broker_kit._broker(tmp_path)
    reply = load_plugin("hermes-smd-reply")  # publishes its register at import
    for name in (
        "_commit_reply",
        "_emit_reply_event",
        "_discard_drafts",
        "_send_file_continuations",
    ):
        monkeypatch.setattr(reply, name, lambda *a, **k: None)
    monkeypatch.setattr(reply.request_index, "record_reply_opening", lambda *a, **k: None)
    est = load_plugin("hermes-smd-establishment")

    def call(payload: dict) -> dict:
        return broker_kit._call(broker, **payload)

    class _CfgCls:
        @classmethod
        def from_volume(cls, path=None):
            return _Cfg()

    monkeypatch.setattr(est, "_broker_request", call)
    monkeypatch.setattr(est, "CustomerConfig", _CfgCls)
    monkeypatch.setattr(readback_send, "_request", call)
    monkeypatch.setenv("SMD_ADMIN_POSSESSION_DB_PATH", str(tmp_path / "possession.db"))
    yaml = tmp_path / "customer.yaml"
    yaml.write_text("customer_id: x\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml))
    for store in (est._ADMIN_STASH, est._CONFIRMED_STASH, est._READBACK_OWED, est._CONFIRMED_SAY):
        store.clear()
    outbound_gate._reset_firm_sentences_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()
    yield reply, est, broker
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


def _propose_and_send(reply: Any, est: Any, broker: Any) -> tuple[str, str]:
    """Turn 1: the attorney states the rule; the read-back goes out through the
    relay's send record. Returns (proposal id, the read-back sent)."""
    bound_turn("s1", PAT, FIRST)
    est.on_pre_llm_call(
        session_id="s1", sender_id=PAT, user_message=_prompt(f"From now on: {RULE}", FIRST)
    )
    out = json.loads(
        est._propose(
            {
                "scope": "person",
                "subject": {"person": PAT},
                "text": RULE,
                "instructed_by": PAT,
                "source_ref": FIRST,
            },
            session_id="s1",
        )
    )
    assert out["ok"] and "proposal_id" not in out
    pending = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    proposal_id = pending[0]["proposal_id"]
    origin = inbound.InboundOrigin(sender_address=PAT, message_id=FIRST, inbox_id="op@x")
    reply._relay_sent(
        "s1",
        origin,
        "key-1",
        PAT,
        out["readback"],
        (out["readback"], ""),
        (None, None),
        SENT,
        "msgraph",
        None,
        False,
    )
    return proposal_id, out["readback"]


def test_the_send_record_binds_the_read_back_it_carried(loop) -> None:
    """FALSIFIER: take ``reply_sent.notify`` out of ``relay_sent.sent`` and no
    binding exists for the sent message."""
    reply, est, broker = loop
    proposal_id, readback = _propose_and_send(reply, est, broker)
    assert readback.endswith("Reply yes to confirm.") and proposal_id not in readback
    carried, listed = rb.ReadbackBindings(broker.establishment._require_pending()).carried(
        rb.normalize_message_id(SENT), PAT
    )
    assert carried == [proposal_id] and listed is False


def test_a_yes_to_that_message_commits_and_says_got_it(loop) -> None:
    """Propose -> read-back sent through the real relay -> the inbound "Yes."
    whose In-Reply-To is that sent id -> committed -> the "Got it" sentence."""
    reply, est, broker = loop
    proposal_id, _readback = _propose_and_send(reply, est, broker)
    broker.msgraph = broker_kit._Graph([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s2", PAT, REPLY)
    context = est.on_pre_llm_call(
        session_id="s2", sender_id=PAT, user_message=_prompt("Yes.", REPLY)
    )["context"]
    assert est._CONFIRMED_SAY["s2"] == "Got it. That is recorded."
    assert "Got it. That is recorded." in context and proposal_id not in context
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id not in [r["proposal_id"] for r in still_open]


def test_without_the_header_the_same_yes_commits_nothing(loop) -> None:
    """The negative control: the same "Yes." replying to no recorded message
    gets the read-back again (code-rendered), and nothing is committed."""
    reply, est, broker = loop
    proposal_id, _readback = _propose_and_send(reply, est, broker)
    broker.msgraph = broker_kit._Graph(
        [{"name": "In-Reply-To", "value": "<CAother@mail.firm.example>"}]
    )
    bound_turn("s2", PAT, REPLY)
    est.on_pre_llm_call(session_id="s2", sender_id=PAT, user_message=_prompt("Yes.", REPLY))
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id in [r["proposal_id"] for r in still_open]
    assert "s2" not in est._CONFIRMED_SAY


SIGNATURE = (
    "Cordially,\n\nPatrick Q. Doe\nDoe & Roe LLP\n1234 N. Central Ave., Suite 500\n"
    "Phoenix, AZ 85004\nOffice: (602) 555-0100\nFax: (602) 555-0101"
)


def _yes_signed(reply: Any, est: Any, broker: Any) -> str:
    proposal_id, _readback = _propose_and_send(reply, est, broker)
    broker.msgraph = broker_kit._Graph([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s2", PAT, REPLY)
    est.on_pre_llm_call(
        session_id="s2", sender_id=PAT, user_message=_prompt(f"Yes\n\n{SIGNATURE}", REPLY)
    )
    return proposal_id


def test_a_signed_yes_from_a_sender_with_no_recorded_signature_is_read_back_again(loop) -> None:
    """His signature lines are not his until the seat has seen them: a first
    signed yes restates once, and nothing is committed."""
    reply, est, broker = loop
    proposal_id = _yes_signed(reply, est, broker)
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id in [r["proposal_id"] for r in still_open]
    assert "s2" not in est._CONFIRMED_SAY


def test_an_earlier_email_records_his_signature_and_a_signed_yes_then_confirms(loop) -> None:
    """An email of his that no rule decision rides on (a request, no yes in it)
    records its closing block, once; his next signed yes is plain."""
    reply, est, broker = loop
    est.own_rules._SIGNED.discard(PAT)
    bound_turn("s0", PAT, "AAMkEarlier=")
    est.on_pre_llm_call(
        session_id="s0",
        sender_id=PAT,
        user_message=_prompt(f"Please pull the Jones file.\n\n{SIGNATURE}", "AAMkEarlier="),
    )
    store = rb.ReadbackBindings(broker.establishment._require_pending())
    assert "patrick q. doe" in store.signature(PAT) and "doe & roe llp" in store.signature(PAT)
    proposal_id = _yes_signed(reply, est, broker)
    assert est._CONFIRMED_SAY["s2"] == "Got it. That is recorded."
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id not in [r["proposal_id"] for r in still_open]


def test_a_turn_the_relay_cannot_hold_owes_nothing_and_commits_nothing(loop, monkeypatch) -> None:
    """Review 2026-11-20 (SHOULD-FIX): with no register published (or no email
    bound this turn) the read-back is not owed, so its sent message binds
    nothing, and a confirmation on such a turn commits nothing."""
    from shared import turn_says

    reply, est, broker = loop
    monkeypatch.setattr(turn_says, "_NOTE", None)
    proposal_id, _readback = _propose_and_send(reply, est, broker)
    carried, _listed = rb.ReadbackBindings(broker.establishment._require_pending()).carried(
        rb.normalize_message_id(SENT), PAT
    )
    assert carried == []
    # Bound by hand, the yes still commits nothing on a turn that cannot say it.
    rb.ReadbackBindings(broker.establishment._require_pending()).record(
        rb.normalize_message_id(SENT), PAT, [proposal_id], False
    )
    broker.msgraph = broker_kit._Graph([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s2", PAT, REPLY)
    est.on_pre_llm_call(session_id="s2", sender_id=PAT, user_message=_prompt("Yes.", REPLY))
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id in [r["proposal_id"] for r in still_open]
    assert not [r for r in est._COMMITTED_TURN.get("s2", [])]


# ---------------------------------------------------------------------------
# The rules-by-hand stopgap (shared/rules_by_hand.py, #4142) owns rules while it
# is on: nothing of confirm-by-reply binds, reads back, lists, confirms or commits.
# ---------------------------------------------------------------------------


def _by_hand(monkeypatch, tmp_path) -> None:
    yaml = tmp_path / "by-hand.yaml"
    yaml.write_text("logic_in_code:\n  rules_by_hand: enforce\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml))


def _open_rule(broker: Any) -> str:
    """An own rule of Pat's, open, and a read-back of it already bound to SENT
    (as an earlier release, before the switch, would have left it)."""
    proposal_id = broker_kit._propose(broker, RULE)
    rb.ReadbackBindings(broker.establishment._require_pending()).record(
        rb.normalize_message_id(SENT), PAT, [proposal_id], False
    )
    return proposal_id


def _answer(est: Any, broker: Any, body: str, listed: bool = False) -> str:
    if listed:
        pending = broker.establishment._require_pending()
        rb.ReadbackBindings(pending).record(
            "CAlist@mail.firm.example", PAT, [r for r in _ids(broker)], True
        )
        broker.msgraph = broker_kit._Graph(
            [{"name": "In-Reply-To", "value": "<CAlist@mail.firm.example>"}]
        )
    else:
        broker.msgraph = broker_kit._Graph([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s2", PAT, REPLY)
    out = est.on_pre_llm_call(session_id="s2", sender_id=PAT, user_message=_prompt(body, REPLY))
    return (out or {}).get("context", "")


def _ids(broker: Any) -> list[str]:
    return [
        r["proposal_id"]
        for r in broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    ]


@pytest.mark.parametrize(
    ("body", "listed"),
    [
        ("Yes.", False),
        ("all", True),
        ("1 and 3", True),
        ("Yes\nThanks,\nActually make it 12pt", False),
    ],
)
def test_with_rules_by_hand_on_no_reply_confirms_commits_or_lists(
    loop, monkeypatch, tmp_path, body, listed
) -> None:
    """A yes is answered by the stopgap's own note (rules_by_hand.reply_for); a
    bare pick carries no yes and reaches the confirmation hook. FALSIFIER: drop
    the switch check in own_rules._resolve_reply and "all" and "1 and 3" on the
    old list commit."""
    _reply_plugin, est, broker = loop
    _by_hand(monkeypatch, tmp_path)
    proposal_id = _open_rule(broker)
    context = _answer(est, broker, body, listed)
    assert proposal_id in _ids(broker)
    assert "s2" not in est._CONFIRMED_SAY and not est._COMMITTED_TURN.get("s2")
    assert "Reply all to confirm" not in context and "Reply yes to confirm" not in context
    assert not [p for p in est._OWN_TURN.get("s2", {})]


def test_with_rules_by_hand_on_a_proposal_owes_no_read_back(loop, monkeypatch, tmp_path) -> None:
    _reply_plugin, est, broker = loop
    _by_hand(monkeypatch, tmp_path)
    bound_turn("s1", PAT, FIRST)
    out = json.loads(
        est._propose(
            {
                "scope": "person",
                "subject": {"person": PAT},
                "text": RULE,
                "instructed_by": PAT,
                "source_ref": FIRST,
            },
            session_id="s1",
        )
    )
    assert "readback" not in out
    rb.ReadbackBindings(broker.establishment._require_pending())  # its tables exist
    conn = broker.establishment._require_pending()._connect()
    try:
        owed = conn.execute("SELECT COUNT(*) FROM readback_pending").fetchone()[0]
    finally:
        conn.close()
    assert owed == 0


def test_with_rules_by_hand_on_a_lapsed_rule_is_not_reopened(loop, monkeypatch, tmp_path) -> None:
    _reply_plugin, est, _broker = loop
    _by_hand(monkeypatch, tmp_path)
    bound_turn("s2", PAT, REPLY)
    assert est.own_rules.reopen_note("s2", PAT, _prompt("Yes.", REPLY)) is None


def test_with_rules_by_hand_on_the_signature_recorder_still_records(
    loop, monkeypatch, tmp_path
) -> None:
    """Harmless while the switch is on: it decides nothing, and the anchor is
    ready when the switch comes off."""
    _reply_plugin, est, broker = loop
    _by_hand(monkeypatch, tmp_path)
    est.own_rules._SIGNED.discard(PAT)
    bound_turn("s0", PAT, "AAMkEarlier=")
    est.on_pre_llm_call(
        session_id="s0",
        sender_id=PAT,
        user_message=_prompt(f"Please pull the Jones file.\n\n{SIGNATURE}", "AAMkEarlier="),
    )
    assert "patrick q. doe" in rb.ReadbackBindings(
        broker.establishment._require_pending()
    ).signature(PAT)
