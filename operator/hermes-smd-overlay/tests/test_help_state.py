"""The quiet help pointer's state and tools (ss-console#3423, increment 2).

What these pin, each with the harm it prevents:

* the turn's OWN email never counts as a prior request (else every first email
  reads as "returning" and the pointer never fires);
* the pointer is offered once per person, the struggle offer once per thread,
  recorded by the signals call itself so a skipped note cannot repeat it;
* a routine back-and-forth is not a struggle (four emails in a thread are);
* a display-name sender is the same person as the bare address;
* a message the index never saw (auto-submitted mail) is offered nothing;
* it stops for a person once helped, or after ten requests;
* an unresolved sender gets nothing, and a "could not look" never reads as
  "first time";
* the note tool accepts its enum and nothing else;
* no address is ever written to the state file, and hashes are salted per seat.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from shared import request_index
from tests.conftest import load_plugin

_SENDER = "Chris@Example-Firm.test"
_OTHER = "lee@example-firm.test"
_STRANGER = "someone@elsewhere.test"


@pytest.fixture
def plugin():
    return load_plugin("hermes-smd-initiation")


@pytest.fixture
def hs(plugin):
    return plugin.help_state


@pytest.fixture
def paths(tmp_path, monkeypatch):
    req = tmp_path / "requests.db"
    state = tmp_path / "smd" / "help_state.db"
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(req))
    monkeypatch.setenv("SMD_HELP_STATE_DB_PATH", str(state))
    return {"requests": req, "state": state}


class _Cfg:
    def sender_on_roster(self, sender):
        return sender.lower().endswith("@example-firm.test")


@pytest.fixture
def roster(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_load_config", lambda: _Cfg())


def _index(message_id: str, sender: str = _SENDER, thread: str = "t-1", at: float | None = None):
    assert request_index.record_inbound(
        vendor_message_id=message_id,
        sender=sender.lower(),
        subject="s",
        thread_ref=thread,
        received_at=at,
        body="please do the thing",
    )


def _turn(session: str, message_id: str, sender: str = _SENDER, conversation: str = ""):
    """One verified email recorded, bound and named by THIS turn's prompt."""
    from shared.inbound import SESSION_INBOUND_ORIGIN, InboundOrigin

    SESSION_INBOUND_ORIGIN.record(
        "",
        InboundOrigin(
            sender_address=sender.lower(),
            message_id=message_id,
            inbox_id="op@x",
            conversation_id=conversation,
        ),
    )
    SESSION_INBOUND_ORIGIN.note_turn_prompt(session, message_id)
    SESSION_INBOUND_ORIGIN.bind(session, message_id)


def _signals(plugin, session: str) -> dict:
    return json.loads(plugin._help_signals_handler({"scope": "sender"}, session_id=session))


def _note(plugin, session: str, kind: str) -> dict:
    return json.loads(plugin._help_note_handler({"kind": kind}, session_id=session))


# --------------------------------------------------------------------------- #
# First time vs returning
# --------------------------------------------------------------------------- #


def test_a_first_email_is_first_time_even_though_it_is_already_indexed(plugin, paths, roster):
    """The router indexes the inbound BEFORE the turn. FALSIFIER: count the
    turn's own row and a first-timer reads as returning; the pointer never fires."""
    _index("m-1")
    _turn("s-1", "m-1")
    out = _signals(plugin, "s-1")
    assert out["resolved"] is True
    assert out["first_time"] is True
    assert out["prior_requests"] == 0
    assert out["pointer_due"] is True


def test_a_returning_sender_is_not_first_time(plugin, paths, roster):
    _index("m-0", at=time.time() - 3600)
    _index("m-1")
    _turn("s-1", "m-1")
    out = _signals(plugin, "s-1")
    assert out["first_time"] is False
    assert out["prior_requests"] == 1
    assert out["pointer_due"] is False


def test_another_persons_requests_do_not_make_this_one_returning(plugin, paths, roster):
    _index("m-0", sender=_OTHER, at=time.time() - 3600)
    _index("m-1")
    _turn("s-1", "m-1")
    assert _signals(plugin, "s-1")["first_time"] is True


