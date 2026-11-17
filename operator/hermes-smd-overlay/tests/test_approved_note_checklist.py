"""An SMD note's approved words meet the safety rules, not the style rules.

THE DEFECT (a client seat, 2026-11-17 2:00 PM MST). The Captain approved a note
telling the firm which folder its mail went into. Its words named the firm's own
folders, MEDICAL and MEDICAL RECORDS, in capitals. The dry run passed; the reply
relay then held the send for ``caps_emphasis`` (staff-send surface), and team@
was paged "a reply did not go out". The style rules judge a model's prose; a
note's words are approved as written and the binding refuses any other wording.

Every test drives the relay's real checklist step (``relay_checks._checklist``)
with the real ``binding.Binding`` and the real ``shared.output_checklist``; only
the hold's side effect is recorded instead of performed.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import load_plugin

#: The held note's folder sentence, as approved (no client names in it).
APPROVED = (
    "Most of your matters have a folder named MEDICAL and only a few have MEDICAL RECORDS, "
    "so I put each piece in the medical folder the matter already had.\n\nThanks,\n"
)
#: An internal id in the words is still held: that rule protects the firm, not style.
WITH_ID = "Your mail is filed as 0f0e0d0c-1b2a-4c3d-8e9f-a0b1c2d3e4f5.\n\nThanks,\n"


@pytest.fixture
def mod(monkeypatch: pytest.MonkeyPatch) -> Any:
    m = load_plugin("hermes-smd-reply")
    m.holds = []
    monkeypatch.setattr(
        m, "_held", lambda reason, origin, **kw: m.holds.append((reason, kw.get("rules")))
    )
    return m


def _bound(mod: Any, kind: str) -> Any:
    return mod.binding.Binding(
        sender="colleague@firm.example",
        request=(("kind", kind), ("job_id", "01J")),
        graph_message_id="AAMk-b",
    )


def _check(mod: Any, text: str, bound: Any) -> list[tuple[str, Any]]:
    """The relay's checklist step on a reply to a colleague (internal)."""
    mod.relay_checks._checklist(
        {"tool_name": "mcp_msgraph_mail_reply_to_message"},
        "s1",
        object(),
        bound,
        None,
        "",
        text,
        text,
        "",
        True,
    )
    return mod.holds


def test_an_approved_note_naming_capitalised_folders_is_not_held(mod: Any) -> None:
    """FALSIFIER: drop the note test in ``relay_checks._checklist`` and this
    holds for caps_emphasis, which is the 2:00 PM page."""
    assert _check(mod, APPROVED, _bound(mod, "note_job")) == []


def test_the_same_words_from_the_model_are_still_held(mod: Any) -> None:
    """The exemption is the note's, not the words': a job reply the model wrote
    with the same capitals, or an unbound reply, still meets the staff rules."""
    held = _check(mod, APPROVED, _bound(mod, "demand_job"))
    assert held and "caps_emphasis" in held[0][1]
    mod.holds.clear()
    held = _check(mod, APPROVED, None)
    assert held and "caps_emphasis" in held[0][1]


def test_an_approved_note_still_meets_the_id_rule(mod: Any) -> None:
    held = _check(mod, WITH_ID, _bound(mod, "note_job"))
    assert held, "an internal id in a note is still a defect the firm must not see"
