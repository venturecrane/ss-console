"""Request cards say who asked by name, what they asked, the reply, and what ran.

Captain, 2026-10-27: the email for each request the Operator receives must
clearly state what the request is, who made it, and the Operator's response and
action. Before this, a card carried the subject line, the first ~240 characters
of the reply, the bare address, and a raw tool list. These tests pin the seat
half: the index keeps the whole request and the whole reply (under the same
custody as the openings), every card carries the firm's AUTHORED name for the
sender and never a guess, and the replied card tags each tool with the trust
gate's action class and its call count.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from shared import request_cards as rc
from shared import request_index as ri
from tests.test_request_cards import NOW, VID, _epoch, _Ledger, _request, _sent


@pytest.fixture
def ledger(tmp_path) -> _Ledger:
    return _Ledger(tmp_path / "audit.db")


def _decide(ledger: _Ledger, requests, done=(), **kwargs) -> list[dict]:
    conn = ledger.ro()
    try:
        return rc.decide(requests, conn, set(done), NOW, **kwargs)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def test_message_text_keeps_paragraphs_and_drops_the_salutation() -> None:
    body = "Hi Sam,\n\nCan you redo the list?\n\n\n\nOne   sheet per case.\n"
    assert ri.message_text(body) == "Can you redo the list?\n\nOne sheet per case."
    assert ri.message_text("") is None and ri.message_text(None) is None


def test_message_text_cuts_a_long_body_at_a_word_and_keeps_far_more_than_the_opening() -> None:
    body = "word " * 2000
    text = ri.message_text(body)
    assert text is not None and text.endswith("...")
    assert ri.OPENING_CHARS * 10 < len(text) <= ri.TEXT_CHARS + 3
    # FALSIFIER: the opening is what a card carried before; the text is not it.
    assert len(ri.reply_opening(body) or "") < len(text)


def test_the_index_keeps_the_whole_request_and_the_whole_first_reply(tmp_path) -> None:
    path = str(tmp_path / "r.db")
    long_ask = "Please redo the litigation list. " * 20
    first = "First answer. " * 30
    ri.record_inbound(vendor_message_id=VID, sender="c@firm", subject="s", body=long_ask, path=path)
    ri.record_reply_opening(VID, first, sent_message_id="sent-1", path=path)
    ri.record_reply_opening(VID, "Second answer.", sent_message_id="sent-2", path=path)
    row = ri.read_requests(0, path=path)[0]
    assert row["request_text"] == long_ask.strip()
    # The request row keeps the FIRST reply; a later one never overwrites it.
    assert row["reply_text"] == first.strip()
    assert len(row["reply_opening"]) < len(row["reply_text"])
    assert ri.read_reply_texts(0, path=path) == {
        "sent-1": first.strip(),
        "sent-2": "Second answer.",
    }


def test_custody_nulls_the_whole_texts_with_the_rest(tmp_path) -> None:
    path = str(tmp_path / "r.db")
    old = time.time() - (ri.TEXT_RETENTION_DAYS + 1) * 86400
    ri.record_inbound(vendor_message_id="old", sender="a@b", body="ask", path=path)
    ri.record_reply_opening("old", "answer", sent_message_id="sent-old", path=path)
    conn = sqlite3.connect(path)  # age both rows past the text window
    conn.execute("UPDATE requests SET received_at=?", (old,))
    conn.execute("UPDATE replies SET sent_at=?", (old,))
    conn.commit()
    conn.close()
    before = ri.read_requests(0, path=path)[0]
    assert before["request_text"] == "ask" and before["reply_text"] == "answer"
    ri.record_inbound(vendor_message_id="new", sender="a@b", path=path)  # any write runs custody
    row = next(r for r in ri.read_requests(0, path=path) if r["vendor_message_id"] == "old")
    assert row["request_text"] is None and row["reply_text"] is None
    assert ri.read_reply_texts(0, path=path) == {"sent-old": None}


def test_an_index_whose_replies_table_predates_the_text_column_gains_it(tmp_path) -> None:
    path = str(tmp_path / "r.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE replies (sent_message_id TEXT PRIMARY KEY, in_reply_to TEXT,"
        " sent_at REAL NOT NULL, opening TEXT)"
    )
    conn.execute("INSERT INTO replies VALUES ('sent-0', 'x', ?, 'old')", (time.time(),))
    conn.commit()
    conn.close()
    # The reader (the gate, read-only) never assumes the writer has caught up.
    assert ri.read_reply_texts(0, path=path) == {}
    assert ri.converge(path=path) is True
    assert ri.read_reply_texts(0, path=path) == {"sent-0": None}


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------


def test_a_replied_card_says_who_by_name_what_was_asked_the_reply_and_what_ran(ledger) -> None:
    ledger.call(55, "mcp_msgraph_mail_read_message")
    ledger.call(54, "mcp_msgraph_mail_read_message")
    ledger.call(53, "operations_request")
    ledger.call(52, "not_a_real_tool")
    ledger.reply(50)
    req = _request(60, request_text="Redo the list.\n\nOne sheet per case.", reply_text="I cannot.")
    card = _decide(
        ledger,
        [req],
        name_of=lambda a: "Pat Paralegal" if a == "paralegal@firm.example" else None,
    )[0]
    assert card["kind"] == "replied"
    assert card["who_name"] == "Pat Paralegal"
    assert card["request_text"] == "Redo the list.\n\nOne sheet per case."
    assert card["reply_text"] == "I cannot."
    assert card["actions"] == [
        {"tool": "mcp_msgraph_mail_read_message", "class": "read", "count": 2},
        {"tool": "operations_request", "class": "internal_write", "count": 1},
        {"tool": "not_a_real_tool", "class": "unmapped", "count": 1},
    ]


def test_no_authored_name_or_a_failing_lookup_sends_no_name_never_a_guess(ledger) -> None:
    ledger.reply(50)
    for name_of in (None, lambda _a: None):
        assert _decide(ledger, [_request(60)], name_of=name_of)[0]["who_name"] is None

    def boom(_a):
        raise RuntimeError("config unreadable")

    cards = _decide(ledger, [_request(60)], name_of=boom)
    assert len(cards) == 1 and cards[0]["who_name"] is None


def test_every_card_kind_carries_who_name_and_the_request_text(ledger) -> None:
    ledger.reply(50)
    cards = _decide(
        ledger,
        [
            _request(60, request_text="the ask"),
            _request(90, vendor_message_id="other", internet_message_id=None, request_text="?"),
        ],
        name_of=lambda _a: "Pat",
    )
    assert {c["kind"] for c in cards} >= {"replied", "no_reply"}
    assert all(c["who_name"] == "Pat" for c in cards)
    assert next(c for c in cards if c["kind"] == "no_reply")["request_text"] == "?"
    assert next(c for c in cards if c["kind"] == "replied")["request_text"] == "the ask"


def test_a_followup_card_quotes_the_whole_later_reply(ledger) -> None:
    _sent(ledger, 60, "sent-1")
    _sent(ledger, 10, "sent-2")
    landed = {rc.card_key(VID, "replied"): _epoch(54)}
    whole = "Correction: both records are unreadable.\n\nI will reorder them."
    cards = _decide(
        ledger,
        [_request(61)],
        done=landed,
        done_at=landed,
        openings={"sent-1": "first", "sent-2": "Correction..."},
        texts={"sent-2": whole},
    )
    followups = [c for c in cards if c["kind"] == "followup"]
    assert len(followups) == 1 and followups[0]["reply_text"] == whole