# --------------------------------------------------------------------------- #
# Offered once
# --------------------------------------------------------------------------- #


def test_the_pointer_is_offered_once(plugin, paths, roster):
    """FALSIFIER: drop pointer_offered from pointer_due and a person whose
    first email was answered keeps getting the line while the index is empty
    of their older mail (it nulls senders after 7 days)."""
    _index("m-1")
    _turn("s-1", "m-1")
    assert _signals(plugin, "s-1")["pointer_due"] is True
    out = _signals(plugin, "s-1")
    assert out["pointer_offered"] is True
    assert out["pointer_due"] is False


def test_the_pointer_is_recorded_by_the_signals_call_not_the_note(plugin, paths, roster):
    """ONCE MEANS ONCE, durably. The model is never asked to remember to note
    the offer: the signals call that reports pointer_due records it. Here no
    note is ever called, and a week later the request index has aged out every
    older email from this person (only the new one is indexed, so they read as
    first-time again): the line is still not due. FALSIFIER: remove the record
    from sender_signals and the second call returns pointer_due again."""
    _index("m-1")
    _turn("s-1", "m-1")
    assert _signals(plugin, "s-1")["pointer_due"] is True
    paths["requests"].unlink()
    _index("m-2")
    _turn("s-2", "m-2")
    out = _signals(plugin, "s-2")
    assert out["first_time"] is True
    assert out["pointer_offered"] is True
    assert out["pointer_due"] is False


def test_the_note_accepts_offer_kinds_as_no_ops(plugin, paths, roster, hs):
    """pointer_offered and struggle_offer are accepted (an older prompt or a
    cautious model may still send them) and change nothing."""
    _index("m-1")
    _turn("s-1", "m-1")
    for kind in ("pointer_offered", "struggle_offer"):
        out = _note(plugin, "s-1", kind)
        assert out["recorded"] is False
        assert out["accepted"] is True
        assert "refused" not in out
    assert not paths["state"].exists()
    assert _signals(plugin, "s-1")["pointer_due"] is True


def test_a_fourth_email_in_a_thread_is_offered_once_per_thread(plugin, paths, roster):
    """Recorded by the signals call: no note is called anywhere here."""
    now = time.time()
    for i in range(1, 4):
        _index(f"m-{i}", at=now - 400 + i)
    _index("m-4", at=now - 100)
    _turn("s-4", "m-4")
    out = _signals(plugin, "s-4")
    assert out["struggle_in_thread"] is True
    assert out["struggle_basis"] == "repeated_messages_in_thread"
    assert out["struggle_offer_due"] is True
    assert _signals(plugin, "s-4")["struggle_offer_due"] is False
    # A fifth email in the same thread: still offered only the once.
    _index("m-5", at=now - 90)
    _turn("s-5", "m-5")
    assert _signals(plugin, "s-5")["struggle_offer_due"] is False

    # A different thread is a different offer.
    for i in range(6, 10):
        _index(f"m-{i}", thread="t-2", at=now - 60 + i)
    _turn("s-9", "m-9")
    assert _signals(plugin, "s-9")["struggle_offer_due"] is True


def test_a_routine_back_and_forth_is_not_a_struggle(plugin, paths, roster):
    """Ask, "make it shorter", "thanks": three emails in a thread are
    conversation, not struggle. FALSIFIER: lower REPEAT_ASK_PRIOR to 2."""
    now = time.time()
    _index("m-1", at=now - 100)
    _index("m-2", at=now - 50)
    _index("m-3", at=now - 10)
    _turn("s-3", "m-3")
    out = _signals(plugin, "s-3")
    assert out["struggle_in_thread"] is False
    assert out["struggle_offer_due"] is False


