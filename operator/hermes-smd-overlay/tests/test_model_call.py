"""shared/model_call.py: the one no-tools Messages client (the sealed reader's
and the mailbox keeper's). The broker's copy is byte-identical
(operator/bin/gen-skill-shared.py --check)."""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from shared import model_call


def test_a_call_carries_the_key_it_was_given_and_no_tools() -> None:
    seen = {}

    def post(host, method, path, headers, body, deadline):
        seen.update(host=host, method=method, path=path, headers=headers, body=json.loads(body))
        return {
            "content": [{"type": "text", "text": '{"ok": true}'}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    out = model_call.call(
        {"model": "m", "messages": []}, key="sk-given", deadline=time.monotonic() + 5, post=post
    )
    assert (
        seen["host"] == model_call.API_HOST
        and seen["path"] == model_call.API_PATH
        and seen["method"] == "POST"
    )
    assert (
        seen["headers"]["x-api-key"] == "sk-given"
        and seen["headers"]["anthropic-version"] == model_call.API_VERSION
    )
    assert model_call.response_text(out) == '{"ok": true}'
    with pytest.raises(model_call.ModelCallError, match="tools"):
        model_call.call(
            {"model": "m", "tools": []}, key="k", deadline=time.monotonic() + 5, post=post
        )
    with pytest.raises(model_call.ModelCallError, match="no model key"):
        model_call.call({"model": "m"}, key="", deadline=time.monotonic() + 5, post=post)


def test_the_broker_transport_reaches_one_host_only() -> None:
    with pytest.raises(model_call.ModelCallError, match="any host"):
        model_call.https_post(
            "evil.example", "POST", "/v1/messages", {}, b"{}", time.monotonic() + 5
        )
    with pytest.raises(model_call.ModelCallError, match="deadline"):
        model_call.https_post(
            model_call.API_HOST, "POST", "/v1/messages", {}, b"{}", time.monotonic() - 1
        )


def test_cost_is_fractional() -> None:
    rates = {"input_per_million_cents": 200, "output_per_million_cents": 1000}
    cents = model_call.usage_cents({"usage": {"input_tokens": 3000, "output_tokens": 600}}, rates)
    assert cents == pytest.approx(1.2)


def test_the_json_reading_is_strict() -> None:
    assert model_call.parse_json_object('{"kind": "notice"}') == {"kind": "notice"}
    assert model_call.parse_json_object('```json\n{"kind": "notice"}\n```') == {"kind": "notice"}
    assert model_call.parse_json_object('Sure: {"kind": "notice"}') is None
    assert model_call.parse_json_object('{"kind": "notice"} trailing') is None
    assert model_call.parse_json_object("[1]") is None
    assert model_call.parse_json_object("{not json}") is None


def test_the_credential_round_trips_at_0600_and_carries_the_pricing_table(tmp_path) -> None:
    path = tmp_path / "broker" / "model.json"
    assert model_call.materialize_credential(path, environ={}) is False and not path.exists()
    assert model_call.materialize_credential(path, environ={"ANTHROPIC_API_KEY": "sk-seat"}) is True
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    key, rates = model_call.load_credential(path)
    assert key == "sk-seat" and rates["claude-sonnet-5-5"]["input_per_million_cents"] == 200
    path.write_text("{}")
    with pytest.raises(model_call.ModelCallError, match="malformed"):
        model_call.load_credential(path)
