"""A representative's own stored figures and dates are quotable in their reply.

WHY (ss-console #3946, gate 13). The reply checks hold a reply for any ``$``
followed by a digit and for an identifier (a date) nothing read this session
carries. A representative asks "what did I note about Acme?" and the honest
answer repeats their own lead value and follow-up date. So a read from their
walled store does two things, both in code:

* the stored text, figures and dates as written, is recorded as this turn's
  provenance, keyed by the session id the handler was handed;
* the model is handed dollar figures in words (``40,000 dollars``), so a reply
  that repeats the value in its own phrasing does not trip a marker meant for
  invented prices.

Driven through the real handler on a worker thread and the real reply gate
(``hermes-smd-reply`` ``relay.gate_body``) with the register the reply plugin
reads. FALSIFIER: on the store as it was (no words, no handler seeding), the
read hands the model ``$40,000`` and the first assertion fails; and a reply
quoting a figure that was never read is still held (last test), so the gate
under test is live.
"""

from __future__ import annotations

import json

from shared import provenance
from tests.conftest import load_plugin
from tests.test_record_store_partition import (  # noqa: F401 - the seat fixture
    NOTES,
    SA,
    _call,
    _two_people,
    seat,
)

LEAD = "---\nstage: warm\n---\nAcme: deal around $40,000, follow up 2026-12-03.\n"


def _gate(body: str):
    relay = load_plugin("hermes-smd-reply").relay
    register = provenance.register_for(SA)
    return relay.gate_body(
        body,
        vertical=None,
        cohort="guide-test",
        internal_recipient=True,
        allowed_case_names=register.captions(),
        allowed_money=register.money(),
    )


def test_the_model_reads_figures_in_words_and_the_reply_is_not_held(seat) -> None:  # noqa: F811 - the imported fixture
    _two_people(seat)
    wrote = _call(
        seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": LEAD}, SA
    )
    assert wrote.get("created") is True, wrote
    read = _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SA)
    content = read["content"]
    assert "$40,000" not in content and "40,000 dollars" in content
    assert "2026-12-03" in content
    # Stored on disk exactly as written.
    assert "$40,000" in (seat["notes"].glob("*/acme.md").__next__()).read_text()
    for reply in (
        "Your Acme lead is warm: around 40,000 dollars, follow up on 2026-12-03.",
        "Your Acme lead is around $40,000.",
    ):
        verdict = _gate(reply)
        assert verdict.allowed, (reply, verdict)


def test_the_read_seeds_the_turns_register_with_the_stored_figure_and_date(seat) -> None:  # noqa: F811 - the imported fixture
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": LEAD}, SA)
    _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SA)
    register = provenance.register_for(SA)
    assert register.money(), "the stored figure was not recorded as read"
    assert "40000" in json.dumps(sorted(register.money())).replace(",", "")
    from shared import identifier_filter

    reply = "Follow up with Acme on 2026-12-03."
    assert identifier_filter.unverified_identifiers(reply, register) == []
    # The falsifier for the date: one the record never carried is unverified.
    other = identifier_filter.unverified_identifiers("Follow up on 2026-12-09.", register)
    assert [h.kind for h in other] == [identifier_filter.IdKind.DATE]


def test_a_figure_nobody_read_is_still_held(seat) -> None:  # noqa: F811 - the imported fixture
    _two_people(seat)
    verdict = _gate("Your Acme lead is around $95,000.")
    assert not verdict.allowed
    assert "fabrication" in verdict.reason