def test_a_display_name_sender_is_the_same_person(plugin, paths, roster, hs):
    """A writer that stored "Chris Price <Chris@Example.com>" and an origin
    carrying the bare address are one person: in the request count, and in the
    help state's hash. FALSIFIER: compare raw lower-cased strings and the
    display-name rows count as somebody else, so a returning person reads as
    first-time."""
    display = "Chris Price <Chris@Example-Firm.test>"
    assert request_index.record_inbound(
        vendor_message_id="m-0", sender=display, thread_ref="t-1", received_at=time.time() - 60
    )
    _index("m-1")
    _turn("s-1", "m-1")
    out = _signals(plugin, "s-1")
    assert out["prior_requests"] == 1
    assert out["first_time"] is False
    rows = request_index.read_sender_requests(display, 0)
    assert [r["vendor_message_id"] for r in rows] == ["m-0", "m-1"]
    assert hs.record("help_given", display) is True
    assert hs.read_sender(_SENDER, None)["helped"] is True


# --------------------------------------------------------------------------- #
# Stops
# --------------------------------------------------------------------------- #


def test_it_stops_once_helped(plugin, paths, roster):
    _index("m-1")
    _turn("s-1", "m-1")
    assert _note(plugin, "s-1", "help_given") == {"recorded": True}
    out = _signals(plugin, "s-1")
    assert out["helped"] is True
    assert out["stopped"] is True
    assert out["pointer_due"] is False


def test_it_stops_after_ten_requests(plugin, paths, roster, hs):
    now = time.time()
    # Ten prior asks in one thread: a struggle, but the person is experienced.
    for i in range(hs.STOP_AFTER_REQUESTS):
        _index(f"m-{i}", at=now - 1000 + i)
    _index("m-last")
    _turn("s-x", "m-last")
    out = _signals(plugin, "s-x")
    assert out["prior_requests"] == hs.STOP_AFTER_REQUESTS
    assert out["struggle_in_thread"] is True
    assert out["stopped"] is True
    assert out["struggle_offer_due"] is False


def test_nine_prior_requests_do_not_stop_it(plugin, paths, roster, hs):
    now = time.time()
    for i in range(hs.STOP_AFTER_REQUESTS - 1):
        _index(f"m-{i}", at=now - 1000 + i)
    _index("m-last")
    _turn("s-x", "m-last")
    out = _signals(plugin, "s-x")
    assert out["stopped"] is False
    assert out["struggle_offer_due"] is True


# --------------------------------------------------------------------------- #
# Resolution and refusal
# --------------------------------------------------------------------------- #


def test_an_unresolved_sender_is_offered_nothing(plugin, paths, roster):
    """No single email opened the turn (a cron, a wake, an empty session id)."""
    assert _signals(plugin, "") == {"resolved": False}
    assert _signals(plugin, "never-seen") == {"resolved": False}
    assert _note(plugin, "", "pointer_offered")["recorded"] is False


def test_a_second_email_in_the_session_resolves_to_nobody(plugin, paths, roster):
    """bound_this_turn, never the session-sticky opener. FALSIFIER: resolve via
    SESSION_INBOUND_ORIGIN.get and the opener's state answers for a later email."""
    from shared.inbound import SESSION_INBOUND_ORIGIN, InboundOrigin

    _index("m-1")
    _turn("s-1", "m-1")
    SESSION_INBOUND_ORIGIN.record(
        "", InboundOrigin(sender_address=_OTHER, message_id="m-2", inbox_id="op@x")
    )
    SESSION_INBOUND_ORIGIN.note_turn_prompt("s-1", "m-2")
    SESSION_INBOUND_ORIGIN.bind("s-1", "m-2")
    assert _signals(plugin, "s-1") == {"resolved": False}


def test_an_off_roster_sender_is_offered_nothing(plugin, paths, roster):
    _turn("s-1", "m-1", sender=_STRANGER)
    assert _signals(plugin, "s-1") == {"resolved": False}
    assert _note(plugin, "s-1", "pointer_offered")["recorded"] is False


@pytest.mark.parametrize("kind", ["", "pointer", "help", "DROP TABLE", None, 3, "Pointer_Offered"])
def test_the_note_refuses_anything_but_its_enum(plugin, paths, roster, kind):
    _index("m-1")
    _turn("s-1", "m-1")
    out = json.loads(plugin._help_note_handler({"kind": kind}, session_id="s-1"))
    assert out["recorded"] is False
    assert "refused" in out
    assert not paths["state"].exists()


