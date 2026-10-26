"""The web_derived trust level (ADR 0099).

A turn that read the web only through the sealed reader is ``web_derived``:
ranked between internal and the external classes, monotonic, tainted to every
plugin that asks, and allowed exactly one sensitive action by the trust gate:
a staff email whose every recipient is an authored ``scope.admins`` address.

The gate tests drive the REAL ``evaluate_tool_call`` with only the persona,
roster, admins and outbound scans stubbed, so a refusal here is the taint
gate's own.
"""

from __future__ import annotations

import logging

import pytest

from shared import inbound
from shared.inbound import (
    SESSION_TAINT,
    TRUST_CLASS_INTERNAL,
    TRUST_CLASS_KNOWN_EXTERNAL,
    TRUST_CLASS_UNKNOWN_EXTERNAL,
    TRUST_CLASS_WEB_DERIVED,
    SessionTaint,
)
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce
AC = enforce.ActionClass
C = enforce.Ceiling

SID = "sess-web-derived"
ADMIN = "admin@firm.example"
ADMIN2 = "second.admin@firm.example"
STAFF = "paralegal@firm.example"
OUTSIDER = "someone@elsewhere.example"


@pytest.fixture(autouse=True)
def seat(monkeypatch):
    SESSION_TAINT._tainted.clear()
    monkeypatch.setattr(
        enforce,
        "_resolve_persona_exposure",
        lambda slug="": {
            AC.EXTERNAL_SEND_INTERNAL: C.AUTONOMOUS,
            AC.EXTERNAL_SEND: C.AUTONOMOUS,
            AC.INTERNAL_WRITE: C.AUTONOMOUS,
            AC.DESTRUCTIVE: C.AUTONOMOUS,
            AC.COMMITMENT: C.AUTONOMOUS,
            AC.CODE_EXECUTION: C.AUTONOMOUS,
        },
    )
    monkeypatch.setattr(enforce, "_resolve_roster", lambda: [ADMIN, ADMIN2, STAFF])
    monkeypatch.setattr(enforce, "_resolve_typed_roster", lambda: [])
    monkeypatch.setattr(enforce, "_resolve_admins", lambda: [ADMIN, ADMIN2])
    monkeypatch.setattr(enforce, "_resolve_vertical_floors", lambda: {})
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: "marketing")
    monkeypatch.setattr(trust.outbound, "check_outbound_draft", lambda **_k: None)
    monkeypatch.setattr(trust.outbound, "check_outbound_send", lambda **_k: None)
    yield
    SESSION_TAINT._tainted.clear()


def _send(to, cc=None):
    args = {"to": to, "subject": "Monthly slate", "text": "Here are this month's draft topics."}
    if cc:
        args["cc"] = cc
    return enforce.evaluate_tool_call("smd_send_message", args, "seat", session_id=SID)


# ---------------------------------------------------------------------------
# The register
# ---------------------------------------------------------------------------


def test_web_derived_ranks_between_internal_and_external_and_is_tainted():
    t = SessionTaint()
    t.mark("s", TRUST_CLASS_WEB_DERIVED)
    assert t.trust_class("s") == TRUST_CLASS_WEB_DERIVED
    assert t.is_tainted("s")


@pytest.mark.parametrize("later", [TRUST_CLASS_KNOWN_EXTERNAL, TRUST_CLASS_UNKNOWN_EXTERNAL])
def test_a_later_external_read_escalates_and_nothing_lowers(later):
    t = SessionTaint()
    t.mark("s", TRUST_CLASS_WEB_DERIVED)
    t.mark("s", later)
    assert t.trust_class("s") == later
    t.mark("s", TRUST_CLASS_WEB_DERIVED)
    t.mark("s", TRUST_CLASS_INTERNAL)
    assert t.trust_class("s") == later


def test_no_inbound_envelope_can_claim_web_derived():
    env = inbound.make_envelope(
        content="x", source="webhook", surface="webhook", trust_class=TRUST_CLASS_WEB_DERIVED
    )
    assert env.trust_class == TRUST_CLASS_UNKNOWN_EXTERNAL


# ---------------------------------------------------------------------------
# enforce(): the policy core
# ---------------------------------------------------------------------------

_EXPOSURE = {cls: C.AUTONOMOUS for cls in enforce._TAINT_GATED_CLASSES}


