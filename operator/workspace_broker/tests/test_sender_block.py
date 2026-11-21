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


def test_fewer_than_three_emails_learn_nothing_and_a_minority_learns_nothing() -> None:
    assert sender_block.learn_from_texts([_email("Only one.")]) == ([], 0)
    assert sender_block.learn_from_texts([_email("One."), _email("Two.")]) == ([], 0)
    texts = [_email("One."), _email("Two.")] + [f"Plain note {n}." for n in range(4)]
    assert sender_block.learn_from_texts(texts) == ([], 0)


def test_html_bodies_read_as_their_lines() -> None:
    sig = "".join(f"<div>{line}</div>" for line in BLOCK)
    bodies = [f"<div>Please pull {n}.</div><div><br></div>{sig}" for n in (1, 2, 3)]
    assert sender_block.learn_from_texts(bodies, html=True)[0] == NORMAL


@pytest.mark.parametrize(
    "markup",
    [
        "<html><head><style>p.MsoNormal{margin:0}</style></head><body><p class=MsoNormal>Yes</p>"
        "<p class=MsoNormal>Cordially,<p class=MsoNormal><b>Patrick Q. Doe</b>"
        "<p class=MsoNormal>Doe &amp; Roe LLP<br>1234 N. Central Ave., Suite 500</body></html>",
        "<p>Cordially,<p>Patrick Q. Doe<p>Doe &amp; Roe LLP",
        "<table><tr><td>Patrick Q. Doe<br>Partner</td><td>Phoenix, AZ</td></tr></table>",
        "<div>Patrick&nbsp;Q.&nbsp;Doe</div><div>Doe &amp; Roe&nbsp;LLP</div>",
    ],
)
def test_the_seed_reads_html_exactly_as_the_seat_reads_his_reply(markup) -> None:
    """NOTE 6 (review 2026-11-20): one converter. The broker's ``mail_text`` is
    a byte copy of the overlay's (``gen-skill-shared --check`` holds it), so his
    signature gives the same lines in the seed as in the reply being judged."""
    from workspace_broker import mail_text

    seat = [sender_block.norm_line(line) for line in mail_text.html_to_text(markup).splitlines() if line.strip()]
    assert sender_block.lines_of(mail_text.html_to_text(markup)) == seat
    assert "p.msonormal{margin:0}" not in seat


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


def _seed(broker, bodies: list[str]) -> dict:
    broker.msgraph = _Mailbox(bodies)
    request = {"action": "establish_block_seed", "senders": [ATTORNEY], "limit": 50}
    return broker.handle(request, peer_pid=1, peer_uid=0)["senders"][ATTORNEY]


def test_the_seed_is_rerunnable_and_reports_counts_never_text(tmp_path) -> None:
    broker = _broker(tmp_path)
    broker.msgraph = _Mailbox([_email(f"Request {n}.") for n in range(4)])
    request = {"action": "establish_block_seed", "senders": [ATTORNEY], "limit": 50}
    first = broker.handle(request, peer_pid=1, peer_uid=0)
    again = broker.handle(request, peer_pid=1, peer_uid=0)
    row = {"lines": 7, "matched": 4, "read": 4, "kept": False}
    assert first == again == {"ok": True, "senders": {ATTORNEY: row}}
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


QUALIFIER = "Only for Craig's matters"


@pytest.mark.parametrize(
    ("name", "later"),
    [
        # The review's poison cases (2026-11-20), now read by a re-seed of his
        # newest emails: none of them changes what a plain yes may end with.
        ("instruction above his block in most emails", lambda n: _email(f"Pull {n}.\n{QUALIFIER}")),
        ("instruction below his block in most emails", lambda n: _email(f"Pull {n}.") + "\n" + QUALIFIER),
        (
            "quoted colleague's signature after his",
            lambda n: _email(f"fyi {n}") + "\nCraig Smith\nAssociate\nDoe & Roe LLP",
        ),
        (
            "a stray unmarked read-back under his block",
            lambda n: _email(f"Yes {n}") + "\nIn memos, no headings.\nReply yes to confirm.\nRiley",
        ),
    ],
)
def test_a_reseed_never_widens_his_block_with_lines_on_either_side(tmp_path, name, later) -> None:
    broker = _broker(tmp_path)
    pending = broker.establishment._require_pending()
    assert _seed(broker, [_email(f"Request {n}.") for n in range(5)])["lines"] == 7
    row = _seed(broker, [later(n) for n in range(5)])
    assert row["kept"] is True, name
    assert sender_block.block_for(pending, ATTORNEY) == NORMAL, name


