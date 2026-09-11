"""Unit tests for the Smokeball egress webhook reconciler — the PURE plan logic.
No network, no client (the imperative reconcile() is exercised e2e on the Machine)."""

from __future__ import annotations

from smokeball_connector.webhook_reconcile import (
    desired_from_intent,
    plan_reconcile,
    unwrap_subscription_list,
)

SLUG = "pilot-smokeball"
URL = "https://hermes-pilot-smokeball.fly.dev/webhooks/smokeball"
NAME = f"op-managed:{SLUG}:PracticeManagement"


def _intent(event_types=("matter.updated",), key="sekret", url=URL):
    return {
        "vendor": "smokeball",
        "slug": SLUG,
        "connector_key": "PracticeManagement",
        "adapter": "smokeball",
        "webhook_url": url,
        "event_types": list(event_types),
        "key": key,
    }


def _desired():
    return desired_from_intent(_intent())


def _sub(sid, name=NAME, url=URL, events=("matter.updated",)):
    return {
        "id": sid,
        "name": name,
        "eventNotificationUrl": url,
        "eventTypes": list(events),
    }


# ---- desired_from_intent --------------------------------------------------
def test_desired_requires_events_and_key():
    assert desired_from_intent(_intent()) is not None
    assert desired_from_intent(_intent(event_types=())) is None  # no triggers
    assert desired_from_intent(_intent(key="")) is None  # no signing key
    d = _desired()
    assert d["name"] == NAME
    assert d["eventNotificationUrl"] == URL
    assert d["eventTypes"] == ["matter.updated"]
    assert d["key"] == "sekret"


def test_desired_event_types_are_sorted_and_deduped():
    d = desired_from_intent(_intent(event_types=("task.created", "matter.updated", "matter.updated")))
    assert d["eventTypes"] == ["matter.updated", "task.created"]


# ---- unwrap_subscription_list (circuit breaker) ---------------------------
def test_unwrap_handles_known_shapes_and_rejects_unknown():
    assert unwrap_subscription_list([{"id": "a"}]) == [{"id": "a"}]
    assert unwrap_subscription_list({"value": [{"id": "a"}]}) == [{"id": "a"}]
    assert unwrap_subscription_list({"results": [{"id": "b"}]}) == [{"id": "b"}]
    # Unrecognized shape → None → circuit breaker (caller skips).
    assert unwrap_subscription_list({"unexpected": 1}) is None
    assert unwrap_subscription_list("garbage") is None
    assert unwrap_subscription_list(None) is None


# ---- plan_reconcile -------------------------------------------------------
def test_empty_actual_creates_one():
    p = plan_reconcile(_desired(), [], SLUG)
    assert p.creates == [_desired()] and p.deletes == []


def test_matching_actual_is_noop():
    p = plan_reconcile(_desired(), [_sub("s1")], SLUG)
    assert p.creates == [] and p.deletes == [] and p.skipped is None


def test_event_type_drift_repairs():
    p = plan_reconcile(_desired(), [_sub("s1", events=("matter.updated", "task.created"))], SLUG)
    assert p.deletes == ["s1"] and p.creates == [_desired()]


def test_url_drift_repairs():
    p = plan_reconcile(_desired(), [_sub("s1", url="https://evil.example/webhooks/smokeball")], SLUG)
    assert p.deletes == ["s1"] and p.creates == [_desired()]


def test_foreign_subscription_is_never_touched():
    foreign = {
        "id": "human-1",
        "name": "my hand-made sub",
        "eventNotificationUrl": URL,
        "eventTypes": ["matter.updated"],
    }
    p = plan_reconcile(_desired(), [foreign], SLUG)
    # No ours-sub → we create our own; the human's sub is NOT in deletes.
    assert "human-1" not in p.deletes
    assert p.creates == [_desired()]


def test_duplicates_are_deduped_to_one():
    p = plan_reconcile(_desired(), [_sub("s1"), _sub("s2"), _sub("s3")], SLUG)
    # One canonical kept (matches → no recreate), the other two deleted.
    assert sorted(p.deletes) == ["s2", "s3"] and p.creates == []


def test_desired_none_skips_never_deletes():
    # No verifiable desired (e.g. triggers removed) must NOT delete our live sub.
    p = plan_reconcile(None, [_sub("s1")], SLUG)
    assert p.skipped == "no_verifiable_desired" and p.deletes == [] and p.creates == []


def test_unreadable_actual_skips():
    p = plan_reconcile(_desired(), None, SLUG)
    assert p.skipped == "unreadable_subscription_list" and p.deletes == [] and p.creates == []


def test_force_recreate_repairs_even_when_matching():
    # Key rotation: url/eventTypes match but the orchestrator flagged a new key.
    p = plan_reconcile(_desired(), [_sub("s1")], SLUG, force_recreate=True)
    assert p.deletes == ["s1"] and p.creates == [_desired()]


def test_apply_then_replan_is_empty_idempotency():
    desired = _desired()
    actual: list = []
    # Round 1: create.
    p1 = plan_reconcile(desired, actual, SLUG)
    assert p1.creates and not p1.deletes
    # Apply: the created sub now exists with a vendor-assigned id.
    applied = [{"id": "new-1", **p1.creates[0]}]
    # Round 2: identical inputs → no-op (the idempotency guarantee).
    p2 = plan_reconcile(desired, applied, SLUG)
    assert p2.creates == [] and p2.deletes == [] and p2.skipped is None
