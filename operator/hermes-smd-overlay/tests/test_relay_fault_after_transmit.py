"""A fault after the reply was transmitted (known defect
``fault-after-transmit-leaves-no-trace``, fixed in wave 4).

Once the transport has returned the sent message's id, the reply is in the
firm's inbox. A raise anywhere after that, before the audit and the
bookkeeping finish, used to be swallowed by the relay's catch-all, which skips
a transmitted reply: no REPLY_SENT row, no REPLY_FAILED row, no page, and the
email's id left uncommitted, so a second draft in the same turn relayed the
same email again. Each case here raises once at one call after the send, then
drafts again in the same turn, and asks: the transport was handed bytes exactly
once; there is exactly one REPLY_SENT row and never a REPLY_FAILED (the email
went out), marked ``trace_incomplete`` with the fault's type when the fault came
before the row was written; SMD was paged as a sent reply; and the turn was told
the reply was sent and must not be sent again.

It drives ``_relay_draft`` itself, so it is in the decision corpus's selection
(``tests/decisions/tables.SELECTION``) and its decisions have a golden.
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.matter_file_reply import Prepared
from tests.test_reply import (  # noqa: F401 - _clear_origin and relay_mod are fixtures
    _clear_origin,
    _draft,
    _record_origin,
    relay_mod,
)
from tests.test_request_cards import _decide, _Ledger, _request

SENDER = "greg@whitfield.example"


@pytest.fixture
def seat(relay_mod):  # noqa: F811 - the fixture imported above, by name
    """test_reply's relayable seat: (module, fake D1, what the transport was handed)."""
    return relay_mod


class _Stop(BaseException):  # noqa: N818 - stands for a KeyboardInterrupt or SystemExit
    """Not an Exception: the relay's catch-all lets it through, and it must
    still leave the reply traced and paged on its way out."""


class _Armed:
    """Raises on the first call once the transport has returned, then passes
    every later call through, so the one fault is the only fault."""

    def __init__(self, real: Any, sent: list[dict], error: type[BaseException]) -> None:
        self.real, self.sent, self.error, self.fired = real, sent, error, False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.sent and not self.fired:
            self.fired = True
            raise self.error("raised after the transport returned")
        return self.real(*args, **kwargs)


#: Every call between the transport's return and the end of ``_relay_audit_sent``
#: (and the two bookkeeping calls after it), as (owner attribute, name, whether
#: the REPLY_SENT row was already written when it raised).
AFTER_SEND = (
    ("", "_commit_reply", False),
    ("_ACT_BODIES", "note", False),
    ("", "sender_key", False),
    ("", "_reply_recipient", False),
    ("inbound", "content_digest", False),
    ("relay", "authored_digests", False),
    ("reply_could_not", "metadata", False),
    ("", "_emit_reply_event", False),
    ("request_index", "record_reply_opening", True),
    ("_WORKING_DRAFTS", "take", True),
    ("", "_discard_drafts", True),
)

#: The calls only a reply that carries files makes.
WITH_FILES = (
    ("matter_file_reply", "discard", False),
    ("matter_file_reply", "audit_fields", False),
    ("", "_send_file_continuations", True),
)


def _arm(
    monkeypatch, mod: Any, owner: str, name: str, sent: list[dict], error=RuntimeError
) -> _Armed:
    target = getattr(mod, owner) if owner else mod
    armed = _Armed(getattr(target, name), sent, error)
    monkeypatch.setattr(target, name, armed)
    return armed


def _pages(monkeypatch, mod: Any) -> list[dict]:
    """Every page, and a fresh notice store so this test reads only its own."""
    pages: list[dict] = []
    monkeypatch.setattr(mod, "_notify_hold", lambda **kw: pages.append(kw))
    monkeypatch.setattr(mod, "_HOLD_NOTICES", mod.notice.HoldNoticeStore())
    return pages


def _with_files(monkeypatch, mod: Any) -> list[Any]:
    """The reply carries two batches of files (ADR 0102): the gate's answer
    names them, SMD's review is not authored, and the continuation is noted."""
    real = mod.relay_checks.matter

    def matter(*args: Any) -> Any:
        found = real(*args)
        return None if found is None else (found[0], Prepared(batches=((), ())))

    continued: list[Any] = []
    monkeypatch.setattr(mod.relay_checks, "matter", matter)
    monkeypatch.setattr(mod.matter_file_reply, "review_by_smd", lambda *_a: False)
    monkeypatch.setattr(mod.matter_file_reply, "discard", lambda _s: None)
    monkeypatch.setattr(mod.matter_file_reply, "audit_fields", lambda _b: {})
    monkeypatch.setattr(mod, "_send_file_continuations", lambda *a, **k: continued.append(a))
    return continued


