"""The overlay half of staff send-as from a Google Workspace mailbox (ss ADR 0089
section 9). The broker half, and every count of Gmail sends, is
``operator/workspace_broker/tests/test_send_as_gmail.py``.

What this side must hold:

* an answer that did not arrive as a verified email never reaches the broker's
  decide verb (an AgentMail inbound whose From fails DKIM/DMARC records no
  origin; ``sender_auth`` and the router's own tests prove that link);
* one person known by two addresses (an alias and its primary) is ONE approver,
  read from the real ``CustomerConfig``;
* the gate passes the Gmail ``thread_id`` through and lets that person ask from
  either address;
* the AgentMail inbound normalizer carries In-Reply-To / References;
* boot refuses a ``via: gmail`` seat that registers a Gmail send tool.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from shared import msgraph_broker, send_as_broker, sender_auth
from shared.customer_config import CustomerConfig
from shared.inbound import SESSION_INBOUND_ORIGIN, SESSION_TAINT, InboundOrigin
from shared.inbound_message import normalize_inbound
from tests.conftest import load_plugin
from tests.test_activation_hook import (
    _ALL_HOOKS,
    _BLOCK,
    _Exit,
    _install_fake_plugins,
    _load_handler,
)

ALIAS = "scott@smd.services"
PRIMARY = "smdurgan@smdurgan.com"
OUTSIDE = "client@client.example"
SESSION = "sess-gmail"
ACT = "1a2b3c4d"
TAG = f"[draft {ACT}]"

SEAT_YAML = f"""
customer_id: smd-office-test
connectors:
  Email:
    adapter: agentmail
    enabled: true