def test_a_reseed_of_his_phone_replies_learns_no_block_and_keeps_his(tmp_path) -> None:
    """Two (or five) identical short phone replies are not a block of three
    lines in three emails that replaces his: the seed needs MIN_LINES lines."""
    broker = _broker(tmp_path)
    pending = broker.establishment._require_pending()
    _seed(broker, [_email(f"Request {n}.") for n in range(5)])
    row = _seed(broker, ["Thanks,\nPatrick"] * 5)
    assert row["lines"] == 0 and sender_block.block_for(pending, ATTORNEY) == NORMAL


def test_a_new_block_that_does_not_hold_the_old_replaces_it(tmp_path) -> None:
    """He moved firms: SMD re-runs the seed and the new block, in three of his
    newest emails and a majority, replaces the old."""
    broker = _broker(tmp_path)
    pending = broker.establishment._require_pending()
    _seed(broker, [_email(f"Request {n}.") for n in range(5)])
    moved = ["Cordially,", "Patrick Q. Doe", "New & Co LLP", "9 Main St"]
    row = _seed(broker, [_email(f"Request {n}.", moved) for n in range(5)])
    assert row["kept"] is False
    assert sender_block.block_for(pending, ATTORNEY) == [sender_block.norm_line(x) for x in moved]


def test_nothing_learns_a_block_but_the_seed() -> None:
    """No agent verb writes his block: the only writer is the root seed."""
    from workspace_broker import verbs

    names = {v.name for v in verbs.VERBS}
    assert "establish_block_observe" not in names
    assert set(sender_block.HANDLERS) == {"establish_block_seed"}


def test_the_block_rides_every_answer_for_the_sender(tmp_path) -> None:
    broker = _broker(tmp_path)
    _seed(broker, [_email(f"Request {n}.") for n in range(3)])
    broker.msgraph = _Graph([])
    answer = _call(broker, action=rb.VERB_FOR, inbound_message_id=GRAPH_ID, sender=ATTORNEY)
    assert answer["block"] == NORMAL


def test_a_rule_is_read_back_at_most_twice_then_handed_to_smd(tmp_path) -> None:
    """FALSIFIER: drop readback_rounds.note_owed from readback_owed and a third
    read-back is owed: the loop."""
    broker = _broker(tmp_path)
    proposal_id = _propose(broker, "Keep my memos to one page.")
    assert _owe(broker, [proposal_id], "AAMk1=")["ok"] is True
    assert _owe(broker, [proposal_id], "AAMk1=")["ok"] is True  # the same email again is the same round
    assert _owe(broker, [proposal_id], "AAMk2=")["ok"] is True
    assert _owe(broker, [proposal_id], "AAMk3=") == {"ok": False, "capped": [proposal_id]}
    broker.msgraph = _Graph([])
    rounds = _call(broker, action=rb.VERB_FOR, inbound_message_id=GRAPH_ID, sender=ATTORNEY)["rounds"]
    assert rounds == {proposal_id: 2}
    handoff = {"action": "establish_readback_handoff", "sender": ATTORNEY, "proposal_ids": [proposal_id]}
    out = _call(broker, **handoff, request_ref="<r@x>")
    assert out["handed_off"] == [proposal_id] and out["smd_told"] is True
    # Handed off again (a restatement of the same rule): SMD was told once.
    assert _call(broker, **handoff, request_ref="<r2@x>")["smd_told"] is True
    owed = smd_owed.open_items(broker.audit_db_path)
    assert [r["kind"] for r in owed["owed"]] == ["rule_needs_person"]
    assert _owe(broker, [proposal_id], "AAMk1=") == {"ok": False, "capped": [proposal_id]}
