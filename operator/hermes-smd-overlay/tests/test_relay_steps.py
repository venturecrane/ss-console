"""The steps of the relay decision, one test per step (wave 4, PR B).

``_relay_draft`` is a spine over named steps: 1 to 7 in
``plugins/hermes-smd-reply/relay_checks.py``, 8 to 10 in the plugin beside the
transports. Each step is driven here on its own, with the plugin's own names
replaced by recorders, so a step that stops deciding what it decides fails here
by name. What the whole decision does, call by call, is held by the corpus under
``tests/decisions/``; these tests never call ``_relay_draft`` itself.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared import inbound, matter_file_facts, matter_file_reply, provenance, send_policy
from shared.recipient_classifier import RecipientClass
from tests.conftest import load_plugin

SENDER = "paralegal@firm.example"
PERSON = "attorney@firm.example"


@pytest.fixture
def mod(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The plugin, with every hold and effect a step makes recorded in ``mod.calls``."""
    m = load_plugin("hermes-smd-reply")
    calls: list[tuple[str, tuple, dict]] = []
    m.calls = calls

    def rec(name: str, result: Any = None) -> Any:
        def fn(*a: Any, **k: Any) -> Any:
            calls.append((name, a, k))
            return result

        return fn

    m.rec = rec
    for name in (
        "_held",
        "_discard_drafts",
        "_deliver_job_reply",
        "_commit_reply",
        "_emit_reply_event",
        "_send_failed",
        "_notify_hold",
        "_hold_for_review",
        "_send_file_continuations",
    ):
        monkeypatch.setattr(m, name, rec(name))
    m._CURRENT_CALL.__dict__.clear()
    return m


def _origin(**over: Any) -> inbound.InboundOrigin:
    fields: dict[str, Any] = {
        "sender_address": SENDER,
        "message_id": "msg-1",
        "inbox_id": "inbox-1",
    }
    fields.update(over)
    return inbound.InboundOrigin(**fields)


class _Cfg:
    """The few CustomerConfig reads the steps make, authored per test."""

    def __init__(self, *, roster: bool = True, device: str = "", adapter: str = "msgraph"):
        self.roster = roster
        self.device = device
        self.raw: dict[str, Any] = {}
        self.vertical = ""
        self.inbound_roster: list[str] = [SENDER]
        self.outbound_roster: dict[str, Any] = {}
        self.connectors = {"Email": {"adapter": adapter}}

    def sender_on_roster(self, address: str) -> bool:
        return self.roster

    def device_reply_target(self, address: str) -> str:
        return self.device


def _held(mod: Any) -> list[str]:
    return [c[1][0] for c in mod.calls if c[0] == "_held"]


# --------------------------------------------------------------------------
# 1. The call guard
# --------------------------------------------------------------------------


def test_call_guard_binds_the_call_and_passes_a_confirmed_draft(mod: Any) -> None:
    kwargs = {
        "tool_name": "mcp_msgraph_mail_create_draft",
        "tool_call_id": "call-7",
        "session_id": "sess-1",
        "result": '{"id": "d1"}',
    }
    assert mod.relay_checks.call_guard(kwargs) is True
    assert mod._CURRENT_CALL.tool_call_id == "call-7"
    assert mod._CURRENT_CALL.session_id == "sess-1"
    assert mod._CURRENT_CALL.origin is None and mod._CURRENT_CALL.transmitted is False


def test_call_guard_refuses_another_tool_and_a_failed_draft(mod: Any, monkeypatch) -> None:
    assert mod.relay_checks.call_guard({"tool_name": "mcp_smokeball_get_matter"}) is False
    assert not hasattr(mod._CURRENT_CALL, "tool_call_id")
    monkeypatch.setattr(mod.relay, "draft_call_failed", lambda **k: True)
    assert mod.relay_checks.call_guard({"tool_name": "mcp_msgraph_mail_create_draft"}) is False


# --------------------------------------------------------------------------
# 2. Config and origin
# --------------------------------------------------------------------------


