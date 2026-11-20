"""A sender's signature block, learned whole from his own emails, and the
two-read-back cap (sender_block.py, readback_rounds.py; 2026-11-20).

Every name, address and line is invented. The block's shape follows the firm
attorney's real one (closing, full name with a middle initial, firm line, street,
city, two phones with the label after the number) with placeholder values.
"""

from __future__ import annotations

import pytest

from workspace_broker import readback_bindings as rb
from workspace_broker import sender_block, smd_owed
from workspace_broker.establishment import EstablishmentValidationError
from workspace_broker.tests.test_readback_bindings import (
    ATTORNEY,
    GRAPH_ID,
    _broker,
    _call,
    _Graph,
    _owe,
    _propose,
)

BLOCK = [
    "Cordially,",
    "Patrick Q. Doe",
    "Doe & Roe LLP",
    "1234 N. Central Ave., Suite 500",
    "Phoenix, AZ 85004",
    "(602) 555-0100 office",
    "(602) 555-0101 fax",
]
NORMAL = [sender_block.norm_line(line) for line in BLOCK]


def _email(body: str, block: list[str] = BLOCK) -> str:
    return body + "\n\n" + "\n".join(block)


def test_the_block_is_the_longest_run_ending_a_majority_of_his_emails() -> None:
    texts = [_email(f"Please pull file {n}.") for n in range(5)] + ["Sent quickly, no signature."]
    block, matched = sender_block.learn_from_texts(texts)
    assert block == NORMAL and matched == 5


def test_one_email_learns_nothing_and_a_minority_learns_nothing() -> None:
    assert sender_block.learn_from_texts([_email("Only one.")]) == ([], 0)
    texts = [_email("One."), _email("Two.")] + [f"Plain note {n}." for n in range(4)]
    assert sender_block.learn_from_texts(texts) == ([], 0)


def test_html_bodies_read_as_their_lines() -> None:
    sig = "".join(f"<div>{line}</div>" for line in BLOCK)
    first, second = (f"<div>Please pull {n}.</div><div><br></div>{sig}" for n in (1, 2))
    assert sender_block.learn_from_texts([first, second], html=True)[0] == NORMAL


class _Mailbox(_Graph):
    """Graph list read: the sender's newest messages with their uniqueBody."""

    def __init__(self, bodies: list[str]) -> None:
        super().__init__([])
        self.bodies = bodies

    def _request(self, path, method, body, credential_path=None, role=None):
        self.paths.append(path)
        return {
            "value": [
                {"id": f"AAMk{n}=", "uniqueBody": {"contentType": "text", "content": b}}
                for n, b in enumerate(self.bodies)
            ]
        }


