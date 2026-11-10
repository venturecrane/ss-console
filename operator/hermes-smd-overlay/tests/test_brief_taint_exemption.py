"""The trust gate's code-fixed-recipients taint exemption, held while it has no minter.

Captain decision 2026-09-28: the date-prep brief could send on a turn that read the
matter's documents, because code (its pre_run's tamper-fenced envelope) fixed its
recipients. The brief and its tool (``casework_brief``) were removed 2026-11-10;
the exemption's plumbing in the trust plugin stays, unreachable, until it is
removed or a new caller is decided. These tests drive the REAL gate through
``shared.send_dispatch`` with a hand-built ``CodeFixedRecipients``, so the
plumbing that remains is still proven to refuse everything but an exact,
staff-only match, and a test pins that nothing in the overlay mints one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import inbound, send_dispatch, spec_gate
from shared.casework_acts import CASEWORK_ACTS
from shared.inbound import SESSION_TAINT, TRUST_CLASS_UNKNOWN_EXTERNAL
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce

ATTORNEY = "atty@firm.example"
PARALEGAL = "para@firm.example"
SESSION = "agent:main:cron:sess-1"
OUTSIDER = "someone@elsewhere.example"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def seat(monkeypatch):
    """A seat whose staff send is autonomous, with a recording transport."""
    monkeypatch.setattr(inbound, "SESSION_INBOUND_ORIGIN", inbound.SessionInboundOrigin())
    SESSION_TAINT._tainted.clear()
    CASEWORK_ACTS.clear(SESSION)
    monkeypatch.setattr(
        enforce,
        "_resolve_persona_exposure",
        lambda slug="": {
            enforce.ActionClass.EXTERNAL_SEND_INTERNAL: enforce.Ceiling.AUTONOMOUS,
            enforce.ActionClass.EXTERNAL_SEND: enforce.Ceiling.AUTONOMOUS,
        },
    )
    monkeypatch.setattr(enforce, "_resolve_roster", lambda: [ATTORNEY, PARALEGAL])
    monkeypatch.setattr(enforce, "_resolve_typed_roster", lambda: [])
    monkeypatch.setattr(enforce, "_resolve_vertical_floors", lambda: {})
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: "agent-case")
    monkeypatch.setattr(spec_gate, "check_structure_floor", lambda **_k: None)
    monkeypatch.setattr(spec_gate, "check_spec_gate", lambda **_k: None)
    monkeypatch.setattr(trust.outbound, "check_outbound_draft", lambda **_k: None)
    monkeypatch.setattr(trust.outbound, "check_outbound_send", lambda **_k: None)
    monkeypatch.setattr(trust, "get_secret", lambda _k: "pilot-smokeball")
    monkeypatch.setattr(trust, "_seat_email_adapter", lambda: "agentmail")
    sent: list[dict] = []

    def transport(*, payload, session_id="", matter_ref=None, audit_extra=None, **_):
        sent.append({"payload": dict(payload), "audit_extra": dict(audit_extra or {})})
        return f"msg-{len(sent)}"

    monkeypatch.setattr(trust.outbound_send, "send_message", transport)
    send_dispatch.set_sender(trust._dispatch_internal_message)
    yield sent
    send_dispatch.set_sender(None)
    SESSION_TAINT._tainted.clear()
    CASEWORK_ACTS.clear(SESSION)


def _taint() -> None:
    """What reading the matter's documents does to the turn."""
    SESSION_TAINT.mark(SESSION, TRUST_CLASS_UNKNOWN_EXTERNAL)


def _dispatch(**kwargs) -> send_dispatch.DispatchResult:
    kwargs.setdefault("subject", "s")
    kwargs.setdefault("text", "Needs you:\n1. A question?")
    kwargs.setdefault("session_id", SESSION)
    kwargs.setdefault("anchor", None)
    kwargs.setdefault("lane", "escalation")
    return send_dispatch.dispatch(**kwargs)


# ---------------------------------------------------------------------------
# (a) the exemption
# ---------------------------------------------------------------------------


def test_the_decision_row_names_the_exemption(monkeypatch):
    _taint()
    recorded: list[str] = []
    real = enforce._record_decision

    def spy(*a, **k):
        recorded.append(k.get("reason", ""))
        return real(*a, **k)

    monkeypatch.setattr(enforce, "_record_decision", spy)
    result = _dispatch(
        to=[ATTORNEY],
        cc=[PARALEGAL],
        code_fixed_recipients=send_dispatch.CodeFixedRecipients(to=(ATTORNEY,), cc=(PARALEGAL,)),
    )
    assert result.sent, result.reason
    assert any("TAINT_EXEMPT: code_fixed_recipients" in r for r in recorded)


