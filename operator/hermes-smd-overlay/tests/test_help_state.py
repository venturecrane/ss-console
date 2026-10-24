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
from pathlib import Path

import pytest
import yaml

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
    out = json.loads(plugin._help_signals_handler({"scope": "everyone"}, session_id="s-1"))
    assert out["resolved"] is False
    assert "refused" in out
    # Increment 3: "review" is a known scope, and a firm person's email turn is
    # refused it (test_help_review.py pins who may run it).
    out = json.loads(plugin._help_signals_handler({"scope": "review"}, session_id="s-1"))
    assert out["read"] is False
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


# =========================================================================== #
# Increment 3: the self-improving guide (help_review + help_tools)
#
# What these pin, each with the harm it prevents:
#
# * only an authored tip id moves, by promote/demote/reorder; anything else is
#   refused, so the Operator can never write or invent advice;
# * a promotion needs a fresh, readable review in which the tip's kind is
#   actionable (>= 3 times from >= 2 people in 7 days);
# * every change snapshots the list before it, and at most 3 changes land in
#   any rolling 7 days, enforced in code;
# * SMD's undo restores the newest snapshot; a firm person can neither change
#   nor undo, and the review runs only on its schedule or for SMD with force;
# * the review counts repeat asks, held/failed jobs and noted kinds, carries no
#   address, and a source it could not read makes the whole review unreadable,
#   never an empty one.
# =========================================================================== #


_SMD = "scott@smd.services"
_TIPS = [
    {"id": "vague_request", "when": "vague_request", "text": "Name the matter."},
    {"id": "wants_changes", "when": "wants_changes", "text": "Reply with changes."},
    {"id": "held_identity_check", "when": "held_identity_check", "text": "Answer in a line."},
    {"id": "repeat_ask", "when": "repeat_ask", "text": "Tell me what was wrong."},
]
_TIPS_YAML = (
    Path(__file__).resolve().parents[2] / "skills" / "operator-help" / "references" / "tips.yaml"
)
_DAY = 86400.0


@pytest.fixture
def hr(plugin):
    return plugin.help_review


@pytest.fixture
def ht(plugin):
    return plugin.help_tools


class _AdminCfg(_Cfg):
    admins = [_SMD, "chris@example-firm.test"]

    def sender_on_roster(self, sender):
        return super().sender_on_roster(sender) or sender == _SMD


