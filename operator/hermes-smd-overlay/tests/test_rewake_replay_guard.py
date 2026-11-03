"""An SMD re-wake passes the router's replay guard; a duplicate and a forgery do not.

The defect (A&P, 2026-11-03 00:01:50Z): ``shared.msgraph_replay --message-id``
got HTTP 202 from the adapter, then the webhook router dropped the delivery as
``replayed event id 'AAMk...'`` because the first intake of that email had put
its Graph id in the router's 1 h replay cache. No turn ran and the tool said
"accepted". These tests drive the real router hook with the real envelope bytes
the replay tool builds, and the replay tool against the router's outcome journal.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from shared import msgraph_poller, msgraph_replay, rewake_outcome
from tests.test_webhook_router import (
    YAML_WITH_TWO_TRIGGERS,
    FakeD1Client,
    _sign,
    load_plugin,
)

_SECRET = "topsecret-test-key"
_MAILBOX = "op@client.example"
_SKIP = {"action": "skip"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(msgraph_poller, "_baseline_now", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setenv("SMD_REWAKE_OUTCOME_DIR", str(tmp_path / "outcomes"))
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp_path / "requests.db"))
    rewake_outcome.reset()
    yield
    rewake_outcome.reset()


def _raw(mid: str = "AAMk-original") -> dict:
    return {
        "id": mid,
        "conversationId": f"conv-{mid}",
        "from": {"emailAddress": {"address": "attorney@client.example"}},
        "toRecipients": [{"emailAddress": {"address": _MAILBOX}}],
        "subject": "Records",
        "body": {"contentType": "Text", "content": "please send the records"},
        "receivedDateTime": "2026-07-24T10:00:00Z",
    }


@pytest.fixture
def router(fake_ctx, monkeypatch, tmp_path):
    path = tmp_path / "customer.yaml"
    path.write_text(YAML_WITH_TWO_TRIGGERS)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    monkeypatch.setenv("SMD_CUSTOMER_SLUG", "acme")
    monkeypatch.setenv("SMD_D1_AUDIT_BINDING", "CUSTOMER_DB")
    monkeypatch.setenv("SMD_WEBHOOK_SIGNING_SECRET", _SECRET)
    mod = load_plugin("hermes-smd-webhook-router")
    mod.register(fake_ctx)
    mod._D1_CLIENT = FakeD1Client()
    return mod


def _rewake_id(n: int) -> str:
    return hashlib.sha256(f"rewake-{n}".encode()).hexdigest()


def _header_less(body: bytes) -> dict:
    """The live hook contract: the adapter verified HMAC V2 upstream and the
    parsed body rides on the event; no headers reach the router."""
    return {"payload": json.loads(body)}


def _signed(body: bytes, *, secret: str = _SECRET) -> dict:
    """The router's own header path: it re-verifies the signature itself."""
    return {
        "payload": json.loads(body),
        "raw_body": body,
        "headers": {"X-Webhook-Signature": _sign(body, secret)},
    }


def test_a_rewake_passes_the_guard_twice_in_a_row_within_the_ttl(router):
    original, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX)
    assert router.on_pre_gateway_dispatch(**_header_less(original)) != _SKIP
    for n in (1, 2):
        body, mid = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(n))
        assert mid == "AAMk-original"
        assert router.on_pre_gateway_dispatch(**_header_less(body)) != _SKIP
        assert rewake_outcome.read(_rewake_id(n))["state"] == rewake_outcome.STATE_DISPATCHED


def test_a_rewake_keeps_the_original_graph_id_for_every_downstream_consumer():
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(1))
    payload = json.loads(body)
    assert payload["event_id"] == "AAMk-original"
    assert payload["inbound_message"]["message_id"] == "AAMk-original"
    assert payload[rewake_outcome.MARKER_KEY] == _rewake_id(1)
    # Without a marker the bytes are exactly the poller's (signature parity).
    plain, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX)
    assert rewake_outcome.MARKER_KEY not in json.loads(plain)


def test_a_plain_duplicate_is_still_refused(router):
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX)
    assert router.on_pre_gateway_dispatch(**_header_less(body)) != _SKIP
    assert router.on_pre_gateway_dispatch(**_header_less(body)) == _SKIP


def test_the_same_rewake_delivered_twice_is_refused_and_journaled(router):
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(1))
    assert router.on_pre_gateway_dispatch(**_header_less(body)) != _SKIP
    assert router.on_pre_gateway_dispatch(**_header_less(body)) == _SKIP
    outcome = rewake_outcome.read(_rewake_id(1))
    assert outcome["state"] == rewake_outcome.STATE_REFUSED
    assert "replayed event id" in outcome["reason"]


def test_a_forged_rewake_is_still_refused(router):
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(1))
    assert router.on_pre_gateway_dispatch(**_signed(body, secret="not-the-secret")) == _SKIP
    outcome = rewake_outcome.read(_rewake_id(1))
    assert outcome["state"] == rewake_outcome.STATE_REFUSED
    assert "signature" in outcome["reason"]
    # A marker added after signing breaks the signature too.
    plain, _ = msgraph_replay.build_envelope(_raw("AAMk-two"), mailbox=_MAILBOX)
    tampered = json.loads(plain)
    tampered[rewake_outcome.MARKER_KEY] = _rewake_id(2)
    kwargs = {
        "payload": tampered,
        "raw_body": json.dumps(tampered, separators=(",", ":")).encode(),
        "headers": {"X-Webhook-Signature": _sign(plain, _SECRET)},
    }
    assert router.on_pre_gateway_dispatch(**kwargs) == _SKIP


