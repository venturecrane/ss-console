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

from workspace_broker.request_errors import PROTOCOL_EXCEPTIONS, error_response_for
from workspace_broker import server


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


def test_protocol_set_is_exactly_the_two_the_handler_raises():
    assert PROTOCOL_EXCEPTIONS == (PermissionError, ValueError)
