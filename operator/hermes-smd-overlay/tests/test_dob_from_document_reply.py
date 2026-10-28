"""A date of birth read from a matter document may be quoted back to the
requester (2026-10-27, the chronology skill's missing-contact-DOB rule).

The medical-chronology-maintainer skill now has the Operator read a client's
labeled date of birth with ``read_document`` when the Smokeball contact lacks
one, and reply to the requester with the date to confirm. That reply is an
external send, and the identifier gate refuses a date the session did not read.
These tests pin the two halves through the real hooks: a date quoted as the
document writes it passes (on the text-layer road and on the scan road), and a
date the Operator reformatted or computed is still refused.
"""

from __future__ import annotations

import json

import pytest

from shared import provenance
from tests.conftest import load_plugin

# Synthetic: no client data. Two clients sharing a surname, a decoy third party.
HEADER_PAGE = (
    "KAISER PERMANENTE  Patient: EXAMPLE, ANNA  DOB: 04/17/1951  MRN 000111\n"
    "Progress note. Patient seen for neck pain after motor vehicle collision.\n"
)
SCAN_PAGE = (
    "TRAFFIC COLLISION REPORT  Party 2: EXAMPLE, PETER  D.O.B. 11/03/1977\n"
    "Party 1: SAMPLE, DORA  D.O.B. 02/28/1980\n"
)


def _read(plugin, session: str, text: str, extraction: str, call: str) -> None:
    plugin.on_post_tool_call(
        tool_name="mcp_smokeball_read_document",
        result=json.dumps({"matterNumber": "203001", "extraction": extraction, "text": text}),
        session_id=session,
        tool_call_id=call,
    )


def _send(plugin, session: str, body: str) -> dict | None:
    return plugin.outbound.check_outbound_send(
        tool_name="mcp_msgraph_mail_reply_message",
        args={"message_id": "AAMk-test", "body": body},
        session_id=session,
        tool_call_id="send1",
    )


@pytest.fixture
def trust_plugin():
    plugin = load_plugin("hermes-smd-trust")
    provenance._reset_for_tests()
    yield plugin
    provenance._reset_for_tests()


def test_dates_quoted_as_the_documents_write_them_are_allowed(trust_plugin) -> None:
    session = "sess-dob-allowed"
    _read(trust_plugin, session, HEADER_PAGE, "pypdf", "r1")
    _read(trust_plugin, session, SCAN_PAGE, "vision", "r2")

    body = (
        "Anna's date of birth is 04/17/1951, from the header of her Kaiser records. "
        "Peter's is 11/03/1977, from the collision report (a machine transcription "
        "of a scan). Reply to confirm and I will start the chronology."
    )
    assert _send(trust_plugin, session, body) is None


def test_a_date_no_document_carried_is_refused(trust_plugin) -> None:
    """The control: without it, 'allow every date' would pass the test above."""
    session = "sess-dob-refused"
    _read(trust_plugin, session, HEADER_PAGE, "pypdf", "r1")

    wrong = "Anna's date of birth is 04/17/1951 and Peter's is 11/04/1977. Reply to confirm."
    directive = _send(trust_plugin, session, wrong)
    assert directive is not None
    assert directive["action"] == "block"
    # Same session, same sentence, only the unread date removed: allowed. So the
    # refusal above was the date no document carried, not something else in it.
    right = "Anna's date of birth is 04/17/1951. Reply to confirm."
    assert _send(trust_plugin, session, right) is None


def test_a_reformatted_date_is_refused(trust_plugin) -> None:
    """The skill says quote the date as written. A spelled-out month is a
    reformat the gate does not trace to the read, so it is refused rather than
    silently trusted."""
    session = "sess-dob-reformat"
    _read(trust_plugin, session, HEADER_PAGE, "pypdf", "r1")
    directive = _send(trust_plugin, session, "Anna's date of birth is 4/17/51. Reply to confirm.")
    assert directive is not None and directive["action"] == "block"