@pytest.mark.parametrize("action", sorted(enforce._TAINT_GATED_CLASSES, key=lambda a: a.value))
def test_every_gated_class_is_refused_on_web_derived_without_admin_recipients(action):
    decision = enforce.enforce(
        action=action,
        exposure=_EXPOSURE,
        tool_name="t",
        current_turn_approval=True,
        inbound_trust_class=TRUST_CLASS_WEB_DERIVED,
    )
    assert decision.allowed is False
    assert "web_derived" in decision.reason


@pytest.mark.parametrize(
    "action",
    sorted(enforce._TAINT_GATED_CLASSES - {AC.EXTERNAL_SEND_INTERNAL}, key=lambda a: a.value),
)
def test_admin_flag_lifts_nothing_but_the_staff_send(action):
    decision = enforce.enforce(
        action=action,
        exposure=_EXPOSURE,
        tool_name="t",
        current_turn_approval=True,
        inbound_trust_class=TRUST_CLASS_WEB_DERIVED,
        admin_recipients=True,
    )
    assert decision.allowed is False
    assert "web_derived" in decision.reason


def test_admin_staff_send_passes_the_gate_and_says_so():
    decision = enforce.enforce(
        action=AC.EXTERNAL_SEND_INTERNAL,
        exposure=_EXPOSURE,
        tool_name="t",
        inbound_trust_class=TRUST_CLASS_WEB_DERIVED,
        admin_recipients=True,
    )
    assert decision.allowed is True
    assert enforce.WEB_DERIVED_ADMIN_SEND in decision.reason
    assert "web_derived" in decision.reason


def test_admin_staff_send_still_obeys_its_own_ceiling():
    decision = enforce.enforce(
        action=AC.EXTERNAL_SEND_INTERNAL,
        exposure={AC.EXTERNAL_SEND_INTERNAL: C.DRAFT_FOR_REVIEW},
        tool_name="t",
        inbound_trust_class=TRUST_CLASS_WEB_DERIVED,
        admin_recipients=True,
    )
    assert decision.allowed is False
    assert decision.audit_action == "draft"


@pytest.mark.parametrize("cls", [TRUST_CLASS_KNOWN_EXTERNAL, TRUST_CLASS_UNKNOWN_EXTERNAL])
def test_the_admin_exception_exists_only_for_web_derived(cls):
    decision = enforce.enforce(
        action=AC.EXTERNAL_SEND_INTERNAL,
        exposure=_EXPOSURE,
        tool_name="t",
        inbound_trust_class=cls,
        admin_recipients=True,
    )
    assert decision.allowed is False


def test_internal_write_and_read_stay_allowed_on_web_derived():
    for action in (AC.INTERNAL_WRITE, AC.READ):
        decision = enforce.enforce(
            action=action,
            exposure={AC.INTERNAL_WRITE: C.AUTONOMOUS},
            tool_name="t",
            inbound_trust_class=TRUST_CLASS_WEB_DERIVED,
        )
        assert decision.allowed is True, action


# ---------------------------------------------------------------------------
# evaluate_tool_call: recipients decided in code from the trusted config
# ---------------------------------------------------------------------------


def test_send_to_an_admin_on_a_web_derived_turn_is_allowed():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    assert _send([ADMIN]) is None


def test_send_to_every_admin_is_allowed():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    assert _send([ADMIN], cc=[ADMIN2]) is None


def test_send_to_a_rostered_non_admin_is_refused():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    result = _send([STAFF])
    assert result is not None and result["action"] == "block"
    assert "web_derived" in result["message"]


def test_an_admin_plus_anyone_else_is_refused():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    for other in (STAFF, OUTSIDER):
        result = _send([ADMIN], cc=[other])
        assert result is not None and result["action"] == "block", other


def test_an_outsider_is_refused():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    result = _send([OUTSIDER])
    assert result is not None and result["action"] == "block"


def test_a_lookalike_address_is_not_an_admin():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    for addr in ("ADMIN@firm.example.evil", "admin@firm.example.co", f"Admin <{OUTSIDER}>"):
        result = _send([addr])
        assert result is not None and result["action"] == "block", addr


def test_a_later_external_read_closes_the_admin_door():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    assert _send([ADMIN]) is None
    SESSION_TAINT.mark(SID, TRUST_CLASS_UNKNOWN_EXTERNAL)
    result = _send([ADMIN])
    assert result is not None and result["action"] == "block"


def test_no_admins_authored_means_no_exception(monkeypatch):
    monkeypatch.setattr(enforce, "_resolve_admins", lambda: [])
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    result = _send([ADMIN])
    assert result is not None and result["action"] == "block"


