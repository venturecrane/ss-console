"""The broker's per-request exception reply is a bounded vocabulary.

``RequestHandler.handle`` catches everything the verb handler raises and writes
one reply. Before 2026-09-10 that reply carried ``str(exc)`` for ANY exception,
so a ``KeyError`` from an internal dict, an ``OSError`` carrying a path, or a
vendor client's exception carrying a URL became a message to whatever holds
the socket group (2026-09-10 review, Security LOW 7). Now only the exceptions
the broker raises on purpose keep their message; the rest are logged and
replied to as ``internal_error``.
"""

from __future__ import annotations

import json
import logging

import io
import socket
import struct
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from workspace_broker.agentmail_ops import AgentMailRefused, AgentMailTransportError
from workspace_broker.msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from workspace_broker.request_errors import PROTOCOL_EXCEPTIONS, BrokerRefusal, error_response_for
from workspace_broker import server
from workspace_broker.tests import test_agentmail_send as am
from workspace_broker.tests import test_msgraph_send as mg


def test_server_request_handler_uses_the_shared_mapping():
    # The mapping is only a control if the socket handler calls it.
    assert server.error_response_for is error_response_for


def test_permission_error_keeps_its_gate_message():
    reply = error_response_for(PermissionError("verb 'x' requires the gateway uid"))
    assert reply == {
        "ok": False,
        "error": "PermissionError",
        "message": "verb 'x' requires the gateway uid",
    }


def test_value_error_keeps_its_field_message():
    reply = error_response_for(ValueError("missing field: action"))
    assert reply["error"] == "ValueError"
    assert reply["message"] == "missing field: action"


def test_malformed_json_is_a_value_error_and_stays_readable():
    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        reply = error_response_for(exc)
    assert reply["ok"] is False
    assert reply["error"] == "JSONDecodeError"


def test_unexpected_exception_is_masked_and_logged(caplog):
    secret_path = "/opt/data/profiles/pilot/token.json"
    with caplog.at_level(logging.ERROR, logger="workspace_broker"):
        reply = error_response_for(OSError(f"cannot open {secret_path}"))
    assert reply == {
        "ok": False,
        "error": "internal_error",
        "message": "internal error; see broker log",
    }
    assert secret_path not in json.dumps(reply)
    assert any("OSError" in record.getMessage() for record in caplog.records)


def test_key_error_is_not_protocol():
    # A KeyError names a key; in a broker that holds vendor payloads that key
    # can be data, so it is an accident, not a reply.
    reply = error_response_for(KeyError("refresh_token"))
    assert reply["error"] == "internal_error"
    assert "refresh_token" not in reply["message"]


def test_protocol_set_is_exactly_the_three_the_broker_raises_on_purpose():
    assert PROTOCOL_EXCEPTIONS == (PermissionError, ValueError, BrokerRefusal)


# ---------------------------------------------------------------------------
# The transmit verbs' deliberate refusals (review 2026-10-06 N9)
# ---------------------------------------------------------------------------

_TRANSMIT_EXCEPTIONS = (MsGraphRefused, MsGraphTransportError, AgentMailRefused, AgentMailTransportError)


@pytest.mark.parametrize("cls", _TRANSMIT_EXCEPTIONS, ids=lambda c: c.__name__)
def test_each_transmit_refusal_keeps_its_name_and_message(cls):
    message = "1 recipient(s) are not on this seat's authored counterparty surface"
    reply = error_response_for(cls(message))
    assert reply == {"ok": False, "error": cls.__name__, "message": message}


@pytest.mark.parametrize("cls", _TRANSMIT_EXCEPTIONS, ids=lambda c: c.__name__)
def test_each_transmit_refusal_is_still_a_runtime_error(cls):
    # Existing `except RuntimeError` callers must see no change.
    assert issubclass(cls, RuntimeError)
    assert issubclass(cls, BrokerRefusal)


def test_the_attachment_refused_prefix_survives_intact():
    # The overlay's send-without-attachment retry keys on this text
    # (hermes-smd-overlay outbound_send.py: `"attachment" in str(exc).lower()`).
    message = "attachment refused: attachment sha256 does not match its content"
    reply = error_response_for(MsGraphRefused(message))
    assert reply["error"] == "MsGraphRefused"
    assert reply["message"] == message