@pytest.fixture
def admin_roster(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_load_config", lambda: _AdminCfg())


@pytest.fixture
def no_jobs(ht, monkeypatch):
    monkeypatch.setattr(ht, "_job_reader", lambda days: {"rows": []})
    monkeypatch.setattr(ht, "_tips", lambda: [dict(t) for t in _TIPS])


@pytest.fixture
def scheduled(monkeypatch):
    """Session ids starting cron_ resolve to the operator-help routine."""
    from shared import cron_attribution

    def _resolve(session_id, **_):
        if str(session_id).startswith("cron_"):
            return cron_attribution.RoutineIdentity(
                job_id="j",
                job_name="op-managed:operator:operator-help",
                persona="operator",
                skill="operator-help",
            )
        return None

    monkeypatch.setattr(cron_attribution, "resolve_routine", _resolve)


def _review(hr, paths, *, rows=(), tips=None, reader=None):
    return hr.review(
        tips=[dict(t) for t in (tips or _TIPS)],
        job_reader=reader or (lambda days: {"rows": list(rows)}),
        path=paths["state"],
    )


def _note_kind(hs, kind, sender, paths):
    assert hs.record(kind, sender, path=paths["state"])


def _actionable_vague(hs, paths):
    for who in (_SENDER, _SENDER, _OTHER):
        _note_kind(hs, "vague_request", who, paths)


def _snapshots(paths):
    conn = sqlite3.connect(paths["state"])
    try:
        return conn.execute(
            "SELECT action, tip_id, prior_json, after_json, reason_json, undone_at "
            "FROM help_snapshots ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ---- the authored kinds -----------------------------------------------------


def test_every_kind_the_review_can_act_on_is_an_authored_tip_when(hr, hs):
    """FALSIFIER: add a note kind with no tip and the model could note a
    struggle the guide can never answer, and the pin goes red."""

    whens = {t["when"] for t in yaml.safe_load(_TIPS_YAML.read_text())["tips"]}
    assert set(hs.NOTE_STRUGGLE_KINDS) <= whens
    assert {hr.KIND_REPEAT_ASK, hr.KIND_HELD_IDENTITY} <= whens
    # nothing_in_scope is deliberately tipless: it is how a proposal reaches SMD.
    assert hr.KIND_NOTHING_IN_SCOPE not in whens


def test_the_note_tool_accepts_a_struggle_kind_and_records_an_event(plugin, paths, roster, hs):
    _index("m-1")
    _turn("s-1", "m-1")
    assert _note(plugin, "s-1", "vague_request") == {"recorded": True}
    conn = sqlite3.connect(paths["state"])
    try:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM help_events")]
        helped = conn.execute("SELECT helped_at FROM help_senders").fetchone()
    finally:
        conn.close()
    assert kinds == ["vague_request"]
    # A struggle note is not "helped": the pointer stays as it was.
    assert helped is None  # a struggle note is not "helped": no sender row at all


# ---- update: what may change ------------------------------------------------


def test_an_unknown_tip_id_is_refused(hr, hs, paths):
    _actionable_vague(hs, paths)
    rev = _review(hr, paths)
    for bad in ("make_it_up", "", None, "x" * 200, 7):
        out = hr.update("promote", bad, tips=_TIPS, review=rev, path=paths["state"])
        assert out == {"changed": False, "refused": "unknown tip id"}
    assert hr.update("rewrite", "vague_request", tips=_TIPS, review=rev, path=paths["state"])[
        "refused"
    ].startswith("action must be one of")
    assert hr.read_emphasis(path=paths["state"])["emphasis"] == []


def test_a_promotion_needs_an_actionable_kind(hr, hs, paths):
    """FALSIFIER: drop the actionable check and one noisy person reorders the guide."""
    _note_kind(hs, "vague_request", _SENDER, paths)
    _note_kind(hs, "vague_request", _SENDER, paths)
    _note_kind(hs, "vague_request", _SENDER, paths)  # 3 times, ONE person
    rev = _review(hr, paths)
    assert rev["kinds"]["vague_request"] == {
        "occurrences": 3,
        "senders": 1,
        "tips": ["vague_request"],
        "actionable": False,
    }
    out = hr.update("promote", "vague_request", tips=_TIPS, review=rev, path=paths["state"])
    assert out["changed"] is False and "not actionable" in out["refused"]
    out = hr.update("promote", "vague_request", tips=_TIPS, review=None, path=paths["state"])
    assert out["changed"] is False and "no readable review" in out["refused"]


def test_a_promotion_snapshots_the_prior_state_with_its_counts(hr, hs, paths):
    _actionable_vague(hs, paths)
    rev = _review(hr, paths)
    assert rev["actionable"] == ["vague_request"]
    out = hr.update("promote", "vague_request", tips=_TIPS, review=rev, path=paths["state"])
    assert out["changed"] is True
    assert out["emphasis"] == ["vague_request"]
    assert out["changes_left"] == hr.MAX_CHANGES - 1
    [(action, tip, prior, after, reason, undone)] = _snapshots(paths)
    assert (action, tip, json.loads(prior), json.loads(after), undone) == (
        "promote",
        "vague_request",
        [],
        ["vague_request"],
        None,
    )
    assert json.loads(reason) == {"kind": "vague_request", "occurrences": 3, "senders": 2}
    assert hr.read_emphasis(path=paths["state"]) == {"read": True, "emphasis": ["vague_request"]}


def test_demote_and_reorder_move_only_emphasized_tips(hr, paths):
    conn = hr.help_state._connect(paths["state"])
    hr._meta_set(conn, "emphasis", ["vague_request", "wants_changes", "repeat_ask"])
    conn.close()
    st = paths["state"]
    out = hr.update("reorder", "repeat_ask", tips=_TIPS, review=None, position=0, path=st)
    assert out["emphasis"] == ["repeat_ask", "vague_request", "wants_changes"]
    assert hr.update("reorder", "repeat_ask", tips=_TIPS, review=None, position=9, path=st)[
        "refused"
    ].startswith("position must be")
    assert (
        "not emphasized"
        in hr.update("demote", "held_identity_check", tips=_TIPS, review=None, path=st)["refused"]
    )
    out = hr.update("demote", "vague_request", tips=_TIPS, review=None, path=st)
    assert out["emphasis"] == ["repeat_ask", "wants_changes"]


def test_at_most_three_changes_in_a_rolling_week(hr, paths):
    """FALSIFIER: drop the window count and the review can churn the guide daily."""
    conn = hr.help_state._connect(paths["state"])
    hr._meta_set(conn, "emphasis", ["vague_request", "wants_changes", "repeat_ask"])
    conn.close()
    st = paths["state"]
    t0 = time.time()
    for i, pos in enumerate((0, 1, 2)):
        out = hr.update(
            "reorder",
            "repeat_ask",
            tips=_TIPS,
            review=None,
            position=(pos + 1) % 3,
            path=st,
            now=t0 + i,
        )
        assert out["changed"] is True, out
    out = hr.update("demote", "repeat_ask", tips=_TIPS, review=None, path=st, now=t0 + 10)
    assert out == {"changed": False, "refused": "weekly change limit reached", "changes_left": 0}
    # An undo is SMD's act and spends nothing, and does not reopen the window.
    assert hr.undo(path=st, now=t0 + 11)["restored"] is True
    assert (
        hr.update("demote", "repeat_ask", tips=_TIPS, review=None, path=st, now=t0 + 12)["changed"]
        is False
    )
    # Eight days later the window has rolled.
    out = hr.update("demote", "repeat_ask", tips=_TIPS, review=None, path=st, now=t0 + 8 * _DAY)
    assert out["changed"] is True


def test_undo_restores_the_newest_snapshot_then_the_one_before(hr, hs, paths):
    _actionable_vague(hs, paths)
    st = paths["state"]
    rev = _review(hr, paths)
    hr.update("promote", "vague_request", tips=_TIPS, review=rev, path=st)
    hr.update("reorder", "vague_request", tips=_TIPS, review=None, position=0, path=st)  # no change
    conn = hr.help_state._connect(st)
    hr._meta_set(conn, "emphasis", ["vague_request", "wants_changes"])
    conn.close()
    hr.update("demote", "wants_changes", tips=_TIPS, review=None, path=st)
    out = hr.undo(path=st)
    assert out["restored"] is True and out["emphasis"] == ["vague_request", "wants_changes"]
    out = hr.undo(path=st)
    assert out["restored"] is True and out["emphasis"] == []
    assert hr.undo(path=st) == {"restored": False, "reason": "nothing to undo"}
    assert all(row[5] is not None for row in _snapshots(paths))


def test_undo_on_a_seat_with_no_state_is_nothing_to_undo(hr, tmp_path):
    assert hr.undo(path=tmp_path / "none.db") == {"restored": False, "reason": "nothing to undo"}


# ---- review: what is counted ------------------------------------------------


def _request(mid, sender, thread, imid=None, ago=3600.0):
    assert request_index.record_inbound(
        vendor_message_id=mid,
        internet_message_id=imid or f"<{mid}@x>",
        sender=sender,
        subject="s",
        thread_ref=thread,
        received_at=time.time() - ago,
        body="b",
    )


def test_the_review_counts_repeat_asks_jobs_and_notes(hr, hs, paths):
    # Two people each send four emails in their own thread: two repeat asks.
    for i in range(4):
        _request(f"a{i}", _SENDER.lower(), "t-a")
        _request(f"b{i}", _OTHER, "t-b")
    _request("c0", _OTHER, "t-c")  # one email in a thread is not a repeat ask
    rows = [
        {
            "lane": "medchron",
            "state": "held",
            "hold_class": "another_person",
            "request_ref": "<a0@x>",
        },
        {
            "lane": "litigation",
            "state": "held",
            "hold_class": "staff_unresolved",
            "request_ref": "<b0@x>",
        },
        {
            "lane": "medchron",
            "state": "held",
            "hold_class": "which_chronology",
            "request_ref": "<gone@x>",
        },
        {"lane": "demand", "state": "failed", "hold_class": None, "request_ref": "<c0@x>"},
        {
            "lane": "medchron",
            "state": "held",
            "hold_class": "smd_finishing",
            "request_ref": "<a1@x>",
        },
        {
            "lane": "litigation",
            "state": "held",
            "hold_class": "nothing_in_scope",
            "request_ref": "<a2@x>",
        },
        {
            "lane": "litigation",
            "state": "held",
            "hold_class": "nothing_in_scope",
            "request_ref": "<b1@x>",
        },
        {
            "lane": "litigation",
            "state": "held",
            "hold_class": "nothing_in_scope",
            "request_ref": "<b2@x>",
        },
    ]
    _note_kind(hs, "wants_changes", _SENDER, paths)
    out = _review(hr, paths, rows=rows)
    assert out["read"] is True
    assert out["requests"] == {"total": 9, "senders": 2, "repeat_ask_threads": 2}
    assert out["jobs"]["held"] == {"medchron": 3, "litigation": 4}
    assert out["jobs"]["failed"] == {"demand": 1}
    assert out["jobs"]["hold_classes"]["smd_finishing"] == 1
    # Our own hold is counted and never a person struggling.
    assert "smd_finishing" not in out["kinds"]
    held = out["kinds"]["held_identity_check"]
    assert (held["occurrences"], held["senders"], held["actionable"]) == (3, 2, True)
    assert out["kinds"]["repeat_ask"] == {
        "occurrences": 2,
        "senders": 2,
        "tips": ["repeat_ask"],
        "actionable": False,
    }
    assert out["kinds"]["nothing_in_scope"]["actionable"] is True
    assert out["promotable"] == ["held_identity_check"]
    assert out["proposals"] == ["nothing_in_scope"]
    assert out["noted"] == {"wants_changes": 1}
    blob = json.dumps(out)
    for leak in (_SENDER.lower(), _OTHER, "<a0@x>", "t-a"):
        assert leak not in blob


def test_the_review_stores_counts_only_for_the_weekly_note(hr, hs, paths):
    _actionable_vague(hs, paths)
    out = _review(hr, paths)
    assert out["stored"] is True
    conn = sqlite3.connect(paths["state"])
    try:
        stored = json.loads(
            conn.execute("SELECT value FROM help_meta WHERE key='last_review'").fetchone()[0]
        )
    finally:
        conn.close()
    assert stored["actionable"] == ["vague_request"]
    assert set(stored) == {"requests", "jobs", "noted", "kinds", "actionable", "proposals", "at"}


@pytest.mark.parametrize("broken", ["jobs", "requests", "tips"])
def test_a_source_that_cannot_be_read_makes_the_review_unreadable_not_empty(hr, hs, paths, broken):
    """FALSIFIER: swallow the fault into [] and "could not look" reads as
    "nothing to fix", and a promotion could cite it."""
    _actionable_vague(hs, paths)

    def _down(days):
        raise RuntimeError("broker socket error")

    reader = _down if broken == "jobs" else None
    if broken == "requests":
        paths["requests"].write_bytes(b"not a database at all" * 100)
    tips = None if broken == "tips" else _TIPS
    out = hr.review(tips=tips, job_reader=reader or (lambda d: {"rows": []}), path=paths["state"])
    assert out["read"] is False
    assert out["unreadable"] == [broken]
    assert "kinds" not in out and "actionable" not in out
    refused = hr.update("promote", "vague_request", tips=_TIPS, review=out, path=paths["state"])
    assert refused["changed"] is False


def test_a_job_answer_without_rows_is_unreadable(hr, paths):
    out = hr.review(tips=_TIPS, job_reader=lambda d: {"ok": True}, path=paths["state"])
    assert out["read"] is False and out["unreadable"] == ["jobs"]


# ---- who may drive it -------------------------------------------------------


def _upd(plugin, session, **args):
    return json.loads(plugin.help_tools.update_handler(args, session_id=session))


def _undo(plugin, session):
    return json.loads(plugin.help_tools.undo_handler({}, session_id=session))


def _rev(plugin, session, **extra):
    return json.loads(
        plugin._help_signals_handler({"scope": "review", **extra}, session_id=session)
    )


def test_a_firm_person_can_neither_review_nor_change_nor_undo(
    plugin, paths, admin_roster, no_jobs, scheduled, hs
):
    """FALSIFIER: gate on the roster instead of SMD and any client steers the guide."""
    _actionable_vague(hs, paths)
    _index("m-1", sender="chris@example-firm.test")
    _turn("s-1", "m-1", sender="chris@example-firm.test")  # a firm ADMIN, not SMD
    assert "refused" in _rev(plugin, "s-1", force=True)
    assert _upd(plugin, "s-1", action="promote", tip_id="vague_request")["changed"] is False
    assert _undo(plugin, "s-1")["restored"] is False
    assert not _snapshots(paths)


def test_the_scheduled_review_may_review_and_update_but_not_undo(
    plugin, paths, admin_roster, no_jobs, scheduled, hs
):
    _actionable_vague(hs, paths)
    session = "cron_abc123_20261026_141300"
    out = _rev(plugin, session)
    assert out["read"] is True and out["promotable"] == ["vague_request"]
    assert _upd(plugin, session, action="promote", tip_id="vague_request")["changed"] is True
    assert _undo(plugin, session)["restored"] is False
    # A cron turn of another routine is not the review.
    assert "refused" in _rev(plugin, "dm-nothing")


def test_smd_may_force_a_review_and_undo_from_its_own_email(
    plugin, paths, admin_roster, no_jobs, scheduled, hs
):
    _actionable_vague(hs, paths)
    _index("m-9", sender=_SMD)
    _turn("s-9", "m-9", sender=_SMD)
    assert "refused" in _rev(plugin, "s-9")  # no force: the schedule owns the review
    assert _rev(plugin, "s-9", force=True)["read"] is True
    assert _upd(plugin, "s-9", action="promote", tip_id="vague_request")["changed"] is True
    out = _undo(plugin, "s-9")
    assert out["restored"] is True and out["emphasis"] == []


def test_the_update_tool_takes_no_free_text(plugin):
    schema = plugin.help_tools.GUIDE_UPDATE_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "tip_id", "position"}
    assert plugin.help_tools.GUIDE_UNDO_SCHEMA["properties"] == {}
    for text in (
        plugin.help_tools.GUIDE_UPDATE_DESCRIPTION,
        plugin.help_tools.GUIDE_UNDO_DESCRIPTION,
    ):
        assert "\u2014" not in text and "\u2013" not in text


# ---- the help answer leads with the emphasis --------------------------------


def test_the_help_section_carries_the_emphasis_in_order(plugin, paths, hr):
    conn = hr.help_state._connect(paths["state"])
    hr._meta_set(conn, "emphasis", ["wants_changes", "retired_tip", "vague_request"])
    conn.close()
    unreadable: list = []
    tips = [dict(t) for t in _TIPS]
    assert plugin.help_guide._emphasis(tips, unreadable) == ["wants_changes", "vague_request"]
    assert unreadable == []


def test_an_unreadable_emphasis_is_reported_not_empty(plugin, paths):
    paths["state"].parent.mkdir(parents=True, exist_ok=True)
    paths["state"].write_bytes(b"garbage" * 200)
    unreadable: list = []
    assert plugin.help_guide._emphasis([dict(t) for t in _TIPS], unreadable) == []
    assert unreadable == [{"section": "help", "reason": "tip emphasis unreadable"}]


def test_the_weekly_note_reads_what_the_review_writes(hr, hs, paths):
    """The pre_run reads help_state.db by hand (it runs alone, outside the
    plugin). FALSIFIER: rename a column or the last_review key on one side and
    the note silently reports a quiet week."""
    import importlib.util

    pre_run_path = _TIPS_YAML.parents[1] / "pre_run.py"
    spec = importlib.util.spec_from_file_location("operator_help_pre_run_pin", pre_run_path)
    assert spec is not None and spec.loader is not None
    pre_run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pre_run)

    _actionable_vague(hs, paths)
    rev = _review(hr, paths)
    assert hr.update("promote", "vague_request", tips=_TIPS, review=rev, path=paths["state"])[
        "changed"
    ]
    week = pre_run.read_week(paths["state"], time.time())
    assert week["changes"] == [
        {
            "action": "promote",
            "tip_id": "vague_request",
            "reason": {"kind": "vague_request", "occurrences": 3, "senders": 2},
            "undone": False,
        }
    ]
    assert week["last_review"]["actionable"] == ["vague_request"]
    body = pre_run.render_note(week)
    assert "Put the tip vague_request first" in body