def test_a_clean_turn_is_unchanged():
    assert _send([STAFF]) is None


def test_the_decision_row_says_web_derived(monkeypatch):
    rows: list[dict] = []
    real = enforce._record_decision

    def spy(*a, **k):
        rows.append(k)
        return real(*a, **k)

    monkeypatch.setattr(enforce, "_record_decision", spy)
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    _send([ADMIN])
    _send([STAFF])
    reasons = [r.get("reason", "") for r in rows]
    assert any(enforce.WEB_DERIVED_ADMIN_SEND in r for r in reasons)
    assert any("refused" in r and "web_derived" in r for r in reasons)


def test_resolved_send_class_mirrors_the_gate():
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    args = {"to": [ADMIN], "text": "x"}
    assert enforce.resolved_send_class("smd_send_message", args, SID) is AC.EXTERNAL_SEND_INTERNAL
    args = {"to": [STAFF], "text": "x"}
    assert enforce.resolved_send_class("smd_send_message", args, SID) is AC.EXTERNAL_SEND


def test_a_recipient_locked_reply_drafts_through_internal_write():
    """The reply relay drafts via create_draft (INTERNAL_WRITE) and pins the
    recipient to the verified sender; a web_derived turn keeps that path."""
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    assert enforce.classify_tool("mcp_agentmail_create_draft").action_class is AC.INTERNAL_WRITE
    result = enforce.evaluate_tool_call(
        "mcp_agentmail_create_draft",
        {"to": [STAFF], "subject": "Re: slate", "text": "Drafted."},
        "seat",
        session_id=SID,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Every plugin that asks is_tainted sees a web_derived turn as tainted
# ---------------------------------------------------------------------------


def test_record_store_treats_web_derived_as_tainted():
    record_store = load_plugin("hermes-smd-record-store")
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    assert record_store._turn_is_tainted(SID) is True
    result = record_store.on_pre_tool_call(
        tool_name=record_store.TOOL_WRITE,
        args={"store": "notes", "content": "x"},
        session_id=SID,
    )
    assert result is not None and result["action"] == "block"


def test_calendar_refuses_writes_on_web_derived():
    calendar = load_plugin("hermes-smd-calendar")
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    for tool in sorted(calendar.WRITE_TOOLS):
        result = calendar.on_pre_tool_call(tool_name=tool, args={}, session_id=SID)
        assert result is not None and result["action"] == "block", tool


def test_peer_memory_refuses_capture_on_web_derived(monkeypatch, caplog):
    peer = load_plugin("hermes-smd-peer-memory")
    monkeypatch.setattr(peer, "_SPACE_MODE", False)
    monkeypatch.setattr(peer, "_D1", object())
    monkeypatch.setattr(peer, "_CUSTOMER_SLUG", "seat")
    SESSION_TAINT.mark(SID, TRUST_CLASS_WEB_DERIVED)
    with caplog.at_level(logging.WARNING):
        peer.on_post_tool_call(tool_name=peer.TOOL_NAME, args={}, result="{}", session_id=SID)
    assert "refused on tainted session" in caplog.text


def test_no_taint_consumer_matches_a_specific_external_class():
    """What makes web_derived tainted everywhere: consumers ask "is this not
    internal", never "is this unknown_external". A consumer that compared
    against one external class would read web_derived as clean."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(
        r"trust_class\([^)]*\)\s*==\s*(?:inbound\.)?TRUST_CLASS_(?:KNOWN|UNKNOWN)_EXTERNAL"
        r"|==\s*[\"'](?:known|unknown)_external[\"']"
    )
    offenders = []
    for path in list((root / "plugins").rglob("*.py")) + list((root / "shared").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert offenders == []


def test_only_the_sealed_reader_marks_web_derived():
    """Inertness on every seat that does not author sealed_reader: nothing but
    the research plugin (registered only there) ever marks a turn web_derived,
    so the graded gate is unreachable everywhere else."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    mark = re.compile(r"\.mark\([^)]*TRUST_CLASS_WEB_DERIVED")
    markers = {
        str(path.relative_to(root))
        for path in list((root / "plugins").rglob("*.py")) + list((root / "shared").rglob("*.py"))
        if mark.search(path.read_text(encoding="utf-8"))
    }
    assert markers == {"plugins/hermes-smd-research/__init__.py"}