def test_a_plain_runtime_error_is_still_masked():
    reply = error_response_for(RuntimeError("https://vendor.example/?token=abc"))
    assert reply["error"] == "internal_error"
    assert "token" not in reply["message"]


# ---------------------------------------------------------------------------
# Through the real socket reply path. The ops tests assert with pytest.raises
# in-process, which is how N9 hid: the exception was right, the REPLY was not.
# ---------------------------------------------------------------------------


def _socket_reply(broker, request: dict, monkeypatch, *, pid: int, uid: int) -> dict:
    """Drive server.RequestHandler.handle with a fake connection carrying peer creds."""
    # SO_PEERCRED is Linux-only; the fake socket ignores the option number.
    monkeypatch.setattr(socket, "SO_PEERCRED", getattr(socket, "SO_PEERCRED", 17), raising=False)
    creds = struct.pack("3i", pid, uid, uid)
    handler = server.RequestHandler.__new__(server.RequestHandler)
    handler.request = SimpleNamespace(getsockopt=lambda *_a: creds)
    handler.rfile = io.BytesIO(json.dumps(request).encode() + b"\n")
    handler.wfile = io.BytesIO()
    handler.server = SimpleNamespace(broker=broker)
    handler.handle()
    return json.loads(handler.wfile.getvalue().decode())


def test_a_msgraph_recipient_refusal_reaches_the_peer_by_name(tmp_path: Path, monkeypatch):
    broker = mg._broker(tmp_path, mg.FakeGraph())
    reply = _socket_reply(
        broker,
        {"action": "msgraph_send", "payload": {"to": [mg.UNAUTHORED], "body_text": "x"}},
        monkeypatch,
        pid=mg.GATEWAY_PID,
        uid=mg.AGENT_UID,
    )
    assert reply["ok"] is False
    assert reply["error"] == "MsGraphRefused"
    assert "authored counterparty surface" in reply["message"]


def test_a_msgraph_attachment_refusal_reaches_the_peer_with_its_prefix(tmp_path: Path, monkeypatch):
    broker = mg._broker(tmp_path, mg.FakeGraph())
    bad = {"name": "report.xlsx", "content_type": "text/plain", "content_b64": "eA==", "sha256": "0" * 64}
    reply = _socket_reply(
        broker,
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "x", "attachments": [bad]},
        },
        monkeypatch,
        pid=mg.GATEWAY_PID,
        uid=mg.AGENT_UID,
    )
    assert reply["error"] == "MsGraphRefused"
    assert reply["message"].startswith("attachment refused:")


def test_an_agentmail_attachment_refusal_reaches_the_peer_with_its_prefix(tmp_path: Path, monkeypatch):
    broker = am._broker(tmp_path, am.FakeHTTP())
    reply = _socket_reply(
        broker,
        {
            "action": "agentmail_send",
            "payload": {"to": ["scott@smd.services"], "text": "x", "attachments": [{"name": "a.xlsx"}]},
        },
        monkeypatch,
        pid=am.GATEWAY_PID,
        uid=am.AGENT_UID,
    )
    assert reply["error"] == "AgentMailRefused"
    assert reply["message"].startswith("attachment refused:")


def test_a_msgraph_transport_failure_names_the_class_and_no_vendor_text(tmp_path: Path, monkeypatch):
    def _boom(request, timeout=None):
        if request.full_url.endswith("/token"):
            return mg._Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        raise urllib.error.URLError("proxy said: secret-vendor-body token=abc")

    customer, credential, _read = mg._seat(tmp_path)
    broker = mg._broker(tmp_path, mg.FakeGraph())
    broker.msgraph = MsGraphOps(credential, customer, opener=_boom, sleep=lambda _s: None)
    reply = _socket_reply(
        broker,
        {"action": "msgraph_send", "payload": {"to": ["scott@smd.services"], "body_text": "x"}},
        monkeypatch,
        pid=mg.GATEWAY_PID,
        uid=mg.AGENT_UID,
    )
    assert reply["error"] == "MsGraphTransportError"
    assert reply["message"].endswith("failed: URLError")
    assert "secret-vendor-body" not in reply["message"]
    assert "token=abc" not in reply["message"]
