"""Tests for the voice live-gate (ADR 0028 §2, issue #855), after the sample-transform
fallback was replaced by a fail-closed one (ss ADR 0083 decision 6, 2026-11-07).

The gate fires only on an allowed autonomous OUTSIDE ``external_send``. A class
that declares ``voice_spec: expected`` is governed by the spec regime
(``test_voice_gate_spec_regime.py``); every other class is undeclared, and the
send is downgraded to draft-for-review and audited with reason
``no_voice_spec_declared``. Nothing can make an undeclared class pass: there is
no transform to certify it any more.

Plus the exclusion of the confirm and external_send_internal paths, which have a
human in the loop or are ops traffic, and the content floor's precedence.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_plugin


def _load_trust(submodule: str = ""):
    plugin = load_plugin("hermes-smd-trust")
    if not submodule:
        return plugin
    if submodule == "voice_gate":
        return plugin.enforce.voice_gate
    return getattr(plugin, submodule)


@pytest.fixture
def voice_gate(monkeypatch):
    vg = _load_trust("voice_gate")
    monkeypatch.setattr(vg, "_AUDIT_WIRED", False)
    monkeypatch.setattr(vg, "_AUDIT_CLIENT", None)
    monkeypatch.setattr(vg, "_AUDIT_CUSTOMER_SLUG", None)
    return vg


def _capture_audit(monkeypatch, vg) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(vg, "_emit_voice_gate_audit", lambda **kw: calls.append(kw))
    return calls


def _undeclared(monkeypatch, vg) -> None:
    monkeypatch.setattr(vg, "_class_declaration", lambda _cls: None)


# ===========================================================================
# check_voice_gate on an undeclared class
# ===========================================================================


def test_an_undeclared_class_downgrades_and_is_audited(voice_gate, monkeypatch):
    _undeclared(monkeypatch, voice_gate)
    calls = _capture_audit(monkeypatch, voice_gate)
    result = voice_gate.check_voice_gate(tool_name="agentmail:send_message", session_id="s1")
    assert isinstance(result, dict) and result["action"] == "block"
    assert "no_voice_spec_declared" in result["message"] and "draft" in result["message"].lower()
    assert [c["reason"] for c in calls] == ["no_voice_spec_declared"]


@pytest.mark.parametrize(
    "decl", [None, {}, {"voice_spec": "none"}, {"voice_spec": "maybe"}, "expected"]
)
def test_every_undeclared_state_downgrades(voice_gate, monkeypatch, decl):
    monkeypatch.setattr(voice_gate, "_class_declaration", lambda _cls: decl)
    _capture_audit(monkeypatch, voice_gate)
    result = voice_gate.check_voice_gate(
        tool_name="agentmail:send_message",
        action_class_value="external_send_client",
        session_id="s1",
    )
    assert result is not None and result["action"] == "block"


def test_an_unreadable_config_reads_as_undeclared_and_downgrades(voice_gate, monkeypatch):
    from shared import customer_config

    def _boom(cls):
        raise customer_config.CustomerConfigMissingError("no volume")

    monkeypatch.setattr(customer_config.CustomerConfig, "from_volume", classmethod(_boom))
    _capture_audit(monkeypatch, voice_gate)
    result = voice_gate.check_voice_gate(
        tool_name="agentmail:send_message",
        action_class_value="external_send_client",
        session_id="s1",
    )
    assert result is not None and result["action"] == "block"


def test_downgrade_without_audit_env_does_not_crash(voice_gate, monkeypatch):
    _undeclared(monkeypatch, voice_gate)
    for key in ("SMD_CUSTOMER_SLUG", "SMD_D1_AUDIT_BINDING", "SMD_AUDIT_BROKER_SOCKET"):
        monkeypatch.delenv(key, raising=False)
    result = voice_gate.check_voice_gate(tool_name="agentmail:send_message", session_id="s1")
    assert isinstance(result, dict) and result["action"] == "block"


def test_the_removed_fallback_is_gone(voice_gate):
    """No sample library to read, no transform mark to consult."""
    assert not hasattr(voice_gate, "_voice_authored")
    assert not hasattr(voice_gate, "VOICE_STATUS")
    assert not hasattr(voice_gate, "REGIME_MECHANISM_B") and not hasattr(
        voice_gate, "REGIME_UNBOUND"
    )


# ===========================================================================
# Integration through enforce.evaluate_tool_call
# ===========================================================================


def _set_exposure(monkeypatch, enforce, mapping, *, persona="marcus"):
    monkeypatch.setattr(enforce, "_resolve_persona_exposure", lambda slug="": dict(mapping))
    monkeypatch.setenv("HERMES_ACTIVE_PROFILE", persona)


_CLEAN = {"subject": "Saw your note", "text": "Got it, that works on my end. Talk soon."}


def test_an_autonomous_outside_send_of_an_undeclared_class_is_a_draft(monkeypatch):
    enforce = _load_trust("enforce")
    _set_exposure(
        monkeypatch, enforce, {enforce.ActionClass.EXTERNAL_SEND: enforce.Ceiling.AUTONOMOUS}
    )
    _undeclared(monkeypatch, enforce.voice_gate)
    _capture_audit(monkeypatch, enforce.voice_gate)
    result = enforce.evaluate_tool_call(
        "agentmail:send_message", dict(_CLEAN), "smd", session_id="sess-x"
    )
    assert isinstance(result, dict) and result["action"] == "block"
    assert "draft" in result["message"].lower()


def test_confirm_ceiling_send_is_not_voice_gated(monkeypatch):
    enforce = _load_trust("enforce")
    _set_exposure(
        monkeypatch, enforce, {enforce.ActionClass.EXTERNAL_SEND: enforce.Ceiling.CONFIRM}
    )
    _undeclared(monkeypatch, enforce.voice_gate)
    args = {"text": "Got it, that works on my end.", "_current_turn_approval": True}
    assert (
        enforce.evaluate_tool_call("agentmail:send_message", args, "smd", session_id="sess-x")
        is None
    )


def test_internal_send_is_not_voice_gated(monkeypatch):
    enforce = _load_trust("enforce")
    _set_exposure(
        monkeypatch,
        enforce,
        {enforce.ActionClass.EXTERNAL_SEND_INTERNAL: enforce.Ceiling.AUTONOMOUS},
    )
    _undeclared(monkeypatch, enforce.voice_gate)
    monkeypatch.setattr(
        enforce, "_reclassify_send", lambda *a, **k: enforce.ActionClass.EXTERNAL_SEND_INTERNAL
    )
    args = {"text": "Heads up: the Miller matter needs a call today."}
    assert (
        enforce.evaluate_tool_call("agentmail:send_message", args, "smd", session_id="sess-x")
        is None
    )


def test_content_floor_precedes_voice_gate_on_sensitive_body(monkeypatch):
    enforce = _load_trust("enforce")
    _set_exposure(
        monkeypatch, enforce, {enforce.ActionClass.EXTERNAL_SEND: enforce.Ceiling.AUTONOMOUS}
    )
    _undeclared(monkeypatch, enforce.voice_gate)
    args = {"text": "Please remit payment of $500 by Friday."}
    result = enforce.evaluate_tool_call("agentmail:send_message", args, "smd", session_id="sess-x")
    assert isinstance(result, dict) and result["action"] == "block"
    assert "voice live-gate" not in result["message"]