def test_config_and_origin_returns_this_turns_email(mod: Any, monkeypatch) -> None:
    cfg = _Cfg()
    origin = _origin()
    monkeypatch.setattr(mod.CustomerConfig, "from_volume", classmethod(lambda cls, p: cfg))
    monkeypatch.setattr(
        inbound.SESSION_INBOUND_ORIGIN, "bound_this_turn", lambda s: origin if s == "s1" else None
    )
    got = mod.relay_checks.config_and_origin({"session_id": "s1", "args": {"to": SENDER}})
    assert got == (cfg, None, "s1", {"to": SENDER}, origin, None)
    assert mod._CURRENT_CALL.origin is origin


def test_config_and_origin_fails_closed(mod: Any, monkeypatch) -> None:
    def unreadable(cls: Any, path: str) -> Any:
        raise OSError("gone")

    monkeypatch.setattr(mod.CustomerConfig, "from_volume", classmethod(unreadable))
    assert mod.relay_checks.config_and_origin({"session_id": "s1"}) is None
    monkeypatch.setattr(mod.CustomerConfig, "from_volume", classmethod(lambda cls, p: _Cfg()))
    monkeypatch.setattr(inbound.SESSION_INBOUND_ORIGIN, "bound_this_turn", lambda s: None)
    monkeypatch.setattr(mod.binding.SESSION_BINDINGS, "get", lambda s: None)
    assert mod.relay_checks.config_and_origin({"session_id": "s1"}) is None


# --------------------------------------------------------------------------
# 3. Duplicate and code-sent kinds
# --------------------------------------------------------------------------


def test_duplicate_reply_is_held_and_its_draft_discarded(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod._REPLIED, "committed", lambda key: True)
    kwargs = {"result": {"id": "draft-9"}}
    got = mod.relay_checks.duplicate_and_code_sent(kwargs, _Cfg(), "s1", _origin(), None)
    assert got is None
    assert _held(mod) == ["duplicate_reply"]
    assert [c[0] for c in mod.calls] == ["_held", "_discard_drafts"]


def test_a_fresh_reply_gets_its_key_and_draft(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod._REPLIED, "committed", lambda key: False)
    monkeypatch.setattr(mod.working_drafts, "draft_id_of", lambda result: "draft-9")
    got = mod.relay_checks.duplicate_and_code_sent({}, _Cfg(), "s1", _origin(), None)
    assert got == (mod._reply_guard_key("s1", None, _origin()), "draft-9")
    assert mod.calls == []


# --------------------------------------------------------------------------
# 4. Roster, recipient and redirect
# --------------------------------------------------------------------------


def test_roster_holds_a_sender_off_the_roster(mod: Any) -> None:
    assert mod.relay_checks._roster(_Cfg(roster=False), "s1", _origin(), "k", "d") is False
    assert _held(mod) == ["sender_not_on_roster"]


def test_roster_tracks_the_draft_and_passes(mod: Any) -> None:
    assert mod.relay_checks._roster(_Cfg(), "s1", _origin(), "k", "d") is True
    assert mod._WORKING_DRAFTS.take("s1", "k") == ["d"]


def test_redirect_aims_a_device_reply_at_its_person(mod: Any) -> None:
    reply_to, redirected = mod.relay_checks._redirect(_Cfg(device=PERSON), _origin())
    assert (reply_to, redirected) == (PERSON, True)
    assert mod._CURRENT_CALL.reply_to == PERSON
    assert mod.relay_checks._redirect(_Cfg(), _origin()) == (SENDER, False)


