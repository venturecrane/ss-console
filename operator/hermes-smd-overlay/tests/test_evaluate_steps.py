"""The trust wall's steps (``plugins/hermes-smd-trust/evaluate_steps.py``), one
test per step.

``enforce._evaluate_tool_call`` is the ordered spine; these test each step on
its own, through the objects the step really calls (``enforce``'s own names,
read at call time, so a patch on ``enforce`` reaches the step). What the whole
spine decides, call by call, is pinned by ``tests/decisions``. None of these
calls the spine itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared import (
    act_broker,
    matter_gate,
    read_volume,
    search_egress,
    send_anchor,
    spec_gate,
    turn_principal,
)
from shared.action_classes import ActionClass, ToolClassification
from shared.inbound import SESSION_TAINT, TRUST_CLASS_INTERNAL, TRUST_CLASS_WEB_DERIVED
from shared.pending_acts import PENDING_ACTS
from shared.pending_send import PENDING_SEND
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce
steps = enforce._steps  # the steps module the wall calls
Ceiling = enforce.Ceiling
Decision = enforce.EnforcementDecision

SEND = "mcp_agentmail_send_message"
SID = "s-steps"


@pytest.fixture()
def recorded(monkeypatch):
    """Every ``enforce._record_decision`` call, as (positional, keyword)."""
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(enforce, "_record_decision", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: "marcus")
    return calls


def _decision(allowed=True, audit="allow", ceiling=Ceiling.AUTONOMOUS, action=None, reason="ok"):
    return Decision(
        allowed=allowed,
        reason=reason,
        audit_action=audit,
        action_class=action or ActionClass.EXTERNAL_SEND,
        effective_ceiling=ceiling,
    )


def _call(**over):
    base = {
        "exposure": {},
        "vertical_floors": {},
        "session_taint": TRUST_CLASS_INTERNAL,
        "web_admin_send": False,
        "is_send_as": False,
        "effective_action": ActionClass.EXTERNAL_SEND,
        "gate_trust_class": TRUST_CLASS_INTERNAL,
        "is_send": True,
        "send_recips": {"pat@client.example"},
        "approved_replay": False,
        "is_commitment": False,
        "confirmed_act": None,
    }
    base.update(over)
    return base


def test_classify_or_refuse_banned(recorded) -> None:
    got = steps.classify_or_refuse_banned("email_send", "tc-1", SID, "keyed")
    assert got == {
        "action": "block",
        "message": "Refused: autonomous email send is forbidden (ADR 0035)",
    }
    (args, kw), *_ = recorded
    assert args == ("tc-1", "email_send", "marcus") and kw["action_class"] == "banned"
    assert kw["effective_ceiling"] is Ceiling.REFUSED and kw["session_match"] == "keyed"
    ok = steps.classify_or_refuse_banned("mcp_smokeball_read_document", "tc-2", SID, "")
    assert isinstance(ok, ToolClassification) and len(recorded) == 1


def test_mixing_fence(recorded, monkeypatch) -> None:
    monkeypatch.setattr(matter_gate, "content_read_refusal", lambda s, t, a: None)
    assert steps.mixing_fence("read", None, "tc", SID, "") is None
    monkeypatch.setattr(matter_gate, "content_read_refusal", lambda s, t, a: "two matters")
    assert steps.mixing_fence("read", None, "tc", SID, "")["message"] == "Refused: two matters"
    assert recorded[0][1]["reason"] == "MATTER_MIXING_FENCE: two matters"


def test_search_egress_fence(recorded, monkeypatch) -> None:
    tool = sorted(search_egress.SEARCH_TOOLS)[0]
    seen: list = []
    monkeypatch.setattr(search_egress, "record_verdict", lambda tc, v: seen.append((tc, v)))
    monkeypatch.setattr(
        search_egress,
        "evaluate",
        lambda s, t, a: search_egress.EgressVerdict(search_egress.VERDICT_REFUSED_SCREEN, 9, "ids"),
    )
    assert steps.search_egress_fence("not_a_search", {}, "tc", SID, "") is None
    assert not seen
    assert steps.search_egress_fence(tool, {}, "tc", SID, "")["message"] == "Refused: ids"
    assert seen[0][0] == "tc" and recorded[0][1]["reason"].startswith("SEARCH_EGRESS (")
    monkeypatch.setattr(
        search_egress,
        "evaluate",
        lambda s, t, a: search_egress.EgressVerdict(search_egress.VERDICT_PASS, 3),
    )
    assert steps.search_egress_fence(tool, {}, "tc", SID, "") is None and len(recorded) == 1


def test_read_volume_fence(recorded, monkeypatch) -> None:
    monkeypatch.setattr(read_volume, "evaluate_read", lambda s, t: read_volume.Verdict())
    assert steps.read_volume_fence("read", "tc", SID, "") is None and not recorded
    monkeypatch.setattr(
        read_volume, "evaluate_read", lambda s, t: read_volume.Verdict(report="crossed 9")
    )
    assert steps.read_volume_fence("read", "tc", SID, "") is None
    assert recorded[-1][1]["audit_action"] == "report" and recorded[-1][1]["allowed"] is True
    monkeypatch.setattr(
        read_volume, "evaluate_read", lambda s, t: read_volume.Verdict(refusal="over 9")
    )
    assert steps.read_volume_fence("read", "tc", SID, "") == {
        "action": "block",
        "message": "Refused: over 9",
    }
    assert recorded[-1][1]["reason"] == "READ_VOLUME_GATE: over 9"


def test_resolve_call(monkeypatch) -> None:
    monkeypatch.setattr(enforce, "_resolve_persona_exposure", lambda p: {"p": p})
    monkeypatch.setattr(enforce, "_resolve_vertical_floors", lambda: {})
    monkeypatch.setattr(SESSION_TAINT, "trust_class", lambda s: TRUST_CLASS_WEB_DERIVED)
    monkeypatch.setattr(enforce, "_web_derived_admin_send", lambda t, a, s: False)
    taint: list = []

    def reclassify(t, a, c, s, *, tainted):
        taint.append(tainted)
        return ActionClass.EXTERNAL_SEND

    monkeypatch.setattr(enforce, "_reclassify_send", reclassify)
    monkeypatch.setattr(enforce, "_resolve_send_recipients", lambda t, a, s: {"pat@x.example"})
    stored = SimpleNamespace(args={"body": "the reviewed text"})
    monkeypatch.setattr(PENDING_SEND, "approved_match", lambda t, r: stored)
    args = {"body": "drifted"}
    got = steps.resolve_call(
        SEND, args, SID, "marcus", ToolClassification(ActionClass.EXTERNAL_SEND, False), False
    )
    assert args == {"body": "the reviewed text"}, "the stored payload replays over the args"
    assert got["approved_replay"] and got["is_send"] and not got["is_commitment"]
    assert got["exposure"] == {"p": "marcus"} and taint == [True]


def test_decide(monkeypatch) -> None:
    seen: dict = {}

    def fake_enforce(**kw):
        seen.update(kw)
        return _decision(reason="permitted")

    monkeypatch.setattr(enforce, "enforce", fake_enforce)
    monkeypatch.setattr(enforce, "_resolve_current_turn_approval", lambda a: False)
    plain = steps.decide(SEND, {}, "marcus", _call())
    assert plain.reason == "permitted" and seen["current_turn_approval"] is False
    exempt = steps.decide(SEND, {}, "marcus", _call(session_taint="untrusted"))
    assert exempt.reason.startswith("permitted | TAINT_EXEMPT: code_fixed_recipients")


def test_fail_closed(recorded) -> None:
    read = ToolClassification(ActionClass.READ, False)
    try:
        raise RuntimeError("config")
    except RuntimeError:
        assert steps.fail_closed("read", "tc", SID, "", "marcus", read) is None
        refused = steps.fail_closed(
            SEND, "tc", SID, "", "marcus", ToolClassification(ActionClass.EXTERNAL_SEND, False)
        )
    assert recorded[0][1]["allowed"] is True and "effective_ceiling" not in recorded[0][1]
    assert refused["message"].endswith("action; failing closed")
    assert recorded[1][1]["reason"] == "trust decision unavailable; failing closed"


def test_record_ceiling(recorded, monkeypatch) -> None:
    logged: list = []
    monkeypatch.setattr(enforce, "_audit_decision", lambda *a: logged.append(a))
    steps.record_ceiling(SEND, "tc", SID, "keyed", "marcus", _decision(reason="r"))
    assert logged and recorded[0][0] == ("tc", SEND, "marcus")
    assert recorded[0][1]["effective_ceiling"] is Ceiling.AUTONOMOUS


def test_capture_held_send(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(PENDING_SEND, "capture", lambda *a, **kw: captured.append((a, kw)))
    monkeypatch.setattr(send_anchor, "capture_context", lambda s: ("anchor", "lane"))
    monkeypatch.setattr(turn_principal, "requester", lambda: "who")
    steps.capture_held_send(SEND, {}, SID, _call(), _decision())
    assert not captured, "only a send held at confirm is captured"
    steps.capture_held_send(SEND, {}, SID, _call(), _decision(audit="await_approval"))
    (args, kw), *_ = captured
    assert args == (SEND, {}, {"pat@client.example"})
    assert kw == {"anchor": "anchor", "lane": "lane", "requester": "who", "session_id": SID}


def test_proposal_exit(monkeypatch) -> None:
    monkeypatch.setattr(steps.send_as, "propose", lambda *a: {"proposed": a})
    monkeypatch.setattr(enforce, "_propose_commitment_act", lambda t, s, a: {"act": t})
    held = _decision(allowed=False, audit="await_approval")
    assert steps.proposal_exit(SEND, {}, SID, "tc", _call(), held) == (False, None)
    done, verdict = steps.proposal_exit(SEND, {}, SID, "tc", _call(is_send_as=True), held)
    assert done and verdict == {"proposed": (SEND, {}, SID, "tc")}
    refused = steps.proposal_exit(
        SEND, {}, SID, "tc", _call(is_send_as=True), _decision(audit="refuse", reason="no")
    )
    assert refused == (True, {"action": "block", "message": "Refused: no"})
    assert steps.proposal_exit("act", {}, SID, "tc", _call(is_commitment=True), held) == (
        True,
        {"act": "act"},
    )


def test_matter_identity(recorded, monkeypatch) -> None:
    verdict = matter_gate.MatterVerdict(status="mismatch", reason="not a party", matters=("M-1",))
    monkeypatch.setattr(matter_gate, "body_from_args", lambda a: "body")
    monkeypatch.setattr(matter_gate, "evaluate", lambda **kw: verdict)
    monkeypatch.setattr(matter_gate, "mode", lambda: "block")
    monkeypatch.setattr(matter_gate, "multi_matter_session", lambda s: [])
    assert steps.matter_identity(SEND, {}, SID, "tc", "", "p", _call(is_send=False), None) is None
    blocked = steps.matter_identity(SEND, {}, SID, "tc", "", "p", _call(), _decision())
    assert "cites M-1 but not a party" in blocked["message"] and not recorded
    withheld = _decision(allowed=False, audit="draft", reason="drafted")
    assert steps.matter_identity(SEND, {}, SID, "tc", "", "p", _call(), withheld) is None
    assert recorded[0][1]["reason"] == "drafted | MATTER_MISMATCH: not a party"


def test_structure_floor(monkeypatch) -> None:
    seen: dict = {}

    def check(**kw):
        seen.update(kw)
        return {"action": "block", "message": "Refused: one paragraph"}

    monkeypatch.setattr(spec_gate, "check_structure_floor", check)
    monkeypatch.setattr(enforce, "_extract_send_body", lambda a: "text")
    assert steps.structure_floor(SEND, {}, SID, "tc", _call(is_send=False), _decision()) is None
    got = steps.structure_floor(SEND, {}, SID, "tc", _call(), _decision(allowed=False))
    assert got["message"] == "Refused: one paragraph" and seen["allowed"] is False
    assert seen["action_class_value"] == "external_send" and seen["body"] == "text"


def test_content_floor_and_voice(monkeypatch) -> None:
    monkeypatch.setattr(enforce, "_apply_content_floor", lambda t, a: None)
    monkeypatch.setattr(
        steps.voice_gate, "check_voice_gate", lambda **kw: {"action": "block", "message": "v"}
    )
    internal = _call(effective_action=ActionClass.EXTERNAL_SEND_INTERNAL)
    assert steps.content_floor_and_voice(SEND, {}, SID, internal, _decision()) is None
    assert steps.content_floor_and_voice(SEND, {}, SID, _call(), _decision())["message"] == "v"
    confirm = _decision(ceiling=Ceiling.CONFIRM)
    assert steps.content_floor_and_voice(SEND, {}, SID, _call(), confirm) is None
    monkeypatch.setattr(enforce, "_apply_content_floor", lambda t, a: {"floor": True})
    assert steps.content_floor_and_voice(SEND, {}, SID, _call(), _decision()) == {"floor": True}


def test_authored_spec(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(enforce, "_extract_send_body", lambda a: None)
    monkeypatch.setattr(spec_gate, "check_spec_gate", lambda **kw: seen.update(kw))
    assert steps.authored_spec(SEND, {}, SID, _call(), _decision(ceiling=Ceiling.CONFIRM)) is None
    assert not seen
    args = {spec_gate.TEMPLATED_BODY_ARG: True}
    assert steps.authored_spec(SEND, args, SID, _call(), _decision()) is None
    assert seen["templated"] is True and seen["body"] is None, "the body passes uncoerced"


def test_release(monkeypatch) -> None:
    replayed = SimpleNamespace(anchor="a", lane="l", recipients=("pat@client.example",))
    noted: list = []
    spent: list = []
    monkeypatch.setattr(PENDING_SEND, "take_for_send", lambda t, r: replayed)
    monkeypatch.setattr(send_anchor, "note_replay", lambda *a: noted.append(a))
    monkeypatch.setattr(PENDING_ACTS, "take_in_flight", lambda s, t: spent.append((s, t)))
    call = _call(approved_replay=True, is_commitment=True, confirmed_act=object())
    assert steps.release(SEND, SID, call, _decision()) is None
    assert noted == [("a", "l", ("pat@client.example",))] and spent == [(SID, SEND)]
    refused = steps.release(SEND, SID, call, _decision(allowed=False, reason="drafted"))
    assert refused == {"action": "block", "message": "Refused: drafted"} and len(spent) == 1


def test_turn_taint(monkeypatch) -> None:
    asked: list = []
    monkeypatch.setattr(SESSION_TAINT, "trust_class", lambda s: TRUST_CLASS_INTERNAL)
    monkeypatch.setattr(enforce, "_web_derived_admin_send", lambda t, a, s: asked.append(a) or True)
    assert steps._turn_taint(SEND, None, SID) == (TRUST_CLASS_INTERNAL, False) and not asked
    monkeypatch.setattr(SESSION_TAINT, "trust_class", lambda s: TRUST_CLASS_WEB_DERIVED)
    assert steps._turn_taint(SEND, None, SID) == (TRUST_CLASS_WEB_DERIVED, True)
    assert asked == [{}], "a None args reaches the admin check as {}"


def test_effective_action(monkeypatch) -> None:
    monkeypatch.setattr(steps.send_as, "requested_from", lambda t, a: (a or {}).get("from"))
    monkeypatch.setattr(enforce, "_reclassify_send", lambda t, a, c, s, *, tainted: (c, tainted))
    send = ToolClassification(ActionClass.EXTERNAL_SEND, False)
    assert steps._effective_action(SEND, {"from": "x"}, SID, send, True) == (
        True,
        ActionClass.EXTERNAL_SEND_AS_STAFF,
    )
    assert steps._effective_action(SEND, {}, SID, send, True) == (
        False,
        (ActionClass.EXTERNAL_SEND, True),
    )


def test_replay_approved_send(monkeypatch) -> None:
    monkeypatch.setattr(enforce, "_resolve_send_recipients", lambda t, a, s: set())
    asked: list = []
    monkeypatch.setattr(PENDING_SEND, "approved_match", lambda t, r: asked.append(r))
    got = steps._replay_approved_send(SEND, {}, SID, ActionClass.EXTERNAL_SEND)
    assert got == (True, set(), False) and not asked, "no recipients, no approval lookup"
    assert steps._replay_approved_send("read", {}, SID, ActionClass.READ) == (False, set(), False)


def test_replay_confirmed_act(monkeypatch) -> None:
    act = SimpleNamespace(payload={"matter": "M-1", "label": "read-back"})
    monkeypatch.setattr(PENDING_ACTS, "peek_confirmed", lambda s, t: act)
    monkeypatch.setattr(act_broker, "is_call_payload_act", lambda t: False)
    monkeypatch.setattr(act_broker, "tool_arguments", lambda t, p: {"matter": p["matter"]})
    args = {"matter": "M-2"}
    got = steps._replay_confirmed_act("act", args, SID, ActionClass.COMMITMENT, False)
    assert got == (True, act, True) and args == {"matter": "M-1"}
    got = steps._replay_confirmed_act("del", {}, SID, ActionClass.DESTRUCTIVE, False)
    assert got == (False, None, False), "a destructive tool without an act shape is not an act"


def test_annotate_matter(recorded) -> None:
    steps._annotate_matter(SEND, "tc", SID, "", "p", _decision(reason="r"), [])
    assert not recorded
    steps._annotate_matter(SEND, "tc", SID, "", "p", _decision(reason="r"), ["A", "B"])
    assert recorded[0][1]["reason"] == "r | A | B"
    assert recorded[0][1]["effective_ceiling"] is Ceiling.AUTONOMOUS, "re-recorded, not rebuilt"
