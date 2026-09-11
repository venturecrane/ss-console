"""Tests for the egress webhook reconciler ORCHESTRATOR (bin/webhook_reconcile.py)
— the pure intent-building + change-hash logic (the subprocess dispatch is
exercised e2e on the Machine)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/

from bin.webhook_reconcile import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    _canonical_hash,
    _env_secret_name,
    _key_fp,
    build_intents,
)


def _customer():
    return {
        "customer_id": "pilot-smokeball",
        "connectors": {
            "PracticeManagement": {
                "adapter": "smokeball",
                "backend": "mcp:smokeball",
                "enabled": True,
                "webhook_url": "https://hermes-pilot-smokeball.fly.dev/webhooks/smokeball",
            },
            "Email": {
                "adapter": "agentmail",
                "backend": "mcp:agentmail",
                "enabled": True,
                "webhook_url": "https://hermes-pilot-smokeball.fly.dev/webhooks/agentmail",
            },
        },
        "webhook_triggers": [
            {
                "source": "smokeball",
                "event_type": "matter.updated",
                "skill": "x",
                "persona": "operator",
            },
            {
                "source": "agentmail",
                "event_type": "message.received",
                "skill": "y",
                "persona": "operator",
            },
        ],
    }


def test_env_secret_name():
    assert _env_secret_name("smokeball") == "WEBHOOK_SECRET_SMOKEBALL"
    assert _env_secret_name("ms-graph") == "WEBHOOK_SECRET_MS_GRAPH"


def test_build_intents_one_per_push_connector_with_per_adapter_events(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET_SMOKEBALL", "sb-secret")
    monkeypatch.delenv("WEBHOOK_SECRET_AGENTMAIL", raising=False)
    intents = {i["vendor"]: i for i in build_intents(_customer())}
    assert set(intents) == {"smokeball", "agentmail"}
    sb = intents["smokeball"]
    assert sb["connector_key"] == "PracticeManagement"
    assert sb["slug"] == "pilot-smokeball"
    assert sb["event_types"] == ["matter.updated"]  # only smokeball-source triggers
    assert sb["key"] == "sb-secret"
    # agentmail intent is built but keyless here; it is correctly SKIPPED later at
    # dispatch (no /opt/connectors/agentmail venv) — its subscription is Svix-side.
    assert intents["agentmail"]["event_types"] == ["message.received"]


def test_synthetic_trigger_is_excluded_from_the_vendor_subscription(monkeypatch):
    """A `vendor_emitted: false` trigger routes at the gate but never reaches the
    vendor's eventTypes.

    Regression for the 2026-08-28 -> 2026-09-02 pilot-smokeball outage. A
    synthetic `responses.served` trigger was authored on the smokeball adapter
    for rehearsal; the union put it beside matter.updated, Smokeball validated
    eventTypes as a SET and rejected the entire POST /webhooks with HTTP 400
    "Invalid EventTypes", and the changed intent hash meant the reconciler had
    already deleted the working subscription. The seat carried no webhook feed
    at all for five days. The real event type must survive its synthetic sibling.
    """
    monkeypatch.setenv("WEBHOOK_SECRET_SMOKEBALL", "sb-secret")
    customer = _customer()
    customer["webhook_triggers"].append(
        {
            "source": "smokeball",
            "event_type": "responses.served",
            "skill": "z",
            "persona": "operator",
            "vendor_emitted": False,
        }
    )
    sb = {i["vendor"]: i for i in build_intents(customer)}["smokeball"]
    assert sb["event_types"] == ["matter.updated"]
    assert "responses.served" not in sb["event_types"]


def test_vendor_emitted_true_or_absent_still_reaches_the_vendor(monkeypatch):
    """Only an explicit `false` suppresses. Absent (the overwhelmingly common
    case) and explicit `true` both belong in the subscription — the safe default
    must never silently drop a real event type."""
    monkeypatch.setenv("WEBHOOK_SECRET_SMOKEBALL", "sb-secret")
    customer = _customer()
    customer["webhook_triggers"].append(
        {
            "source": "smokeball",
            "event_type": "task.created",
            "skill": "z",
            "persona": "operator",
            "vendor_emitted": True,
        }
    )
    sb = {i["vendor"]: i for i in build_intents(customer)}["smokeball"]
    assert sb["event_types"] == ["matter.updated", "task.created"]


def test_disabled_or_no_webhook_connectors_excluded(monkeypatch):
    c = _customer()
    c["connectors"]["PracticeManagement"]["enabled"] = False
    c["connectors"]["Email"].pop("webhook_url")
    assert build_intents(c) == []


def test_two_connectors_same_adapter_skips_the_extra(monkeypatch):
    c = _customer()
    c["connectors"]["PM2"] = dict(
        c["connectors"]["PracticeManagement"]
    )  # second smokeball
    intents = [i for i in build_intents(c) if i["vendor"] == "smokeball"]
    assert len(intents) == 1  # collision avoided — only the first smokeball connector


def test_canonical_hash_changes_on_intent_and_key_change():
    base = {
        "vendor": "smokeball",
        "connector_key": "PracticeManagement",
        "webhook_url": "https://h/webhooks/smokeball",
        "event_types": ["matter.updated"],
        "key": "k1",
    }
    h0 = _canonical_hash(base)
    assert _canonical_hash(base) == h0  # stable
    assert (
        _canonical_hash({**base, "event_types": ["matter.updated", "task.created"]})
        != h0
    )
    assert (
        _canonical_hash({**base, "webhook_url": "https://other/webhooks/smokeball"})
        != h0
    )
    rotated = {**base, "key": "k2"}
    assert (
        _canonical_hash(rotated) != h0
    )  # key rotation flips the hash → reconcile runs
    assert _key_fp(rotated) != _key_fp(base)  # and the fp flips → force_recreate