def test_recipient_lock_holds_each_failing_fence(mod: Any) -> None:
    lock = mod.relay_checks._recipient_lock
    to_sender = {"to": [SENDER]}
    assert lock(_Cfg(), {"to": ["x@evil.example"]}, _origin(), None, SENDER, False, ()) is False
    assert lock(_Cfg(), to_sender, _origin(inbox_id=""), None, SENDER, False, ()) is False
    assert (
        lock(_Cfg(adapter="agentmail"), to_sender, _origin(), object(), SENDER, False, ()) is False
    )
    assert (
        lock(_Cfg(adapter="agentmail"), {"to": [PERSON]}, _origin(), None, PERSON, True, ())
        is False
    )
    assert _held(mod) == [
        "recipient_mismatch",
        "no_inbox_id",
        "bound_reply_unsupported",
        "device_redirect_unsupported",
    ]
    assert lock(_Cfg(), to_sender, _origin(), None, SENDER, False, ()) is True


def test_roster_and_recipient_returns_the_route(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_shared_reply_people", lambda *a: ())
    got = mod.relay_checks.roster_and_recipient(
        _Cfg(), "s1", {"to": [SENDER]}, _origin(), None, "k", "d"
    )
    assert got == (SENDER, False, ())
    monkeypatch.setattr(mod, "_shared_reply_people", lambda *a: mod._SHARED_UNSUPPORTED)
    got = mod.relay_checks.roster_and_recipient(
        _Cfg(), "s1", {"to": [SENDER]}, _origin(), None, "k", "d"
    )
    assert got is None and _held(mod) == ["shared_reply_unsupported"]


# --------------------------------------------------------------------------
# 5. Body and facts checks
# --------------------------------------------------------------------------


def test_facts_without_a_records_reply_leave_the_args(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_file_facts", lambda s, o: None)
    args = {"body": "Hello"}
    facts, facts_hold, _words, out = mod.relay_checks._facts("s1", _origin(), args)
    assert (facts, facts_hold, out) == (None, "", args)


def test_facts_on_hold_write_the_empty_block(mod: Any, monkeypatch) -> None:
    facts = SimpleNamespace(hold="no page found", text="")
    monkeypatch.setattr(mod, "_file_facts", lambda s, o: facts)
    seen = []
    monkeypatch.setattr(matter_file_facts, "insert", lambda a, f: seen.append(f) or a)
    _f, facts_hold, _w, _a = mod.relay_checks._facts("s1", _origin(), {"body": "x"})
    assert facts_hold == "no page found" and seen == [matter_file_facts.EMPTY]


def test_provenance_degrades_to_no_exemption(mod: Any, monkeypatch) -> None:
    def boom(session_id: str) -> Any:
        raise RuntimeError("register unreadable")

    monkeypatch.setattr(provenance, "resolve_session", boom)
    assert mod.relay_checks._provenance("s1") == (frozenset(), frozenset())


def test_content_floor_floors_an_unclassifiable_recipient(mod: Any, monkeypatch) -> None:
    def boom(*a: Any) -> Any:
        raise ValueError("unclassifiable")

    monkeypatch.setattr(mod, "classify_recipients_typed", boom)
    seen: dict[str, Any] = {}

    def gate(text: str, **k: Any) -> Any:
        seen.update(k)
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(mod.relay, "gate_body", gate)
    got = mod.relay_checks._content_floor(_Cfg(), None, "s1", _origin(), None, SENDER, "body")
    assert got == (None, False) and seen["internal_recipient"] is False


def test_content_floor_holds_on_the_gate(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "classify_recipients_typed", lambda *a: RecipientClass.INTERNAL)
    monkeypatch.setattr(
        mod.relay,
        "gate_body",
        lambda text, **k: SimpleNamespace(allowed=False, reason="fabrication", categories=()),
    )
    got = mod.relay_checks._content_floor(_Cfg(), None, "s1", _origin(), None, SENDER, "body")
    assert got is None and _held(mod) == ["fabrication"]


def test_body_and_facts_returns_the_text(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_file_facts", lambda s, o: None)
    monkeypatch.setattr(mod.relay, "draft_body", lambda a: ("scan", "text", "<p>html</p>"))
    monkeypatch.setattr(mod, "classify_recipients_typed", lambda *a: RecipientClass.INTERNAL)
    monkeypatch.setattr(mod.relay, "gate_body", lambda t, **k: SimpleNamespace(allowed=True))
    monkeypatch.setattr(mod, "_session_tainted", lambda s: False)
    got = mod.relay_checks.body_and_facts(_Cfg(), None, "s1", {}, _origin(), None, SENDER)
    assert got is not None
    facts, facts_hold, _words, text, rclass, internal = got
    assert (facts, facts_hold, text) == (None, "", ("scan", "text", "<p>html</p>"))
    assert (rclass, internal) == (RecipientClass.INTERNAL, True)


# --------------------------------------------------------------------------
# 6. The spec checklist
# --------------------------------------------------------------------------


def test_checklist_holds_the_models_words(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: ("rule-a", "detail"))
    got = mod.relay_checks._checklist(
        {"tool_name": "t"}, "s1", _origin(), None, None, "", None, "text", "", True
    )
    assert got is None and _held(mod) == [mod.spec_gate.REASON_OUTPUT_CHECKLIST]


def test_checklist_on_the_facts_block_waits_for_smd(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: ("rule-a", "detail"))
    facts = SimpleNamespace(hold="", text="the facts", names=())
    got = mod.relay_checks._checklist(
        {"tool_name": "t"}, "s1", _origin(), None, facts, "facts unchecked", None, "t", "", True
    )
    assert got == "facts unchecked" and _held(mod) == []


def test_spec_checklist_holds_an_empty_body_and_passes_a_body(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_advertising_violations", lambda *a, **k: None)
    step = mod.relay_checks.spec_checklist
    kw = {"tool_name": "t"}
    assert step(kw, "s1", _origin(), None, None, "", None, ("s", "", ""), True) is None
    assert _held(mod) == ["empty_body"]
    assert step(kw, "s1", _origin(), None, None, "", None, ("s", "body", ""), True) == ""


def test_spec_checklist_holds_on_the_advertising_gate(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_advertising_violations", lambda *a, **k: ("ad-rule", "why"))
    got = mod.relay_checks.spec_checklist(
        {"tool_name": "t"}, "s1", _origin(), None, None, "", None, ("s", "b", ""), True
    )
    assert got is None and _held(mod) == [mod.advertising_gate.REASON]


# --------------------------------------------------------------------------
# 7. The matter
# --------------------------------------------------------------------------


def _verdict(**over: Any) -> Any:
    fields = {"should_withhold": False, "status": "ok", "matters": (), "reason": ""}
    fields.update(over)
    return SimpleNamespace(**fields)


def test_matter_gate_holds_a_mismatch_in_block_mode(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod.matter_gate, "evaluate", lambda **k: _verdict(should_withhold=True))
    monkeypatch.setattr(mod.matter_gate, "matter_ref_for", lambda s, m: "M-1")
    monkeypatch.setattr(mod.matter_gate, "mode", lambda: "block")
    got = mod.relay_checks._matter_gate("s1", _origin(), "body", SENDER, None)
    assert got == (True, "M-1") and _held(mod) == ["matter_mismatch"]


def test_matter_gate_records_an_unresolved_matter(mod: Any, monkeypatch) -> None:
    verdict = _verdict(status="unresolved", matters=("M-2",))
    monkeypatch.setattr(mod.matter_gate, "evaluate", lambda **k: verdict)
    monkeypatch.setattr(mod.matter_gate, "matter_ref_for", lambda s, m: None)
    got = mod.relay_checks._matter_gate("s1", _origin(), "body", SENDER, None)
    assert got == (False, None)
    assert [c[2]["action_type"] for c in mod.calls] == ["MATTER_UNRESOLVED"]


def test_matter_holds_a_refused_file(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod.matter_gate, "evaluate", lambda **k: _verdict())
    monkeypatch.setattr(mod.matter_gate, "matter_ref_for", lambda s, m: None)

    def refuse(*a: Any, **k: Any) -> Any:
        raise matter_file_reply.FileRefused("two matters")

    monkeypatch.setattr(mod, "_prepare_matter_files", refuse)
    route = (SENDER, False, ())
    got = mod.relay_checks.matter(_Cfg(), "s1", _origin(), None, ("b", "b", ""), route, None)
    assert got is None and _held(mod) == [matter_file_reply.REFUSAL_REASON]
    monkeypatch.setattr(mod, "_prepare_matter_files", lambda *a, **k: None)
    got = mod.relay_checks.matter(_Cfg(), "s1", _origin(), None, ("b", "b", ""), route, None)
    assert got == (None, None)


# --------------------------------------------------------------------------
# 8. Send-posture routing (in the plugin)
# --------------------------------------------------------------------------

ROUTE = (SENDER, False, ())
TEXT = ("scan", "text", "")


def test_send_posture_passes_a_reply_no_review_holds(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(matter_file_facts, "searched", lambda s, m: False)
    got = mod._relay_send_posture(_Cfg(), "s1", _origin(), None, "k", ROUTE, TEXT, (None, None), "")
    assert got is True and mod.calls == []


def test_send_posture_holds_for_review_or_refuses(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_review_unsupported", lambda *a: "")
    step = mod._relay_send_posture
    assert step(_Cfg(), "s1", _origin(), None, "k", ROUTE, TEXT, (None, None), "bad facts") is False
    assert [c[0] for c in mod.calls] == ["_hold_for_review"]
    mod.calls.clear()
    monkeypatch.setattr(mod, "_review_unsupported", lambda *a: "agentmail cannot")
    assert step(_Cfg(), "s1", _origin(), None, "k", ROUTE, TEXT, (None, None), "bad facts") is False
    assert [c[0] for c in mod.calls] == ["_commit_reply", "_held"]


# --------------------------------------------------------------------------
# 9. Transmit or hold (in the plugin)
# --------------------------------------------------------------------------


def _policy(monkeypatch: pytest.MonkeyPatch, mod: Any) -> None:
    monkeypatch.setattr(send_policy, "live_send_policy", lambda path: "policy")
    monkeypatch.setattr(mod, "_held_pending_for", lambda to, policy: False)


def test_rate_limit_holds_without_a_limiter(mod: Any, monkeypatch) -> None:
    _policy(monkeypatch, mod)
    monkeypatch.setattr(mod, "_LIMITER", None)
    got = mod._relay_rate_limit(_Cfg(), "s1", _origin(), "k", ROUTE, TEXT, None, None, False)
    assert got is None and _held(mod) == ["rate_limited"]


def test_rate_limit_passes_or_holds_a_files_reply(mod: Any, monkeypatch) -> None:
    _policy(monkeypatch, mod)
    allowed = SimpleNamespace(check=lambda to, **k: mod.relay.RateDecision(True))
    monkeypatch.setattr(mod, "_LIMITER", allowed)
    got = mod._relay_rate_limit(
        _Cfg(), "s1", _origin(), "k", ROUTE, TEXT, None, RecipientClass.INTERNAL, True
    )
    assert got == ("internal", "msgraph", inbound.content_digest("scan"))
    refused = SimpleNamespace(check=lambda to, **k: mod.relay.RateDecision(False, "rate_limited"))
    monkeypatch.setattr(mod, "_LIMITER", refused)
    got = mod._relay_rate_limit(_Cfg(), "s1", _origin(), "k", ROUTE, TEXT, object(), None, False)
    assert got is None and _held(mod) == ["matter_files_rate_limited"]


def test_queue_held_commits_only_a_queued_reply(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_enqueue_hold", lambda **k: True)
    monkeypatch.setattr(mod, "_pending_count", lambda: 1)
    decision = mod.relay.RateDecision(False, None)
    mod._relay_queue_held(
        decision, "s1", _origin(), "k", "internal", "msgraph", "t", "", "dg", "p", SENDER
    )
    assert [c[0] for c in mod.calls] == ["_commit_reply", "_held", "_notify_hold"]
    assert mod.calls[1][1][0] == "rate_limited"


def test_transmit_returns_the_sent_id_or_decided(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_send_msgraph_reply", lambda *a, **k: "sent-1")
    args = (None, "msgraph", "s1", _origin(), "k", ("t", ""), ROUTE, (None, None), "c", "dg")
    assert mod._relay_transmit(*args) == "sent-1"

    def refused(*a: Any, **k: Any) -> Any:
        raise mod.relay.FixedWordingRefused("use the fixed words")

    monkeypatch.setattr(mod, "_send_msgraph_reply", refused)
    assert mod._relay_transmit(*args) is mod._RELAY_DECIDED
    assert _held(mod) == [mod._FIXED_WORDING_REASON]

    def unknown(*a: Any, **k: Any) -> Any:
        raise mod.relay.RelayOutcomeUnknown("lost answer")

    monkeypatch.setattr(mod, "_send_msgraph_reply", unknown)
    assert mod._relay_transmit(*args) is mod._RELAY_DECIDED
    assert [c[0] for c in mod.calls][-2:] == ["_commit_reply", "_send_failed"]


def test_audit_sent_writes_the_reply_sent_row(mod: Any, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(mod.request_index, "record_reply_opening", lambda *a, **k: opened.append(a))
    mod._relay_audit_sent(
        "s1", _origin(), SENDER, None, False, "msgraph", "sent-1", "scan", ("t", ""), (None, None)
    )
    (row,) = mod.calls
    assert row[2]["action_type"] == "REPLY_SENT"
    assert row[2]["metadata"]["sent_message_id"] == "sent-1"
    assert row[2]["metadata"]["recipient_class"] == "unclassified"
    assert opened == [("msg-1", "t")]


def test_sent_commits_audits_and_marks_transmitted(mod: Any, monkeypatch) -> None:
    monkeypatch.setattr(mod.request_index, "record_reply_opening", lambda *a, **k: None)
    mod._relay_sent(
        "s1", _origin(), "k", SENDER, "scan", ("t", ""), (None, None), "sent-1", "msgraph", None, 0
    )
    assert mod._CURRENT_CALL.transmitted is True
    assert [c[0] for c in mod.calls] == ["_commit_reply", "_emit_reply_event", "_discard_drafts"]


def test_transmit_or_hold_stops_at_a_held_rate_limit(mod: Any, monkeypatch) -> None:
    _policy(monkeypatch, mod)
    monkeypatch.setattr(mod, "_LIMITER", None)
    mod._relay_transmit_or_hold(
        _Cfg(), "s1", _origin(), None, "k", ROUTE, TEXT, (None, None), None, 0
    )
    assert _held(mod) == ["rate_limited"]
    assert not getattr(mod._CURRENT_CALL, "transmitted", False)


# --------------------------------------------------------------------------
# 10. The catch-all (in the plugin)
# --------------------------------------------------------------------------


def test_catch_all_tells_an_unsent_reply_and_nothing_else(mod: Any) -> None:
    mod._relay_catch_all(RuntimeError("fault"))
    assert mod.calls == []
    mod._CURRENT_CALL.origin = _origin()
    mod._CURRENT_CALL.tool_call_id = "call-1"
    mod._relay_catch_all(RuntimeError("fault"))
    assert [(c[0], c[1][0]) for c in mod.calls] == [("_send_failed", "relay_error: RuntimeError")]
    mod.calls.clear()
    mod._CURRENT_CALL.transmitted = True
    mod._relay_catch_all(RuntimeError("fault"))
    assert mod.calls == []


# --------------------------------------------------------------------------
# The binding
# --------------------------------------------------------------------------


def test_each_load_of_the_plugin_gets_steps_bound_to_itself() -> None:
    """The steps read the plugin's names at call time, so two loads of the
    plugin must not share one copy of them (the second would serve the first)."""
    first = load_plugin("hermes-smd-reply")
    second = load_plugin("hermes-smd-reply")
    assert first.relay_checks is not second.relay_checks
    assert first.relay_checks._plugin is first
    assert second.relay_checks._plugin is second