def _draft_twice(mod: Any) -> None:
    for call_id in ("tc-1", "tc-2"):
        mod.on_post_tool_call(
            tool_name="agentmail:create_draft",
            tool_call_id=call_id,
            args=_draft([SENDER]),
            session_id="s1",
        )


def _assert_traced_once(
    mod: Any, d1: Any, sent: list[dict], pages: list[dict], armed: _Armed, row_first: bool
) -> None:
    assert armed.fired, "the injected raise never fired"
    assert len(sent) == 1, f"the same email was relayed {len(sent)} times"
    rows = [(a, m) for a, m in d1.events() if a.startswith("REPLY_")]
    # One REPLY_SENT for the email that went out, then the second draft's
    # duplicate hold: never a REPLY_FAILED, never a second REPLY_SENT.
    assert [a for a, _m in rows] == ["REPLY_SENT", "REPLY_HELD"], rows
    assert rows[1][1]["reason"] == "duplicate_reply"
    meta = rows[0][1]
    assert meta["sent_message_id"] == "msg_sent_1" and meta["in_reply_to"] == "msg_in"
    if row_first:
        assert "trace_incomplete" not in meta and "fault" not in meta
    else:
        assert meta["trace_incomplete"] is True
        assert meta["fault"] == armed.error.__name__
    assert [(p["reason"], p["title"]) for p in pages] == [
        (mod.notice.SENT_TRACE_INCOMPLETE, "reply_sent_trace_incomplete")
    ]
    told = mod._HOLD_NOTICES.take("tc-1")
    assert told is not None and told.reason == mod.notice.SENT_TRACE_INCOMPLETE
    text = mod.notice.render(told)
    assert "YOUR REPLY WAS SENT" in text and "Do not send it again" in text
    assert "NOT SENT" not in text
    _assert_redraft_told_already_delivered(mod)


def _assert_redraft_told_already_delivered(mod: Any) -> None:
    """The second draft's notice is the duplicate hold's own: the sent reply's
    notice never counts as a hold of the email, so this is not a "second hold"
    telling the model to deliver a minimal note."""
    again = mod._HOLD_NOTICES.take("tc-2")
    assert again is not None and again.reason == "duplicate_reply" and again.attempt == 1
    text = mod.notice.render(again)
    assert "already delivered" in text and "Do not redraft" in text
    assert "minimal factual note" not in text


def _no_reply_card(tmp_path, d1: Any) -> list[str]:
    """The request cards the rows this seat wrote would raise for the email,
    an hour after it arrived."""
    ledger = _Ledger(tmp_path / "audit.db")
    for action, meta in d1.events():
        ledger.row(action, 50, **meta)
    return [c["kind"] for c in _decide(ledger, [_request(60, vendor_message_id="msg_in")])]