def test_the_signals_refuse_an_unknown_scope(plugin, paths, roster):
    _index("m-1")
    _turn("s-1", "m-1")
    out = json.loads(plugin._help_signals_handler({"scope": "review"}, session_id="s-1"))
    assert out["resolved"] is False
    assert "refused" in out


def test_the_note_ignores_a_named_sender_argument(plugin, paths, roster, hs):
    """The schema has no sender: a forged one in args records nothing for them."""
    _index("m-1")
    _turn("s-1", "m-1")
    plugin._help_note_handler({"kind": "help_given", "sender": _OTHER}, session_id="s-1")
    assert hs.read_sender(_OTHER, None)["helped"] is False
    assert hs.read_sender(_SENDER, None)["helped"] is True


# --------------------------------------------------------------------------- #
# Fail-safe and custody
# --------------------------------------------------------------------------- #


def test_an_unreadable_request_index_is_not_first_time(plugin, paths, roster):
    """ "Could not look" must never read as "none". FALSIFIER: return [] on the
    read fault and every person reads as a first-timer."""
    paths["requests"].write_bytes(b"this is not a sqlite file at all" * 64)
    _turn("s-1", "m-1")
    out = _signals(plugin, "s-1")
    assert out["requests_read"] is False
    assert out["first_time"] is False
    assert out["pointer_due"] is False
    assert out["struggle_offer_due"] is False


def test_an_unreadable_state_file_offers_nothing(plugin, paths, roster):
    _index("m-1")
    paths["state"].parent.mkdir(parents=True, exist_ok=True)
    paths["state"].write_bytes(b"garbage, not sqlite" * 64)
    _turn("s-1", "m-1")
    out = _signals(plugin, "s-1")
    assert out["state_read"] is False
    assert out["pointer_due"] is False


def test_nothing_raises_on_a_broken_state_path(plugin, paths, roster, monkeypatch, hs):
    """A directory where the file should be: every call returns, none raises."""
    paths["state"].mkdir(parents=True)
    _index("m-1")
    _turn("s-1", "m-1")
    assert _note(plugin, "s-1", "help_given") == {"recorded": False}
    assert _signals(plugin, "s-1")["pointer_due"] is False
    assert hs.record("help_given", _SENDER) is False

    def _boom(*_a, **_k):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(hs, "sender_signals", _boom)
    assert _signals(plugin, "s-1") == {"resolved": False}


def test_no_address_or_thread_is_stored(plugin, paths, roster):
    """Only hashes. FALSIFIER: key the table on the address."""
    now = time.time()
    _index("m-0", at=now - 200, thread="conv-AAQkADI0")
    _turn("s-0", "m-0")
    assert _signals(plugin, "s-0")["pointer_due"] is True
    for i in range(1, 4):
        _index(f"m-{i}", at=now - 100 + i, thread="conv-AAQkADI0")
    _turn("s-3", "m-3")
    assert _signals(plugin, "s-3")["struggle_offer_due"] is True
    _note(plugin, "s-3", "help_given")
    raw = paths["state"].read_bytes().lower()
    # The raw file (the sqlite header itself carries an '@' byte, so the
    # address is searched for, not the character): no part of it anywhere.
    assert _SENDER.lower().encode() not in raw
    assert b"chris" not in raw
    assert b"example-firm" not in raw
    assert b"conv-aaqkadi0" not in raw
    conn = sqlite3.connect(paths["state"])
    try:
        rows = conn.execute("SELECT * FROM help_senders").fetchall()
        rows += conn.execute("SELECT * FROM help_threads").fetchall()
    finally:
        conn.close()
    assert rows
    for row in rows:
        for value in row:
            assert "@" not in str(value)


def test_the_output_carries_no_text_or_address(plugin, paths, roster):
    _index("m-1")
    _turn("s-1", "m-1")
    rendered = plugin._help_signals_handler({"scope": "sender"}, session_id="s-1")
    assert "@" not in rendered
    assert "please do the thing" not in rendered
    assert "t-1" not in rendered