def test_a_correctly_signed_rewake_passes_the_header_path(router):
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(1))
    assert router.on_pre_gateway_dispatch(**_signed(body)) != _SKIP


def test_a_malformed_or_misplaced_marker_is_refused(router):
    plain, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX)
    bad = json.loads(plain)
    bad[rewake_outcome.MARKER_KEY] = "not-hex"
    assert router.on_pre_gateway_dispatch(payload=bad) == _SKIP
    other = {
        "source": "filevine",
        "event_type": "matter.created",
        "event_id": "evt-9",
        rewake_outcome.MARKER_KEY: _rewake_id(9),
    }
    assert router.on_pre_gateway_dispatch(payload=other) == _SKIP


def test_only_a_webhook_first_turn_claims_a_dispatched_rewake(router):
    body, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX, rewake_id=_rewake_id(1))
    router.on_pre_gateway_dispatch(**_header_less(body))
    router.on_pre_llm_call(session_id="cron-1", platform="cron", is_first_turn=True)
    router.on_pre_llm_call(session_id="later", platform="webhook", is_first_turn=False)
    assert rewake_outcome.read(_rewake_id(1))["state"] == rewake_outcome.STATE_DISPATCHED
    router.on_pre_llm_call(session_id="sess-1", platform="webhook", is_first_turn=True)
    outcome = rewake_outcome.read(_rewake_id(1))
    assert (outcome["state"], outcome["session_id"]) == (
        rewake_outcome.STATE_TURN_STARTED,
        "sess-1",
    )


class _Graph:
    def __init__(self, raw: dict) -> None:
        self.raw = raw

    def get_message(self, message_id: str) -> dict:
        return self.raw


class _ThroughTheRouter:
    """A forward double that hands the signed bytes to the real router hook,
    the way the adapter does after verifying them, then runs the turn's first
    LLM call when the router let it through and ``start_turn`` is set."""

    def __init__(self, router, *, start_turn: bool = True, status: int = 202) -> None:
        self.router = router
        self.start_turn = start_turn
        self.status = status
        self.results: list = []

    def __call__(self, *, body: bytes, signature: str, timestamp: str, request_id: str):
        result = self.router.on_pre_gateway_dispatch(**_header_less(body))
        self.results.append(result)
        if result != _SKIP and self.start_turn:
            self.router.on_pre_llm_call(session_id="sess-x", platform="webhook", is_first_turn=True)
        return self.status, json.dumps({"status": "accepted"}).encode()


def _run_rewake(tmp_path, fwd, **kw) -> int:
    return msgraph_replay.rewake(
        "AAMk-original",
        mailbox=_MAILBOX,
        signing_secret=_SECRET,
        client=_Graph(_raw()),
        forward_fn=fwd,
        state_dir=str(tmp_path / "msgraph"),
        sleep=lambda _s: None,
        **kw,
    )


def test_rewake_exits_zero_twice_after_the_original_intake(router, tmp_path):
    # The A&P sequence: the email arrived, then two re-wakes inside the hour.
    original, _ = msgraph_replay.build_envelope(_raw(), mailbox=_MAILBOX)
    assert router.on_pre_gateway_dispatch(**_header_less(original)) != _SKIP
    assert _run_rewake(tmp_path, _ThroughTheRouter(router)) == 0
    assert _run_rewake(tmp_path, _ThroughTheRouter(router)) == 0


def test_rewake_exits_nonzero_with_the_reason_when_the_router_drops_it(router, tmp_path, caplog):
    # The adapter answers 202, then the router refuses: here the forward hop's
    # signature does not verify under the router's secret.
    class _BadHop(_ThroughTheRouter):
        def __call__(self, *, body, signature, timestamp, request_id):
            result = self.router.on_pre_gateway_dispatch(**_signed(body, secret="rotated"))
            self.results.append(result)
            return 202, b'{"status":"accepted"}'

    fwd = _BadHop(router)
    caplog.set_level("INFO")
    assert _run_rewake(tmp_path, fwd) == 1
    assert fwd.results == [_SKIP]
    assert "NOT delivered" in caplog.text and "signature verification failed" in caplog.text


def test_rewake_exits_nonzero_when_dispatched_but_no_turn_starts(router, tmp_path, caplog):
    caplog.set_level("INFO")
    code = _run_rewake(tmp_path, _ThroughTheRouter(router, start_turn=False), wait_s=0.0)
    assert code == 1
    assert "no webhook turn started" in caplog.text


def test_rewake_exits_nonzero_when_the_router_records_nothing(tmp_path, caplog):
    def adapter_only(*, body, signature, timestamp, request_id):
        return 202, b'{"status":"accepted"}'

    caplog.set_level("INFO")
    assert _run_rewake(tmp_path, adapter_only, wait_s=0.0) == 1
    assert "recorded no outcome" in caplog.text
    assert not os.path.exists(os.environ["SMD_REWAKE_OUTCOME_DIR"]) or not any(
        name.endswith(".json") for name in os.listdir(os.environ["SMD_REWAKE_OUTCOME_DIR"])
    )