# ---------------------------------------------------------------------------
# (b) every other send on the same tainted turn stays refused
# ---------------------------------------------------------------------------


def test_the_model_callable_send_is_still_refused_on_a_tainted_turn():
    _taint()
    args = {
        "to": [ATTORNEY],
        "text": "body",
        # An argument cannot carry the capability: it is a keyword, not a key.
        "code_fixed_recipients": True,
        "_smd_code_fixed_recipients": True,
    }
    block = enforce.evaluate_tool_call("smd_send_message", args, "smd", session_id=SESSION)
    assert block is not None and "untrusted inbound" in block["message"]


def test_the_pre_tool_call_hook_is_still_refused_on_a_tainted_turn(seat):
    _taint()
    block = trust.on_pre_tool_call(
        tool_name="smd_send_message",
        args={"to": [ATTORNEY], "text": "body", "code_fixed_recipients": True},
        session_id=SESSION,
        tool_call_id="tc-1",
    )
    assert block is not None and block["action"] == "block"
    assert seat == []


def test_an_out_of_turn_send_without_the_capability_is_still_refused(seat):
    """The digest (prerendered_dispatch), the rule-request loop and the
    establishment sweeper all call dispatch without the capability."""
    _taint()
    result = _dispatch(to=[ATTORNEY], cc=[PARALEGAL], audit_extra={"skill_name": "digest"})
    assert not result.sent
    assert "untrusted inbound" in result.reason
    assert seat == []


def test_nothing_mints_the_capability():
    """The capability is a code path, so the code paths that mint it are the
    whole scope. Its one minter was removed 2026-11-10; a new one widens the
    exemption and must be decided."""
    minters = sorted(
        str(path.relative_to(ROOT))
        for base in ("plugins", "shared")
        for path in (ROOT / base).rglob("*.py")
        if "CodeFixedRecipients(" in path.read_text(encoding="utf-8")
        and path.name != "send_dispatch.py"
    )
    assert minters == []


# ---------------------------------------------------------------------------
# (c) the exemption cannot be steered
# ---------------------------------------------------------------------------


def test_recipients_differing_from_the_capability_are_refused(seat):
    _taint()
    cap = send_dispatch.CodeFixedRecipients(to=(ATTORNEY,), cc=(PARALEGAL,))
    for to, cc in (
        ([OUTSIDER], [PARALEGAL]),
        ([ATTORNEY, OUTSIDER], [PARALEGAL]),
        ([ATTORNEY], [PARALEGAL, OUTSIDER]),
        ([ATTORNEY], []),
    ):
        result = _dispatch(to=to, cc=cc, code_fixed_recipients=cap)
        assert not result.sent, (to, cc)
        assert "differ" in result.reason
    assert seat == []


def test_a_capability_of_the_wrong_type_is_refused(seat):
    _taint()
    result = _dispatch(to=[ATTORNEY], code_fixed_recipients=True)
    assert not result.sent
    assert seat == []


def test_a_caller_cannot_pre_stamp_the_marker(seat):
    result = _dispatch(to=[ATTORNEY], audit_extra={"taint_exempt": "code_fixed_recipients"})
    assert result.sent
    [call] = seat
    assert "taint_exempt" not in call["audit_extra"]


# ---------------------------------------------------------------------------
# (d) an untainted turn is unchanged
# ---------------------------------------------------------------------------


def test_an_untainted_model_send_is_unchanged():
    block = enforce.evaluate_tool_call(
        "smd_send_message", {"to": [ATTORNEY], "text": "body"}, "smd", session_id=SESSION
    )
    assert block is None


def test_the_gate_input_is_the_session_taint_without_the_capability():
    internal = enforce.ActionClass.EXTERNAL_SEND_INTERNAL
    tainted = TRUST_CLASS_UNKNOWN_EXTERNAL
    assert enforce._taint_gate_input(tainted, internal, False) == tainted
    assert enforce._taint_gate_input(tainted, enforce.ActionClass.EXTERNAL_SEND, True) == tainted
    assert enforce._taint_gate_input(tainted, internal, True) == enforce.TRUST_CLASS_INTERNAL
