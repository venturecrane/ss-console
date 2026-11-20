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
import time
from pathlib import Path
from typing import Any

import pytest

from shared import inbound, outbound_gate, readback_send
from tests.conftest import load_plugin
from tests.rule_turns import bound_turn
from tests.test_rule_reply_shapes import REAL_BLOCK, REAL_YESES, ROSTER_NAME

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import (
    readback_bindings as rb,  # noqa: E402 - after the operator tree is on the path
)
from workspace_broker import sender_block  # noqa: E402 - the same
from workspace_broker.tests import test_readback_bindings as broker_kit  # noqa: E402 - the same

PAT = broker_kit.ATTORNEY
SENT = "<CAlive1@mail.firm.example>"
FIRST, REPLY = "AAMkFirst=", broker_kit.GRAPH_ID
RULE = "In memos, no headings."


def _prompt(body: str, message_id: str, bare: bool = False) -> str:
    """The turn prompt. ``bare``: the Graph lane's from: line, the address alone."""
    sender = PAT if bare else f"Pat Q. Doe <{PAT}>"
    return (
        f"An inbound email arrived.\nfrom: {sender}\nsubject: Re: memos\n"
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


def _propose_and_send(
    reply: Any, est: Any, broker: Any, stated: str = f"From now on: {RULE}", bare: bool = False
) -> tuple[str, str]:
    """Turn 1: the attorney states the rule; the read-back goes out through the
    relay's send record. Returns (proposal id, the read-back sent)."""
    bound_turn("s1", PAT, FIRST)
    est.on_pre_llm_call(session_id="s1", sender_id=PAT, user_message=_prompt(stated, FIRST, bare))
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


def _two_earlier_emails(est: Any) -> None:
    """Two emails of his that no rule decision rides on (requests, no yes in them),
    each ending with his block: the broker learns it (sender_block)."""
    for n in (1, 2):
        mid = f"AAMkEarlier{n}="
        bound_turn(f"s0{n}", PAT, mid)
        est.on_pre_llm_call(
            session_id=f"s0{n}",
            sender_id=PAT,
            user_message=_prompt(f"Please pull the file for matter 44{n}.\n\n{SIGNATURE}", mid),
        )


def test_his_live_emails_teach_the_seat_nothing(loop) -> None:
    """Review 2026-11-20 (MUST-FIX 2): his block comes ONLY from SMD's root
    seed. Emails arriving at runtime, however alike they end, store nothing, so
    no two emails can teach the seat that a qualifier is part of his signature.
    FALSIFIER: learn from the emails a turn reads, and the block appears and
    the signed yes below confirms."""
    reply, est, broker = loop
    _two_earlier_emails(est)
    _two_earlier_emails(est)
    assert sender_block.block_for(broker.establishment._require_pending(), PAT) == []
    proposal_id = _yes_signed(reply, est, broker)
    assert "s2" not in est._CONFIRMED_SAY
    still_open = broker_kit._call(broker, action="establish_pending", sender=PAT)["pending"]
    assert proposal_id in [r["proposal_id"] for r in still_open]


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


# ---------------------------------------------------------------------------
# The FIRST rule after a deploy (the replay of the firm's real emails,
# 2026-11-20): his block is seeded from the seat mailbox by the root command,
# the from: line is his bare address, his name is the roster's. His plain yes
# with his real block confirms; anything added to it restates; and a rule
# whose replies never become a plain yes is read back at most twice, then SMD.
# ---------------------------------------------------------------------------


class _Mailbox(broker_kit._Graph):
    """The seat mailbox: his 34 earlier emails, each ending with his block."""

    def __init__(self, headers: list[dict]) -> None:
        super().__init__(headers)
        self.history = [
            f"Please pull the file for matter {4400 + n}.\n\n{REAL_BLOCK}" for n in range(34)
        ]

    def _request(self, path, method, body, credential_path=None, role=None):
        if "$filter" in path:
            return {
                "value": [
                    {"id": f"AAMkHist{n}=", "uniqueBody": {"contentType": "text", "content": t}}
                    for n, t in enumerate(self.history)
                ]
            }
        return super()._request(path, method, body, credential_path, role)


def _seeded(broker: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        _Cfg, "authored_person_name", lambda _self, a: ROSTER_NAME if a == PAT else None
    )
    broker.msgraph = _Mailbox([])
    seed = broker.handle(
        {"action": "establish_block_seed", "senders": [PAT]}, peer_pid=1, peer_uid=0
    )
    assert seed["senders"][PAT] == {"lines": 7, "matched": 34, "read": 34, "kept": False}


def _answer_read_back(reply: Any, est: Any, broker: Any, answer: str) -> str:
    proposal_id, _readback = _propose_and_send(reply, est, broker, bare=True)
    broker.msgraph = _Mailbox([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s2", PAT, REPLY)
    est.on_pre_llm_call(
        session_id="s2", sender_id=PAT, user_message=_prompt(answer, REPLY, bare=True)
    )
    return proposal_id


@pytest.mark.parametrize("yes", REAL_YESES)
def test_the_first_yes_after_deploy_with_his_real_block_confirms(loop, monkeypatch, yes) -> None:
    """FALSIFIER: skip the seed, or learn the block from anything but his own
    emails, and every one of these restates: the loop the client was in."""
    reply, est, broker = loop
    _seeded(broker, monkeypatch)
    proposal_id = _answer_read_back(reply, est, broker, f"{yes}\n\n{REAL_BLOCK}")
    assert est._CONFIRMED_SAY.get("s2") == "Got it. That is recorded."
    assert proposal_id not in _ids(broker)


@pytest.mark.parametrize(
    "extra",
    [
        "Only For Craig LLC",
        "12 Point Font Avenue",
        "Patrick Never Doe",
        "_____\nmake it 14",
        "(602) 555-0199 cell",
    ],
)
def test_his_block_with_anything_added_restates(loop, monkeypatch, extra) -> None:
    reply, est, broker = loop
    _seeded(broker, monkeypatch)
    proposal_id = _answer_read_back(reply, est, broker, f"Yes\n{extra}\n\n{REAL_BLOCK}")
    assert "s2" not in est._CONFIRMED_SAY and proposal_id in _ids(broker)


def test_a_rule_whose_replies_are_never_plain_is_read_back_twice_then_handed_to_smd(
    loop, monkeypatch
) -> None:
    """Rounds 0, 1, 2 through the real send record and the real broker: the
    read-back, one restatement, then the stopgap sentence and an owed item for
    SMD, and no third read-back. FALSIFIER: remove readback_rounds.note_owed or
    the cap check in own_rules.own_ask_note, and a third read-back is owed."""
    from workspace_broker import smd_owed

    reply, est, broker = loop
    monkeypatch.setattr(
        _Cfg, "authored_person_name", lambda _self, a: ROSTER_NAME if a == PAT else None
    )
    proposal_id, _readback = _propose_and_send(reply, est, broker, bare=True)
    sent_ids = [SENT, "<CAlive2@mail.firm.example>"]
    for n, sent in enumerate(sent_ids, start=1):
        broker.msgraph = broker_kit._Graph([{"name": "In-Reply-To", "value": sent}])
        inbound_id = f"AAMkRound{n}="
        broker.msgraph.message["id"] = inbound_id
        bound_turn(f"r{n}", PAT, inbound_id)
        context = est.on_pre_llm_call(
            session_id=f"r{n}",
            sender_id=PAT,
            user_message=_prompt("Yes\nletters only", inbound_id, bare=True),
        )["context"]
        if n == 1:  # the restatement: read back again, owed, and sent
            assert "Reply yes to confirm." in context
            origin = inbound.InboundOrigin(
                sender_address=PAT, message_id=inbound_id, inbox_id="op@x"
            )
            reply._relay_sent(
                f"r{n}",
                origin,
                f"k{n}",
                PAT,
                "x",
                ("x", ""),
                (None, None),
                sent_ids[1],
                "msgraph",
                None,
                False,
            )
        else:  # the second non-plain reply: SMD, never a third read-back
            assert "once that's in place" in context and "Reply yes to confirm" not in context
    owed_kinds = [r["kind"] for r in smd_owed.open_items(broker.audit_db_path)["owed"]]
    assert owed_kinds == ["rule_needs_person"]
    conn = broker.establishment._require_pending()._connect()
    try:
        rounds = conn.execute(
            "SELECT COUNT(*) FROM readback_rounds WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert rounds == 2 and proposal_id in _ids(broker)


def _turn(est: Any, broker: Any, body: str, inbound_id: str, sent: str, session: str) -> str:
    broker.msgraph = _Mailbox([{"name": "In-Reply-To", "value": sent}])
    broker.msgraph.message["id"] = inbound_id
    bound_turn(session, PAT, inbound_id)
    out = est.on_pre_llm_call(
        session_id=session, sender_id=PAT, user_message=_prompt(body, inbound_id, bare=True)
    )
    return (out or {}).get("context", "")


def _send_reply(reply: Any, session: str, inbound_id: str, sent: str) -> None:
    origin = inbound.InboundOrigin(sender_address=PAT, message_id=inbound_id, inbox_id="op@x")
    reply._relay_sent(
        session,
        origin,
        "k" + session,
        PAT,
        "x",
        ("x", ""),
        (None, None),
        sent,
        "msgraph",
        None,
        False,
    )


def test_after_the_hand_off_a_restated_rule_is_never_read_back_a_third_time(
    loop, monkeypatch
) -> None:
    """Review 2026-11-20 (MUST-FIX 1): after two read-backs and the hand-off,
    he states the same rule again. The broker returns the same open rule, and
    its proposal answers the stopgap sentence: no read-back is said, none is
    owed, and his plain yes to the reply confirms nothing. FALSIFIER: register
    the read-back before the broker owes it, and the reply must carry "Reply yes
    to confirm." again."""
    reply, est, broker = loop
    _seeded(broker, monkeypatch)
    proposal_id, _ = _propose_and_send(reply, est, broker, bare=True)
    _turn(est, broker, f"Yes\nletters only\n\n{REAL_BLOCK}", "AAMkR1=", SENT, "r1")
    _send_reply(reply, "r1", "AAMkR1=", "<CAlive2@mail.firm.example>")
    handed = _turn(
        est,
        broker,
        f"Yes\nletters only\n\n{REAL_BLOCK}",
        "AAMkR2=",
        "<CAlive2@mail.firm.example>",
        "r2",
    )
    assert "once that's in place" in handed
    bound_turn("r3", PAT, "AAMkR3=")
    est.on_pre_llm_call(
        session_id="r3",
        sender_id=PAT,
        user_message=_prompt(f"From now on: {RULE}", "AAMkR3=", bare=True),
    )
    answer = json.loads(
        est._propose(
            {
                "scope": "person",
                "subject": {"person": PAT},
                "text": RULE,
                "instructed_by": PAT,
                "source_ref": "AAMkR3=",
            },
            session_id="r3",
        )
    )
    assert answer == {"say": "Got it. I'll confirm in this thread once that's in place."}
    says = _required_says(reply, "r3", "AAMkR3=")
    assert says, "the turn registers the stopgap sentence"
    assert not any("Reply yes" in say for say in says)
    _send_reply(reply, "r3", "AAMkR3=", "<CAlive3@mail.firm.example>")
    _turn(est, broker, f"Yes\n\n{REAL_BLOCK}", "AAMkR4=", "<CAlive3@mail.firm.example>", "r4")
    assert "r4" not in est._CONFIRMED_SAY
    assert proposal_id in _ids(broker)
    owed = [r["kind"] for r in smd_owed_items(broker)]
    assert owed == ["rule_needs_person"]  # SMD told once, not again


def smd_owed_items(broker: Any) -> list[dict]:
    from workspace_broker import smd_owed

    return smd_owed.open_items(broker.audit_db_path)["owed"]


def test_a_lapsed_rule_reopens_on_his_signed_yes(loop, monkeypatch) -> None:
    """Review 2026-11-20 (SHOULD-FIX 5): the reopen gate reads his plain yes
    with his seeded block, as every other answer is read. FALSIFIER: judge it
    with no block and his signed yes reopens nothing."""
    reply, est, broker = loop
    _seeded(broker, monkeypatch)
    proposal_id, _ = _propose_and_send(reply, est, broker, bare=True)
    pending = broker.establishment._require_pending()
    conn = pending._connect()
    try:
        with conn:
            conn.execute(
                "UPDATE pending_rules SET expires_at = 1, lapsed_at = ? WHERE proposal_id = ?",
                (time.time(), proposal_id),
            )
    finally:
        conn.close()
    broker.msgraph = _Mailbox([{"name": "In-Reply-To", "value": SENT}])
    bound_turn("s9", PAT, REPLY)
    note = est.own_rules.reopen_note("s9", PAT, _prompt(f"Yes.\n\n{REAL_BLOCK}", REPLY, bare=True))
    assert note is not None and "Reply yes to confirm." in note


RULE_B = "In memos, put the answer in the first line."


def _two_rules_one_at_the_cap(broker: Any) -> tuple[str, str]:
    """Rule A fresh, rule B read back twice already, and one sent read-back
    carrying both (the broker's own records, as two earlier rounds leave them)."""
    from workspace_broker import readback_bindings as rb

    a = broker_kit._propose(broker, RULE)
    b = broker_kit._propose(broker, RULE_B)
    for n in (1, 2):
        assert broker_kit._owe(broker, [b], f"AAMkEarlier{n}=")["ok"] is True
    rb.ReadbackBindings(broker.establishment._require_pending()).record(
        rb.normalize_message_id(SENT), PAT, [a, b], False
    )
    return a, b


def _required_says(reply: Any, session: str, inbound_id: str) -> tuple[str, ...]:
    """What the relay requires of this turn's reply: the loaded reply plugin's own register."""
    return tuple(reply.relay_checks._TURN_SAYS.get(session, inbound_id) or ())


def test_a_restatement_with_one_rule_at_the_cap_hands_off_only_that_one(loop, monkeypatch) -> None:
    """Review 2026-11-20: a reply that is not a plain yes, to a read-back of a
    fresh rule and one already read back twice. Only the capped rule goes to
    SMD; the fresh one is read back again; the note asks for both, and the
    relay requires exactly both, so the reply is not held. FALSIFIER: return at
    the first capped rule (or hand off every rule in the reply) and the fresh
    rule's read-back is missing from the note, or the relay requires a
    read-back the note forbids."""
    from workspace_broker import readback_rounds

    reply, est, broker = loop
    _seeded(broker, monkeypatch)
    a, b = _two_rules_one_at_the_cap(broker)
    context = _turn(est, broker, f"Yes\nletters only\n\n{REAL_BLOCK}", REPLY, SENT, "m1")
    assert "once that's in place" in context
    assert RULE in context and "Reply yes to confirm." in context and RULE_B not in context
    says = _required_says(reply, "m1", REPLY)
    assert len(says) == 2
    assert any("once that's in place" in s for s in says)
    assert any(RULE in s and "Reply yes to confirm." in s and RULE_B not in s for s in says)
    rounds = readback_rounds.rounds_for(broker.establishment._require_pending(), PAT)
    assert rounds["handed_off"] == [b]
    assert a in _ids(broker) and b in _ids(broker)


def test_a_read_back_the_broker_could_not_owe_is_never_sent(loop, monkeypatch) -> None:
    """Review 2026-11-20: the broker refuses the owe for a reason that is not
    the cap (here, a lost socket). Nothing is owed, so nothing asks for a yes:
    the proposal answers say-none and no read-back is the reply's required
    words. FALSIFIER: send the read-back whenever the owe is not capped and the
    reply carries "Reply yes to confirm." that no send will ever bind."""
    reply, est, broker = loop
    real = est._broker_request

    def lost_owe(payload: dict) -> dict:
        if payload.get("action") == est.readback_confirm.VERB_OWED:
            raise OSError("broker socket closed")
        return real(payload)

    monkeypatch.setattr(est, "_broker_request", lost_owe)
    bound_turn("s1", PAT, FIRST)
    est.on_pre_llm_call(
        session_id="s1", sender_id=PAT, user_message=_prompt(f"From now on: {RULE}", FIRST)
    )
    answer = json.loads(
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
    assert answer["say"] == "none" and "Reply yes" not in json.dumps(answer)
    assert not any("Reply yes" in s for s in _required_says(reply, "s1", FIRST))