def test_old_offers_expire(plugin, paths, roster, hs, monkeypatch):
    _index("m-1")
    _turn("s-1", "m-1")
    assert _signals(plugin, "s-1")["pointer_due"] is True
    conn = sqlite3.connect(paths["state"])
    old = time.time() - (hs.RETENTION_DAYS + 1) * 86400.0
    with conn:
        conn.execute("UPDATE help_senders SET updated_at = ?, pointer_offered_at = ?", (old, old))
    conn.close()
    assert hs.read_sender(_SENDER, None)["pointer_offered"] is False
    # The next write deletes the row outright.
    hs.record("help_given", _OTHER)
    conn = sqlite3.connect(paths["state"])
    try:
        n = conn.execute("SELECT COUNT(*) FROM help_senders").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_auto_submitted_mail_is_offered_nothing(plugin, paths, roster):
    """An out-of-office from a rostered person is never indexed by the router,
    so its turn finds no index row of its own and nothing is due, even though
    the person reads as first-time and the thread already holds four emails.
    FALSIFIER: drop the own-row requirement and an auto-reply gets the pointer."""
    from shared import inbound, inbound_message

    router = load_plugin("hermes-smd-webhook-router")
    envelope = inbound.InboundEnvelope(
        source="agentmail",
        surface="email",
        ingested_at="2026-10-23T00:00:00Z",
        trust_class=inbound.TRUST_CLASS_INTERNAL,
    )

    def _dto(mid: str, auto: bool):
        return inbound_message.InboundMessage(
            provider="agentmail",
            mailbox="op@x",
            message_id=mid,
            thread_ref="conv-9",
            from_addr=_SENDER.lower(),
            to=["op@x"],
            cc=[],
            subject="Automatic reply",
            body_text="I am out of the office",
            received_at="2026-10-23T00:00:00Z",
            provider_refs={},
            auto_submitted=auto,
        )

    router._index_request(envelope, _dto("m-auto", True))
    assert request_index.read_sender_requests(_SENDER, 0) == []
    _turn("s-1", "m-auto", conversation="conv-9")
    out = _signals(plugin, "s-1")
    assert out["this_message_indexed"] is False
    assert out["first_time"] is True
    assert out["pointer_due"] is False

    # Four person-written emails in the thread, then an auto-reply in it.
    for i in range(4):
        router._index_request(envelope, _dto(f"m-{i}", False))
    assert len(request_index.read_sender_requests(_SENDER, 0)) == 4
    router._index_request(envelope, _dto("m-auto-2", True))
    _turn("s-2", "m-auto-2", conversation="conv-9")
    out = _signals(plugin, "s-2")
    assert out["this_message_indexed"] is False
    assert out["struggle_offer_due"] is False
    assert out["pointer_due"] is False


def test_hashes_are_salted_per_seat(tmp_path, hs):
    """Two seats' files hash the same person differently, so a hash cannot be
    matched against a list of known addresses without the file's own salt.
    FALSIFIER: hash without the salt and the two keys are equal."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    assert hs.record("help_given", _SENDER, path=a)
    assert hs.record("help_given", _SENDER, path=b)
    keys = []
    for p in (a, b):
        conn = sqlite3.connect(p)
        try:
            salt = conn.execute("SELECT value FROM help_meta WHERE key='salt'").fetchone()[0]
            keys.append(conn.execute("SELECT sender_hash FROM help_senders").fetchone()[0])
        finally:
            conn.close()
        assert len(salt) == 64
        assert hs.sender_key(_SENDER, salt) == keys[-1]
    assert keys[0] != keys[1]
    assert hs.read_sender(_SENDER, None, path=a)["helped"] is True


def test_the_lookback_is_the_index_sender_custody(hs):
    """Prior requests are counted over exactly the window the index can
    attribute a request to a person, never a longer number it cannot honor."""
    assert hs.LOOKBACK_DAYS == request_index.TEXT_RETENTION_DAYS


def test_the_narrow_request_reader_returns_no_text(paths):
    _index("m-1")
    rows = request_index.read_sender_requests(_SENDER, 0)
    assert rows == [
        {"vendor_message_id": "m-1", "received_at": rows[0]["received_at"], "thread_ref": "t-1"}
    ]
    assert request_index.read_sender_requests("", 0) == []
    assert request_index.read_sender_requests(_OTHER, 0) == []