@pytest.mark.parametrize(
    ("owner", "name", "row_first"), AFTER_SEND, ids=[n for _o, n, _r in AFTER_SEND]
)
def test_a_fault_after_the_send_is_traced_and_never_resent(
    seat, monkeypatch, owner, name, row_first
):
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    armed = _arm(monkeypatch, mod, owner, name, sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    _assert_traced_once(mod, d1, sent, pages, armed, row_first)


@pytest.mark.parametrize(
    ("owner", "name", "row_first"), WITH_FILES, ids=[n for _o, n, _r in WITH_FILES]
)
def test_a_fault_after_a_files_reply_is_traced_and_never_resent(
    seat, monkeypatch, owner, name, row_first
):
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    _with_files(monkeypatch, mod)
    armed = _arm(monkeypatch, mod, owner, name, sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    _assert_traced_once(mod, d1, sent, pages, armed, row_first)


@pytest.mark.parametrize("name", ["_emit_reply_event", "_discard_drafts"])
def test_an_interrupt_after_the_send_is_traced_then_goes_on(seat, monkeypatch, name):
    """A BaseException (an interrupt, a shutdown) is not swallowed: it leaves
    the hook, after the reply is counted as carried, recorded and paged."""
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    armed = _arm(monkeypatch, mod, "", name, sent, error=_Stop)
    _record_origin(sender=SENDER, message_id="msg_in")
    with pytest.raises(_Stop):
        mod.on_post_tool_call(
            tool_name="agentmail:create_draft",
            tool_call_id="tc-1",
            args=_draft([SENDER]),
            session_id="s1",
        )
    mod.on_post_tool_call(
        tool_name="agentmail:create_draft",
        tool_call_id="tc-2",
        args=_draft([SENDER]),
        session_id="s1",
    )
    _assert_traced_once(mod, d1, sent, pages, armed, row_first=name == "_discard_drafts")


def test_a_clean_send_still_pages_nothing(seat, monkeypatch):
    """The falsifier: with no fault, one send, one REPLY_SENT, no page."""
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    assert len(sent) == 1
    assert [a for a, _m in d1.events() if a.startswith("REPLY_")].count("REPLY_SENT") == 1
    assert "REPLY_FAILED" not in [a for a, _m in d1.events()]
    assert pages == []


class _Always:
    """Raises on EVERY call once the transport has returned: a fault that
    recurs, so the trace's own second attempt meets it too."""

    def __init__(self, real: Any, sent: list[dict]) -> None:
        self.real, self.sent, self.calls = real, sent, 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.sent:
            self.calls += 1
            raise IndexError("raised on every call after the transport returned")
        return self.real(*args, **kwargs)


def _always(monkeypatch, target: Any, name: str, sent: list[dict]) -> _Always:
    fault = _Always(getattr(target, name), sent)
    monkeypatch.setattr(target, name, fault)
    return fault


def _the_trace_row(d1: Any) -> dict:
    rows = [(a, m) for a, m in d1.events() if a.startswith("REPLY_")]
    assert [a for a, _m in rows] == ["REPLY_SENT", "REPLY_HELD"], rows
    meta = rows[0][1]
    assert meta["trace_incomplete"] is True and meta["fault"] == "IndexError"
    assert meta["in_reply_to"] == "msg_in" and meta["sent_message_id"] == "msg_sent_1"
    return meta


def _assert_told_and_paged(mod: Any, pages: list[dict]) -> None:
    assert [p["title"] for p in pages] == ["reply_sent_trace_incomplete"]
    told = mod._HOLD_NOTICES.take("tc-1")
    assert told is not None and "YOUR REPLY WAS SENT" in mod.notice.render(told)


def test_a_commit_that_always_faults_still_commits_writes_the_row_and_pages(
    seat, monkeypatch, tmp_path
):
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    fault = _always(monkeypatch, mod, "_commit_reply", sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    assert fault.calls == 2, "the trace tried the commit again"
    assert len(sent) == 1, "the register itself was not committed"
    assert mod._REPLIED.committed("msg_in")
    assert "recipient_class" in _the_trace_row(d1)
    _assert_told_and_paged(mod, pages)
    _assert_redraft_told_already_delivered(mod)
    assert "no_reply" not in _no_reply_card(tmp_path, d1)


def test_metadata_that_always_faults_falls_back_to_the_minimal_row(seat, monkeypatch, tmp_path):
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    fault = _always(monkeypatch, mod.relay_sent, "sent_metadata", sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    assert fault.calls == 2 and len(sent) == 1
    meta = _the_trace_row(d1)
    assert meta["recipient"] == SENDER and meta["adapter"] == "agentmail"
    assert "body_digest" not in meta
    _assert_told_and_paged(mod, pages)
    _assert_redraft_told_already_delivered(mod)
    assert "no_reply" not in _no_reply_card(tmp_path, d1)


def test_file_fields_that_always_fault_fall_back_to_the_minimal_row(seat, monkeypatch, tmp_path):
    """The reviewer's case: a files reply whose file fields raise every time."""
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    _with_files(monkeypatch, mod)
    fault = _always(monkeypatch, mod.matter_file_reply, "audit_fields", sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    assert fault.calls == 2 and len(sent) == 1
    assert "body_digest" not in _the_trace_row(d1)
    _assert_told_and_paged(mod, pages)
    _assert_redraft_told_already_delivered(mod)
    assert "no_reply" not in _no_reply_card(tmp_path, d1)


def test_an_emit_that_always_faults_cannot_write_a_row_so_the_page_carries_it(
    seat, monkeypatch, tmp_path
):
    """No row can be written when the emit itself always faults, so the
    request card will raise its NO REPLY: the Sentry page, which says the reply
    went out and must not be resent, is the trace. It is still committed and
    sent once, and the turn is told it was sent."""
    mod, d1, sent = seat
    pages = _pages(monkeypatch, mod)
    fault = _always(monkeypatch, mod, "_emit_reply_event", sent)
    _record_origin(sender=SENDER, message_id="msg_in")
    _draft_twice(mod)
    assert fault.calls >= 2 and len(sent) == 1
    assert [a for a, _m in d1.events() if a.startswith("REPLY_")] == []
    _assert_told_and_paged(mod, pages)
    assert _no_reply_card(tmp_path, d1) == ["no_reply"]
