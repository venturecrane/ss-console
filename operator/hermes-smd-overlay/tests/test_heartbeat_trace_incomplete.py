"""A reply that went out and then faulted before its record finished is a
``reply_sent:trace_incomplete`` event, never ``reply_failed`` (wave 4, the
fault-after-transmit fix). The email is in the person's inbox: a page saying it
did not reach them would invite SMD to send it a second time.

The reply plugin writes the row as a ``REPLY_SENT`` stamped ``trace_incomplete``
and ``fault`` (``plugins/hermes-smd-reply/relay_sent.py``); this reads it
through ``count_shortfalls`` exactly as the beat does.
"""

from __future__ import annotations

from shared import heartbeat as hb
from tests.test_heartbeat import _INBOUND, _REQ, _reply_row, _shortfalls, _ts


def _trace_row(led, **meta):
    _reply_row(
        led,
        _ts(2),
        "REPLY_SENT",
        in_reply_to=_INBOUND,
        trace_incomplete=True,
        fault="RuntimeError",
        **meta,
    )


def test_a_sent_reply_whose_record_broke_is_trace_incomplete_never_reply_failed(tmp_path):
    """FALSIFIER: drop the trace branch of ``_sent_reply_events`` and this counts 0."""
    facts = _shortfalls(tmp_path, _trace_row, [_REQ])
    assert [e["code"] for e in facts.events] == [hb.REPLY_TRACE_INCOMPLETE_CODE]
    assert hb.REPLY_TRACE_INCOMPLETE_CODE == "reply_sent:trace_incomplete"
    event = facts.events[0]
    assert event["class"] == "failed"
    assert not event["code"].startswith("reply_failed")
    assert event["tool"] == hb.REPLY_RELAY_TOOL


def test_the_trace_row_still_answers_the_request(tmp_path):
    """A REPLY_SENT with an incomplete trace is still the reply: an earlier hold
    of the same email is answered by it and does not page as a failed reply."""

    def build(led):
        _reply_row(led, _ts(3), "REPLY_HELD", message_id=_INBOUND, reason="output_checklist")
        _trace_row(led)

    codes = [e["code"] for e in _shortfalls(tmp_path, build, [_REQ]).events]
    assert codes == [hb.REPLY_TRACE_INCOMPLETE_CODE]


def test_a_trace_row_that_also_declined_is_two_events(tmp_path):
    facts = _shortfalls(tmp_path, lambda led: _trace_row(led, could_not="cannot_find"), [_REQ])
    assert sorted(e["code"] for e in facts.events) == [
        "reply_could_not:cannot_find",
        hb.REPLY_TRACE_INCOMPLETE_CODE,
    ]
    assert len({e["key"] for e in facts.events}) == 2


def test_an_ordinary_sent_reply_is_still_nothing(tmp_path):
    facts = _shortfalls(
        tmp_path, lambda led: _reply_row(led, _ts(2), "REPLY_SENT", in_reply_to=_INBOUND)
    )
    assert facts.count == 0
