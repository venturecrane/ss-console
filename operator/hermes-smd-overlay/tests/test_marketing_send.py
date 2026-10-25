"""A marketing seat's scheduled drafts reach its administrators on a tainted turn, and nothing else does.

``shared.marketing_send`` mints ``CodeFixedRecipients`` for the six scheduled
marketing skills on a ``seat.role: marketing`` seat, with recipients computed
from the seat's own config (``scope.admins`` that are exact
``scope.inbound_allow_from`` entries). These tests drive the REAL gate:
``marketing_draft_send`` -> ``shared.send_dispatch`` -> the trust plugin's
``_dispatch_internal_message`` -> ``evaluate_tool_call``. Only the transport,
the outbound scans and the spec floors are stubbed, so a refusal here is the
taint gate's own (the shape of ``test_brief_taint_exemption.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from shared import inbound, marketing_send, send_dispatch, spec_gate
from shared.action_classes import ActionClass, classify_tool
from shared.customer_config import CustomerConfig
from shared.inbound import SESSION_TAINT, TRUST_CLASS_UNKNOWN_EXTERNAL
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce

REVIEWER = "reviewer@firm.example"
STAFF = "staff@firm.example"
SMD = "ops@smd.example"
OUTSIDER = "someone@elsewhere.example"
SESSION = "cron_abc123def456_20261101_071700"
PERSONA = "marketing"
TEXT = "Two drafts for review.\n\n--- DRAFT social ---\nExample text.\n--- END DRAFT ---"


def _config(
    *, role: str | None = "marketing", admins=(REVIEWER, SMD), roster=(REVIEWER, STAFF)
) -> CustomerConfig:
    data = {
        "customer_slug": "example-marketing",
        "customer_name": "Example",
        "vertical": "law-firm",
        "seat": {"product": "operator", **({"role": role} if role else {})},
        "scope": {"admins": list(admins), "inbound_allow_from": list(roster)},
        "personas": [
            {
                "slug": PERSONA,
                "skills": [
                    {"name": skill, "enabled": True, "settings": {"recipients": "roster"}}
                    for skill in sorted(marketing_send.MARKETING_CRON_SKILLS)
                ]
                + [{"name": "statute-watch", "enabled": True}],
                "cron": [
                    {"skill": skill, "schedule": "17 7 * * 1"}
                    for skill in marketing_send.MARKETING_CRON_SKILLS
                ],
            }
        ],
    }
    return CustomerConfig(data)


def _routine(skill: str = "social-post-draft", *, job_name: str | None = None):
    return lambda _s: SimpleNamespace(
        job_id="abc123def456",
        job_name=job_name or f"op-managed:{PERSONA}:{skill}",
        persona=PERSONA,
        skill=skill,
        deliver=None,
    )


@pytest.fixture(autouse=True)
def seat(monkeypatch):
    """A seat whose staff send is autonomous, with a recording transport."""
    monkeypatch.setattr(inbound, "SESSION_INBOUND_ORIGIN", inbound.SessionInboundOrigin())
    SESSION_TAINT._tainted.clear()
    marketing_send._reset_for_tests()
    monkeypatch.setattr(
        enforce,
        "_resolve_persona_exposure",
        lambda slug="": {
            enforce.ActionClass.EXTERNAL_SEND_INTERNAL: enforce.Ceiling.AUTONOMOUS,
            enforce.ActionClass.EXTERNAL_SEND: enforce.Ceiling.AUTONOMOUS,
        },
    )
    monkeypatch.setattr(enforce, "_resolve_roster", lambda: [REVIEWER, STAFF])
    monkeypatch.setattr(enforce, "_resolve_typed_roster", lambda: [])
    monkeypatch.setattr(enforce, "_resolve_vertical_floors", lambda: {})
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: PERSONA)
    monkeypatch.setattr(spec_gate, "check_structure_floor", lambda **_k: None)
    monkeypatch.setattr(spec_gate, "check_spec_gate", lambda **_k: None)
    monkeypatch.setattr(trust.outbound, "check_outbound_draft", lambda **_k: None)
    monkeypatch.setattr(trust.outbound, "check_outbound_send", lambda **_k: None)
    monkeypatch.setattr(trust, "get_secret", lambda _k: "example-marketing")
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
    marketing_send._reset_for_tests()


def _taint() -> None:
    """What a web search or a YouTube read does to the turn."""
    SESSION_TAINT.mark(SESSION, TRUST_CLASS_UNKNOWN_EXTERNAL)


def _send(cfg: CustomerConfig | None = None, *, routine=None, args=None) -> dict:
    return json.loads(
        marketing_send.marketing_draft_send(
            args if args is not None else {"subject": "Social posts for the week", "text": TEXT},
            session_id=SESSION,
            load_config=lambda: cfg or _config(),
            routine=routine or _routine(),
        )
    )


# ---------------------------------------------------------------------------
# (a) a tainted scheduled run mails the fixed administrators
# ---------------------------------------------------------------------------


def test_a_tainted_scheduled_run_mails_the_fixed_administrator(seat):
    _taint()
    out = _send()
    assert out["status"] == "sent", out
    [call] = seat
    # SMD is an admin but not on the roster: only the admin on BOTH lists.
    assert call["payload"]["to"] == [REVIEWER]
    assert call["audit_extra"]["taint_exempt"] == "code_fixed_recipients"
    assert call["audit_extra"]["skill_name"] == "social-post-draft"


@pytest.mark.parametrize("skill", sorted(marketing_send.MARKETING_CRON_SKILLS))
def test_every_marketing_cron_skill_mints_it(seat, skill):
    _taint()
    assert _send(routine=_routine(skill))["status"] == "sent"


def test_one_email_per_run(seat):
    _taint()
    assert _send()["status"] == "sent"
    again = _send()
    assert again["status"] == "refused" and "already gone out" in again["reason"]
    assert len(seat) == 1


# ---------------------------------------------------------------------------
# (b) it cannot mail anyone else
# ---------------------------------------------------------------------------


def test_the_tool_takes_no_address():
    assert set(marketing_send.SCHEMA["properties"]) == {"subject", "text"}
    assert marketing_send.SCHEMA["additionalProperties"] is False


def test_an_address_in_the_arguments_is_ignored(seat):
    _taint()
    out = _send(args={"subject": "s", "text": TEXT, "to": [OUTSIDER]})
    # The handler reads only subject and text; the recipients are still the fixed set.
    assert out["status"] == "sent"
    assert seat[0]["payload"]["to"] == [REVIEWER]


def test_a_domain_roster_grant_admits_no_one():
    cfg = _config(admins=(REVIEWER,), roster=("@firm.example",))
    assert marketing_send.fixed_recipients(cfg) == ()


def test_an_admin_off_the_roster_is_never_a_recipient():
    assert marketing_send.fixed_recipients(_config()) == (REVIEWER,)


def test_no_admin_on_the_roster_sends_nothing(seat):
    _taint()
    out = _send(_config(admins=(SMD,)))
    assert out["status"] == "refused" and "administrator" in out["reason"]
    assert seat == []


def test_the_capability_cannot_be_steered_to_an_outsider(seat):
    _taint()
    cap = send_dispatch.CodeFixedRecipients(to=(REVIEWER,))
    result = send_dispatch.dispatch(
        to=[OUTSIDER],
        subject="s",
        text="t",
        anchor=None,
        lane=None,
        session_id=SESSION,
        code_fixed_recipients=cap,
    )
    assert not result.sent
    assert seat == []


def test_the_model_callable_send_is_still_refused_on_a_tainted_turn():
    _taint()
    block = enforce.evaluate_tool_call(
        "smd_send_message", {"to": [REVIEWER], "text": "b"}, "smd", session_id=SESSION
    )
    assert block is not None and "untrusted inbound" in block["message"]


# ---------------------------------------------------------------------------
# (c) inert anywhere else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [None, "couples", "paralegal"])
def test_inert_on_a_seat_that_is_not_marketing(seat, role):
    _taint()
    out = _send(_config(role=role))
    assert out["status"] == "refused" and "marketing seat" in out["reason"]
    assert seat == []


def test_inert_for_a_skill_that_is_not_a_marketing_cron_skill(seat):
    _taint()
    out = _send(routine=_routine("statute-watch"))
    assert out["status"] == "refused"
    assert seat == []


def test_inert_on_a_turn_that_is_not_a_scheduled_run(seat):
    _taint()
    out = _send(routine=lambda _s: None)
    assert out["status"] == "refused" and "scheduled" in out["reason"]
    assert seat == []


def test_inert_for_an_unmanaged_job(seat):
    _taint()
    out = _send(routine=_routine(job_name="hand-made-job"))
    assert out["status"] == "refused"
    assert seat == []


def test_inert_for_a_skill_the_seat_does_not_enable(seat):
    _taint()
    cfg = _config()
    for row in cfg.personas[0]["skills"]:
        if row["name"] == "social-post-draft":
            row["enabled"] = False
    out = _send(cfg)
    assert out["status"] == "refused"
    assert seat == []


# ---------------------------------------------------------------------------
# (d) the tool is registered, classified and kept out of job wakes
# ---------------------------------------------------------------------------


def test_registered_and_classified():
    assert "marketing_draft_send" in load_plugin("hermes-smd-escalation").TOOLS
    assert classify_tool("marketing_draft_send").action_class is ActionClass.INTERNAL_WRITE
    assert classify_tool("marketing_draft_send").unmapped is False


def test_forbidden_in_a_job_wake():
    binding = load_plugin("hermes-smd-reply").binding
    assert "marketing_draft_send" in binding.WAKE_FORBIDDEN_TOOLS