def test_the_seed_is_rerunnable_and_reports_counts_never_text(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.msgraph = _Mailbox([_email(f"Request {n}.") for n in range(4)])
    request = {"action": "establish_block_seed", "senders": [ATTORNEY], "limit": 50}
    first = broker.handle(request, peer_pid=1, peer_uid=0)
    again = broker.handle(request, peer_pid=1, peer_uid=0)
    assert first == again == {"ok": True, "senders": {ATTORNEY: {"lines": 7, "matched": 4, "read": 4}}}
    assert "doe & roe llp" not in str(first)
    pending = broker.establishment._require_pending()
    assert sender_block.block_for(pending, ATTORNEY) == NORMAL
    # The agent may not run it.
    with pytest.raises(PermissionError):
        _call(broker, **request)


def test_the_seed_command_prints_counts_per_address(tmp_path, monkeypatch, capsys) -> None:
    from workspace_broker import broker_socket

    reply = b'{"ok": true, "senders": {"pat@firm.example": {"lines": 7, "matched": 34, "read": 40}}}\n'
    monkeypatch.setattr(broker_socket, "request", lambda *a, **k: bytearray(reply))
    assert sender_block.main(["seed", "--sender", ATTORNEY, "--socket", str(tmp_path / "s")]) == 0
    assert capsys.readouterr().out == "pat@firm.example: block 7 lines, matched 34 of 40 emails\n"


def test_an_observed_email_learns_once_two_end_alike_and_a_line_on_top_never_joins(tmp_path) -> None:
    broker = _broker(tmp_path)
    pending = broker.establishment._require_pending()
    lines = [sender_block.norm_line(x) for x in ("Please pull it.", *BLOCK)]
    assert sender_block.observe_lines(pending, ATTORNEY, "m1", lines) is False
    assert sender_block.observe_lines(pending, ATTORNEY, "m2", ["another request.", *NORMAL]) is True
    assert sender_block.block_for(pending, ATTORNEY) == NORMAL
    # Poisoning: an instruction written twice above his block never joins it.
    poisoned = ["only for craig llc", *NORMAL]
    sender_block.observe_lines(pending, ATTORNEY, "m3", ["x.", *poisoned])
    sender_block.observe_lines(pending, ATTORNEY, "m4", ["y.", *poisoned])
    assert sender_block.block_for(pending, ATTORNEY) == NORMAL
    # A genuinely new block (he moved firms), seen twice, replaces it.
    moved = ["cordially,", "patrick q. doe", "new & co llp", "9 main st"]
    sender_block.observe_lines(pending, ATTORNEY, "m5", ["a.", *moved])
    assert sender_block.observe_lines(pending, ATTORNEY, "m6", ["b.", *moved]) is True
    assert sender_block.block_for(pending, ATTORNEY) == moved


def test_the_block_rides_every_answer_for_the_sender(tmp_path) -> None:
    broker = _broker(tmp_path)
    pending = broker.establishment._require_pending()
    sender_block.observe_lines(pending, ATTORNEY, "m1", ["a.", *NORMAL])
    sender_block.observe_lines(pending, ATTORNEY, "m2", ["b.", *NORMAL])
    broker.msgraph = _Graph([])
    answer = _call(broker, action=rb.VERB_FOR, inbound_message_id=GRAPH_ID, sender=ATTORNEY)
    assert answer["block"] == NORMAL


def test_a_rule_is_read_back_at_most_twice_then_handed_to_smd(tmp_path) -> None:
    """FALSIFIER: drop readback_rounds.note_owed from readback_owed and a third
    read-back is owed: the loop."""
    broker = _broker(tmp_path)
    proposal_id = _propose(broker, "Keep my memos to one page.")
    _owe(broker, [proposal_id], "AAMk1=")
    _owe(broker, [proposal_id], "AAMk1=")  # the same email again is the same round
    _owe(broker, [proposal_id], "AAMk2=")
    with pytest.raises(EstablishmentValidationError, match="read-backs"):
        _owe(broker, [proposal_id], "AAMk3=")
    broker.msgraph = _Graph([])
    rounds = _call(broker, action=rb.VERB_FOR, inbound_message_id=GRAPH_ID, sender=ATTORNEY)["rounds"]
    assert rounds == {proposal_id: 2}
    out = _call(
        broker, action="establish_readback_handoff", sender=ATTORNEY, proposal_ids=[proposal_id], request_ref="<r@x>"
    )
    assert out["handed_off"] == [proposal_id] and out["smd_told"] is True
    owed = smd_owed.open_items(broker.audit_db_path)
    assert [r["kind"] for r in owed["owed"]] == ["rule_needs_person"]
    with pytest.raises(EstablishmentValidationError, match="handed to SMD"):
        _owe(broker, [proposal_id], "AAMk1=")


def test_the_observe_verb_keeps_only_hashes_and_learns_the_block(tmp_path) -> None:
    """``establish_block_observe``, as the seat calls it: his last lines in, his
    block learned once two emails end alike; an email's lines are never kept."""
    broker = _broker(tmp_path)
    for n in (1, 2):
        out = _call(
            broker, action="establish_block_observe", sender=ATTORNEY, message_id=f"m{n}", lines=[f"req {n}.", *BLOCK]
        )
        assert out["ok"] is True
    pending = broker.establishment._require_pending()
    assert sender_block.block_for(pending, ATTORNEY) == NORMAL
    conn = pending._connect()
    try:
        stored = " ".join(r[0] for r in conn.execute("SELECT hashes FROM sender_tails").fetchall())
    finally:
        conn.close()
    assert "req 1" not in stored and "doe & roe" not in stored
    with pytest.raises(EstablishmentValidationError):
        _call(broker, action="establish_block_observe", sender=ATTORNEY, message_id="m3", lines=["x"] * 30)
