"""The harness still catches the 2026-08-13 defect: a green assertion that it bites.

``_regression/hermes-smd-e2e-shadow/send_0813.py`` is the committed copy of the
handler that shipped (``def _smd_send_message(**kwargs)``). This test puts it
behind the real ``smd_send_message`` registry entry, drives one model tool call
through the real agent loop, and runs the dispatch contract's own per-tool
check on the outcome. That check must FAIL, naming the TypeError Hermes raised
at ``entry.handler(args, **kwargs)``. If it ever passes, the harness has stopped
measuring the class it was built for, and this test goes red.

The real handler is restored in ``finally``, and the next test re-proves the
restored entry still sends.
"""

from __future__ import annotations

import importlib

import pytest

from tests_e2e._drive import drive
from tests_e2e._seat import OWNER_EMAIL, SHADOW_MODULE
from tests_e2e.test_dispatch_contract import BROKER_EFFECTS, PINNED_TOOL, _effect_violation


def _registry_entry(name: str):
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry

    entry = registry.get_entry(name, scope=get_plugin_manager().scope_key) or registry.get_entry(
        name
    )
    assert entry is not None, f"{name} has no registry entry"
    return entry


def _contract_check(outcome, args) -> None:
    """The same per-tool assertions ``test_every_overlay_tool_dispatches_through_hermes``
    applies, raising AssertionError on the first violation."""
    assert outcome.dispatched, f"{outcome.tool}: never dispatched: {outcome.model_saw[:200]!r}"
    assert "TypeError" not in outcome.result, (
        f"{outcome.tool}: Hermes cannot call this handler: {outcome.result[:300]!r}"
    )
    problem = _effect_violation(outcome, args, BROKER_EFFECTS[outcome.tool])
    assert problem is None, problem


def test_the_0813_handler_shape_fails_the_dispatch_contract(harness, agent):
    shape = importlib.import_module(f"{SHADOW_MODULE}.send_0813")
    entry = _registry_entry(PINNED_TOOL)
    original = entry.handler
    args = {"to": [OWNER_EMAIL], "subject": "e2e", "text": "e2e body"}
    entry.handler = shape._smd_send_message
    try:
        outcome = drive(harness, agent, PINNED_TOOL, args)
    finally:
        entry.handler = original

    with pytest.raises(AssertionError, match="TypeError"):
        _contract_check(outcome, args)
    assert "positional argument" in outcome.result, outcome.result
    assert not [r for r in outcome.broker_requests if r.get("action") == "agentmail_send"]


def test_the_restored_handler_passes_the_same_check(harness, agent):
    args = {"to": [OWNER_EMAIL], "subject": "e2e", "text": "e2e body"}
    outcome = drive(harness, agent, PINNED_TOOL, args)
    _contract_check(outcome, args)