google_auth:
  mode: dwd
  subject: {PRIMARY}
  scopes: [https://www.googleapis.com/auth/gmail.modify]
  managed_mailboxes:
    - address: {PRIMARY}
      send_as: [{ALIAS}, {PRIMARY}]
personas:
  - name: smd-operations
    entitlements:
      exposure:
        external_send_as_staff: confirm
scope:
  inbound_allow_from: [{ALIAS}, {PRIMARY}]
  admins: [ops-admin@smd.services]
  staff_send_as:
    - {{address: {ALIAS}, name: Scott Durgan, via: gmail, mailbox: {PRIMARY}}}
    - {{address: {PRIMARY}, name: Scott Durgan, via: gmail, mailbox: {PRIMARY}}}
"""


@pytest.fixture
def seat(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text(SEAT_YAML)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    SESSION_INBOUND_ORIGIN._origins.clear()
    SESSION_TAINT._tainted.clear()
    yield path
    SESSION_INBOUND_ORIGIN._origins.clear()
    SESSION_TAINT._tainted.clear()


# ---------------------------------------------------------------------------
# One person, two addresses: the real accessor
# ---------------------------------------------------------------------------


def test_one_mailbox_is_one_approver_known_by_both_addresses(seat):
    cfg = CustomerConfig.from_volume()
    assert cfg.staff_send_as_approvers(ALIAS) == {ALIAS, PRIMARY}
    assert cfg.staff_send_as_approvers(PRIMARY.upper()) == {ALIAS, PRIMARY}
    assert cfg.staff_send_as_approvers(OUTSIDE) == set()


def test_a_graph_entry_answers_only_for_itself(tmp_path, monkeypatch):
    path = tmp_path / "customer.yaml"
    path.write_text(
        "customer_id: t\nscope:\n  staff_send_as:\n"
        "    - {address: a@firm.example, name: A}\n    - {address: b@firm.example, name: B}\n"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    assert CustomerConfig.from_volume().staff_send_as_approvers("a@firm.example") == {
        "a@firm.example"
    }


# ---------------------------------------------------------------------------
# The approval reply: only a verified email reaches decide
# ---------------------------------------------------------------------------


@pytest.fixture
def establishment(monkeypatch, tmp_path, seat):
    mod = load_plugin("hermes-smd-establishment")
    decisions: list[dict] = []
    monkeypatch.setattr(mod, "_broker_request", lambda payload: {"ok": True, "pending": []})
    monkeypatch.setattr(
        msgraph_broker,
        "send_as_decide",
        lambda **kw: decisions.append(kw) or {"status": "DISPATCHED"},
    )
    monkeypatch.setattr(
        send_as_broker, "_verdict", lambda *a, **k: pytest.fail("reached the socket")
    )
    monkeypatch.setenv("SMD_ADMIN_POSSESSION_DB_PATH", str(tmp_path / "possession.db"))
    mod._ADMIN_STASH.clear()
    mod._CONFIRMED_STASH.clear()
    mod._READBACK_OWED.clear()
    return mod, decisions


def _agentmail_origin(sender: str) -> None:
    SESSION_INBOUND_ORIGIN.record(
        SESSION,
        InboundOrigin(
            sender_address=sender,
            message_id="<CAreply@mail.gmail.com>",
            inbox_id="smd-office@agentmail.to",
            internet_message_id="<CAreply@mail.gmail.com>",
            conversation_id="am-thread-1",
        ),
    )


def test_an_unverified_answer_never_reaches_decide(establishment):
    mod, decisions = establishment
    # No recorded origin: what the router leaves when sender_auth refuses the From.
    out = mod.on_pre_llm_call(session_id=SESSION, sender_id=PRIMARY, user_message=f"{TAG} send")
    assert decisions == []
    assert "did not arrive as a verified email" in (out or {}).get("context", "")


def test_a_claimed_sender_never_stands_in_for_the_verified_one(establishment):
    """The turn claims to be from him; the verified email is from someone else.
    The decision carries the VERIFIED sender, never the claim, so the broker
    refuses it as a wrong approver (broker test_a_wrong_approver_cannot_send)."""
    mod, decisions = establishment
    _agentmail_origin(OUTSIDE)
    mod.on_pre_llm_call(session_id=SESSION, sender_id=PRIMARY, user_message=f"{TAG} send")
    assert all(d["decided_by"] == OUTSIDE for d in decisions)
    assert not any(d["decided_by"] in (PRIMARY, ALIAS) for d in decisions)


def test_a_verified_answer_carries_the_rfc_id_the_sent_mail_guard_searches(establishment):
    mod, decisions = establishment
    _agentmail_origin(PRIMARY)
    mod.on_pre_llm_call(session_id=SESSION, sender_id=PRIMARY, user_message=f"{TAG} send")
    (decision,) = decisions
    assert decision["decided_by"] == PRIMARY
    assert decision["internet_message_id"] == "<CAreply@mail.gmail.com>"


@pytest.mark.parametrize(
    "results",
    [
        None,
        {},
        {"dkim": "fail", "dkim_domain": "smdurgan.com", "dmarc": "fail"},
        {"dkim": "pass", "dkim_domain": "attacker.example", "dmarc": "none"},
    ],
)
def test_an_unauthenticated_from_for_his_address_is_refused(results):
    ok, _reason = sender_auth.evaluate(PRIMARY, results)
    assert ok is False


# ---------------------------------------------------------------------------
# The gate: thread passthrough, and either of his addresses may ask
# ---------------------------------------------------------------------------


def test_the_gate_proposes_from_the_alias_at_his_primarys_request_with_the_thread(
    seat, monkeypatch
):
    module = load_plugin("hermes-smd-trust").enforce.send_as
    seen: list[dict] = []
    monkeypatch.setattr(module, "_run_gates", lambda *a, **k: (None, []))
    monkeypatch.setattr(
        msgraph_broker,
        "send_as_propose",
        lambda **kw: seen.append(kw) or {"ok": True, "tag": TAG, "act_id": ACT},
    )
    _agentmail_origin(PRIMARY)
    result = module.propose(
        "smd_send_message",
        {
            "from": ALIAS,
            "to": [OUTSIDE],
            "subject": "Re: Next steps",
            "text": "Tuesday works.",
            "thread_id": "18c0ffee",
        },
        SESSION,
    )
    assert "Held for approval" in result["message"], result
    (call,) = seen
    assert call["instructed_by"] == PRIMARY
    assert call["payload"]["from"] == ALIAS and call["payload"]["thread_id"] == "18c0ffee"


def test_the_tool_schema_offers_a_thread_id_beside_from():
    trust = load_plugin("hermes-smd-trust")
    props = trust._SEND_TOOL_SCHEMA["properties"]
    assert "thread_id" in props and "thread_id" not in trust._SEND_TOOL_SCHEMA["required"]


# ---------------------------------------------------------------------------
# What the inbound answers
# ---------------------------------------------------------------------------


def _received(message: dict) -> dict:
    return {
        "type": "message.received",
        "message": {
            "from": OUTSIDE,
            "message_id": "<x@client.example>",
            "inbox_id": "smd-office@agentmail.to",
            "text": "hi",
            **message,
        },
    }


def test_in_reply_to_and_references_ride_from_the_header_set():
    dto = normalize_inbound(
        "agentmail",
        _received(
            {
                "headers": {
                    "In-Reply-To": "<sent@smd.services>",
                    "References": "<a@b> <sent@smd.services>",
                }
            }
        ),
    )
    assert dto.provider_refs["in_reply_to"] == "<sent@smd.services>"
    assert dto.provider_refs["references"] == "<a@b> <sent@smd.services>"


def test_top_level_fields_win_and_absent_means_absent():
    dto = normalize_inbound(
        "agentmail",
        _received(
            {"in_reply_to": "<top@x>", "headers": [{"name": "In-Reply-To", "value": "<hdr@x>"}]}
        ),
    )
    assert dto.provider_refs["in_reply_to"] == "<top@x>"
    bare = normalize_inbound("agentmail", _received({}))
    assert "in_reply_to" not in bare.provider_refs and "references" not in bare.provider_refs


# ---------------------------------------------------------------------------
# Boot: no Gmail send tool beside the approval path
# ---------------------------------------------------------------------------


@pytest.fixture
def no_real_exit(monkeypatch):
    def _fake_exit(code: int):
        raise _Exit(code)

    monkeypatch.setattr(os, "_exit", _fake_exit)


def _boots(monkeypatch, tools: set[str]) -> bool:
    _install_fake_plugins(
        monkeypatch, hooks=_ALL_HOOKS, invoke_results=_BLOCK, workspace_tools=tools
    )
    handler = _load_handler()
    try:
        asyncio.run(handler.handle("gateway:startup", {}))
    except _Exit as ei:
        assert ei.code == 1
        return False
    return True


_WORKSPACE = {
    "workspace_gmail_search",
    "workspace_gmail_get",
    "workspace_gmail_create_draft",
    "workspace_gmail_modify",
    "workspace_gmail_archive",
    "workspace_calendar_list",
    "workspace_calendar_get",
    "workspace_calendar_create_draft",
    "workspace_calendar_update_draft",
    "workspace_drive_list",
    "workspace_drive_get",
    "workspace_drive_export",
    "workspace_docs_create",
    "workspace_docs_get",
    "workspace_docs_append",
    "workspace_sheets_create",
    "workspace_sheets_get_values",
    "workspace_sheets_update_values",
}


@pytest.mark.parametrize("tool", ["workspace_gmail_send", "mcp_gmail_send_message"])
def test_FALSIFIER_a_gmail_send_as_seat_with_a_gmail_send_tool_refuses_to_boot(
    monkeypatch,
    no_real_exit,
    seat,
    tool,
):
    assert _boots(monkeypatch, _WORKSPACE | {tool}) is False


def test_a_gmail_send_as_seat_without_one_boots(monkeypatch, no_real_exit, seat):
    assert _boots(monkeypatch, set(_WORKSPACE)) is True


def test_a_graph_seat_is_not_held_to_it(monkeypatch, no_real_exit, tmp_path):
    path = tmp_path / "customer.yaml"
    path.write_text(
        "customer_id: t\nscope:\n  staff_send_as:\n    - {address: a@firm.example, name: A}\n"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    assert _boots(monkeypatch, _WORKSPACE | {"mcp_gmail_send_message"}) is True
