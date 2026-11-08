"""A layout rule's read-back says what a later change of the same thing in the
firm's own library Word file does, behind ``logic_in_code.layout_into_library``
(design 2026-11-06, piece 5; ``shared/rule_reach.LATER_EDIT_SENTENCE``).

Off (every seat today), the read-back is byte for byte what it was; on, a rule
whose words compile to a layout value gets the one sentence, and a rule of
wording does not. The sentence lives in the read-back's own file, beside the
reach sentence. Names are made up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import rule_reach
from tests.test_wording_rules import (  # noqa: F401 - the plugin fixture, used by name
    ADMIN,
    RULE,
    plugin,
)

LAYOUT = "In mediation briefs, three blank lines before the court name."
WORDING = "In mediation briefs, write persuasively."


def _switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text(
        f"logic_in_code:\n  layout_into_library: {mode}\n" if mode else "customer_id: x\n", "utf-8"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _args(text: str) -> dict:
    return {
        "scope": "firm_adjust",
        "subject": {
            "output_class": "work_product",
            "property": "format",
            "doc_kinds": ["mediation_brief"],
        },
        "text": text,
        "instructed_by": ADMIN,
        "source_ref": "msg-1",
    }


def test_the_sentence_has_no_em_dash_no_timeframe_and_says_we():
    s = rule_reach.LATER_EDIT_SENTENCE
    assert chr(0x2014) not in s and " we " in f" {s} " and "day" not in s and "week" not in s


@pytest.mark.parametrize(
    "mode, said", [(None, False), ("observe", False), ("sometimes", False), ("enforce", True)]
)
def test_only_an_enforcing_switch_says_it(tmp_path, monkeypatch, mode, said):
    _switch(tmp_path, monkeypatch, mode)
    assert rule_reach.precedence_on() is said


def test_off_the_plugins_readback_is_what_it_was(plugin, tmp_path, monkeypatch):  # noqa: F811 - pytest fixture injection
    mod, _ = plugin
    _switch(tmp_path, monkeypatch, None)
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message=LAYOUT)
    out = json.loads(mod._propose(_args(LAYOUT), session_id="s1"))
    assert out["readback"] == f"[rule {RULE}] {LAYOUT}"


def test_on_a_layout_rules_readback_carries_the_sentence_and_a_wording_rules_does_not(
    plugin,  # noqa: F811 - pytest fixture injection
    tmp_path,
    monkeypatch,
):
    mod, _ = plugin
    _switch(tmp_path, monkeypatch, "enforce")
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message=LAYOUT)
    out = json.loads(mod._propose(_args(LAYOUT), session_id="s1"))
    assert out["readback"] == f"[rule {RULE}] {LAYOUT} {rule_reach.LATER_EDIT_SENTENCE}"
    mod.on_pre_llm_call(session_id="s2", sender_id=ADMIN, user_message=WORDING)
    out = json.loads(mod._propose(_args(WORDING), session_id="s2"))
    assert rule_reach.LATER_EDIT_SENTENCE not in out["readback"]


def test_a_rule_for_another_class_never_carries_it():
    args = {**_args(LAYOUT), "subject": {"output_class": "outbound_client", "property": "format"}}
    assert rule_reach.readback_with_precedence("[rule x] y", args, True) == "[rule x] y"
