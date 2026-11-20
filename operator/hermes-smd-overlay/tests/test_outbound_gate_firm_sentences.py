"""A firm's own rule sentence keeps its citation through the outbound scan (R2.4).

An attorney's rule named a code section and a case; the broker stored his
sentence and rendered it in the read-back, and the citation scan then refused
the read-back, so the Operator cut the citation out of HIS rule. The exemption is
narrow: the EXACT broker-rendered sentence is removed before the citation scan
only. The same citation in words the model wrote around it is refused exactly as
before, and every other check reads the whole body. The section number and the
case name are invented.
"""

from __future__ import annotations

import pytest

from shared import outbound_gate
from tests.conftest import load_plugin

RULE = "In discovery responses, object under A.R.S. § 12-9999 and cite Example v. Sample."
LAW = "law-firm"


@pytest.fixture(autouse=True)
def _fresh():
    outbound_gate._reset_firm_sentences_for_tests()
    yield
    outbound_gate._reset_firm_sentences_for_tests()


def test_the_rendered_sentence_with_its_citation_passes():
    body = f"Here is the rule as I have it:\n\n{RULE}\n\nReply yes to confirm."
    assert outbound_gate.evaluate(body, None, LAW, firm_sentences=(RULE,)).allowed


def test_the_same_citation_in_model_written_text_is_refused():
    body = f"{RULE}\n\nThat means every response will rely on A.R.S. § 12-9999."
    decision = outbound_gate.evaluate(body, None, LAW, firm_sentences=(RULE,))
    assert not decision.allowed and decision.tier == "tier2_citation"


def test_a_paraphrase_is_not_the_sentence():
    paraphrase = "In discovery responses you want objections under A.R.S. § 12-9999."
    assert not outbound_gate.evaluate(paraphrase, None, LAW, firm_sentences=(RULE,)).allowed


def test_a_turn_with_no_firm_sentences_behaves_as_before():
    body = f"Here is the rule:\n\n{RULE}"
    assert not outbound_gate.evaluate(body, None, LAW, firm_sentences=()).allowed
    # No argument is no exemption.
    assert not outbound_gate.evaluate(body, None, LAW).allowed


def test_a_rewrapped_body_still_matches():
    wrapped = RULE.replace(" and cite", "\nand cite")
    assert outbound_gate.evaluate(wrapped, None, LAW, firm_sentences=(RULE,)).allowed


def test_the_exemption_touches_the_citation_scan_alone():
    """A Tier-1 marker inside the firm's sentence still blocks: only Tier-2
    reads the text with the sentence removed."""
    figure = "In demand letters, always ask for $50,000 in the first line."
    decision = outbound_gate.evaluate(figure, None, LAW, firm_sentences=(figure,))
    assert not decision.allowed and decision.tier == "tier1_marker"


def test_noted_sentences_are_per_session_and_never_cross_to_another():
    """Review: a process-wide default exempted any session's sentence in any
    other session's reply. Now a session's sentences are its own."""
    outbound_gate.note_firm_sentences([RULE], session_id="sess-a")
    assert outbound_gate.firm_sentences_for("sess-a") == (RULE,)
    assert outbound_gate.firm_sentences_for("sess-b") == ()
    assert not outbound_gate.evaluate(RULE, None, LAW).allowed
    assert not outbound_gate.evaluate(
        RULE, None, LAW, firm_sentences=outbound_gate.firm_sentences_for("sess-b")
    ).allowed


def test_the_reply_relay_passes_its_own_sessions_sentences():
    relay = load_plugin("hermes-smd-reply").relay
    outbound_gate.note_firm_sentences([RULE], session_id="sess-a")
    mine = outbound_gate.firm_sentences_for("sess-a")
    assert relay.gate_body(
        RULE, vertical=LAW, cohort=None, internal_recipient=True, firm_sentences=mine
    ).allowed
    assert not relay.gate_body(RULE, vertical=LAW, cohort=None, internal_recipient=True).allowed


def test_noted_sentences_expire():
    outbound_gate.note_firm_sentences([RULE], session_id="sess-a", now=1000.0)
    later = 1000.0 + outbound_gate.FIRM_SENTENCE_TTL_SECONDS + 1
    assert outbound_gate.firm_sentences_for("sess-a", now=later) == ()
