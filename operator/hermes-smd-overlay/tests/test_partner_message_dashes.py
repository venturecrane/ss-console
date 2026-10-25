"""A partner's message is never refused for a dash (ADR 0097).

hermes-captain-mark, 2026-10-25 17:34:43Z: the first real partner message was
refused by the draft gate's em-dash marker, and the model reworded the
partner's words to get past it. The gate now normalizes the dash in
``partner_message_send`` text in place before it scans, so what is scanned is
what is delivered, and every other check still runs.
"""

from __future__ import annotations

import pytest

from shared import provenance
from tests.conftest import load_plugin

EM = "—"


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.delenv("SMD_IDENTIFIER_GATE_MODE", raising=False)
    plugin = load_plugin("hermes-smd-trust")
    provenance._reset_for_tests()
    yield plugin
    provenance._reset_for_tests()


def _check(plugin, tool: str, args: dict):
    return plugin.outbound.check_outbound_draft(
        tool_name=tool, args=args, session_id="s", tool_call_id="c"
    )


def test_a_partner_message_with_an_em_dash_is_normalized_and_allowed(gate):
    """FALSIFIER: drop the normalize call in check_outbound_draft."""
    args = {"text": f"Dinner at 7 {EM} the usual place"}
    assert _check(gate, "partner_message_send", args) is None
    assert args["text"] == "Dinner at 7, the usual place"


def test_the_same_dash_still_refuses_any_other_write(gate):
    """FALSIFIER: widen the normalized set to every INTERNAL_WRITE tool."""
    directive = _check(gate, "couples_settings_change", {"text": f"a {EM} b"})
    assert directive is not None and directive.get("action") == "block"


def test_a_message_with_no_dash_is_untouched(gate):
    args = {"text": "I think you are cute"}
    assert _check(gate, "partner_message_send", args) is None
    assert args["text"] == "I think you are cute"
