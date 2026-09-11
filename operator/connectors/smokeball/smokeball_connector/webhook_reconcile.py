"""Smokeball egress webhook-subscription reconciler (vendor adapter).

Makes Smokeball PUSH events: ensures the firm's Smokeball tenant has exactly the
webhook subscriptions `customer.yaml` declares (the egress mirror of the ingress
route `translate.py` materializes). Runs as boot/connect INFRASTRUCTURE in the
connector's own venv (it has httpx + the client) — NOT through the agent/MCP
governance layer — so it uses the raw `SmokeballClient`, needs no MCP tool and no
overlay action-class.

Invoked by `operator/bin/webhook_reconcile.py` (the orchestrator) via subprocess:
the normalized `intent` arrives as JSON on stdin, a result `manifest` is written
as JSON on stdout; human-readable progress goes to stderr (→ the boot log).

Safety invariants (see plan + /critique):
- Ownership is the `op-managed:<slug>:` NAME PREFIX only — a subscription a human
  created (even one pointed at our gate URL) is never deleted.
- NEVER delete-to-zero: an empty desired (no triggers / no key / unreadable) is a
  SKIP, never a teardown. Explicit teardown is the decommission path (Phase 2).
- Circuit breaker: an unreadable `GET /webhooks` shape → skip (no create, no
  delete) — never act on a state we cannot confidently read, and never loop-create
  duplicates on a persistent misparse.
- Key rotation: the signing key is write-only (GET never returns it), so the
  orchestrator detects a re-vaulted key locally and sets `force_recreate`; we then
  delete+recreate so the new key actually takes effect.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import Any

from .client import build_client_from_env


@dataclasses.dataclass(frozen=True)
class Plan:
    """A pure reconcile decision. ``skipped`` (when set) means make NO vendor
    writes — it is never an error, just 'do nothing this run'."""

    creates: list[dict[str, Any]]
    deletes: list[str]  # subscription ids
    skipped: str | None = None


def desired_from_intent(intent: dict[str, Any]) -> dict[str, Any] | None:
    """The single subscription this connector should have, or None if there is
    nothing verifiable to register (no event types, or no signing key — a keyless
    subscription's deliveries would fail the gate's signature check, so it is
    worse than none). None → the caller SKIPS (never deletes)."""
    event_types = sorted({str(e) for e in (intent.get("event_types") or []) if e})
    key = intent.get("key")
    if not event_types or not key:
        return None
    return {
        "name": f"op-managed:{intent['slug']}:{intent['connector_key']}",
        "eventNotificationUrl": intent["webhook_url"],
        "eventTypes": event_types,
        "key": key,
    }


def unwrap_subscription_list(raw: Any) -> list[dict[str, Any]] | None:
    """Tolerant unwrap of `GET /webhooks` (the list shape is ASSUMED until
    confirmed at connect; mirror the connector diagnostic's value/results/items
    unwrap). Returns the list, or None if the shape is unrecognized → circuit
    breaker (skip; never delete or create on an unreadable state)."""
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, dict)]
    if isinstance(raw, dict):
        for k in ("value", "results", "items", "subscriptions", "data"):
            v = raw.get(k)
            if isinstance(v, list):
                return [s for s in v if isinstance(s, dict)]
    return None


def _matches(sub: dict[str, Any], desired: dict[str, Any]) -> bool:
    """A sub already equals desired in the only two dimensions GET returns (url +
    event set). The key is write-only and cannot be compared here — key rotation
    is handled by ``force_recreate`` from the orchestrator."""
    return (
        str(sub.get("eventNotificationUrl", "")) == desired["eventNotificationUrl"]
        and sorted(str(e) for e in (sub.get("eventTypes") or [])) == desired["eventTypes"]
    )


def plan_reconcile(
    desired: dict[str, Any] | None,
    actual_subs: list[dict[str, Any]] | None,
    slug: str,
    *,
    force_recreate: bool = False,
) -> Plan:
    """PURE reconcile decision. No I/O. The unit-test target.

    ``actual_subs is None`` → unreadable list → skip (circuit breaker).
    ``desired is None``      → nothing verifiable to register → skip (never delete).
    Otherwise: keep/repair one op-managed subscription, dedupe extras, create if
    absent. Only op-managed subs (our name prefix) are ever eligible for deletion.
    """
    if actual_subs is None:
        return Plan([], [], skipped="unreadable_subscription_list")
    if desired is None:
        return Plan([], [], skipped="no_verifiable_desired")

    prefix = f"op-managed:{slug}:"
    ours = [s for s in actual_subs if str(s.get("name", "")).startswith(prefix)]

    if not ours:
        return Plan([desired], [])

    # Pick the canonical sub to keep: prefer one already matching desired.
    canonical = next((s for s in ours if _matches(s, desired)), ours[0])
    extras = [s for s in ours if s is not canonical]
    deletes = [str(s["id"]) for s in extras if s.get("id")]  # dedupe duplicates

    if _matches(canonical, desired) and not force_recreate:
        return Plan([], deletes)  # steady (plus any dedupe)
    # Repair (drift in url/eventTypes, or a key rotation the orchestrator flagged):
    # delete the canonical and recreate to the desired shape with the current key.
    if canonical.get("id"):
        deletes.append(str(canonical["id"]))
    return Plan([desired], deletes)


def _err(msg: str) -> None:
    print(f"[webhook-reconcile:smokeball] {msg}", file=sys.stderr, flush=True)


def reconcile(intent: dict[str, Any]) -> dict[str, Any]:
    """Imperative: build the client, read live state, plan, apply. Fail-soft —
    every handled condition returns a manifest with a status; the orchestrator
    swallows non-success. Returns ``{vendor, status, created[], deleted[], detail}``."""
    slug = intent.get("slug", "?")

    def manifest(
        status: str,
        *,
        created: list | None = None,
        deleted: list | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        return {
            "vendor": "smokeball",
            "slug": slug,
            "status": status,
            "created": created or [],
            "deleted": deleted or [],
            "detail": detail,
        }

    desired = desired_from_intent(intent)
    if desired is None:
        _err(f"{slug}: no verifiable subscription to register (no event types or no key) — skipping")
        return manifest("skipped:no_verifiable_desired")

    try:
        client = build_client_from_env()
    except Exception as exc:  # noqa: BLE001 - ValueError (auth_code without a token) or a missing secret: not connected yet is a skip, not a crash
        _err(f"{slug}: client not constructable (likely not connected yet): {exc} — skip, retry next trigger")
        return manifest("skipped:not_connected", detail=str(exc))

    try:
        raw = client.get("/webhooks")
    except Exception as exc:  # SmokeballApiError / httpx / auth  # noqa: BLE001 - SmokeballApiError, httpx or auth: any failure listing subscriptions is a skip the next trigger retries
        _err(f"{slug}: GET /webhooks failed: {exc} — skip, retry next trigger")
        return manifest("skipped:list_failed", detail=str(exc))

    actual = unwrap_subscription_list(raw)
    if actual is None:
        _err(f"{slug}: GET /webhooks returned an unrecognized shape — skip (no writes)")
        return manifest("skipped:unreadable_subscription_list")

    # Observability: a human-owned subscription pointed at our gate URL (never touched).
    our_origin = _origin(desired["eventNotificationUrl"])
    for s in actual:
        if (
            not str(s.get("name", "")).startswith(f"op-managed:{slug}:")
            and _origin(str(s.get("eventNotificationUrl", ""))) == our_origin
        ):
            _err(f"{slug}: NOTE unmanaged subscription {s.get('id')!r} at our gate URL — leaving it untouched")

    plan = plan_reconcile(desired, actual, slug, force_recreate=bool(intent.get("force_recreate")))
    if plan.skipped:
        _err(f"{slug}: {plan.skipped} — skipping")
        return manifest(f"skipped:{plan.skipped}")

    deleted: list[str] = []
    for sid in plan.deletes:
        try:
            client.delete_webhook_subscription(sid)
            deleted.append(sid)
        except Exception as exc:  # noqa: BLE001 - a failed delete leaves a partial state the next trigger heals; the manifest says so
            _err(f"{slug}: DELETE /webhooks/{sid} failed: {exc} — partial; next trigger heals")
            return manifest("error:delete_failed", deleted=deleted, detail=str(exc))

    created: list[str] = []
    for payload in plan.creates:
        try:
            resp = client.request("POST", "/webhooks", json=payload)
            sid = resp.get("id") if isinstance(resp, dict) else None
            created.append(str(sid) if sid else payload["name"])
        except Exception as exc:  # noqa: BLE001 - a failed create is reported in the manifest as error:create_failed; the next trigger heals
            _err(f"{slug}: POST /webhooks failed: {exc} — partial; next trigger heals")
            return manifest("error:create_failed", created=created, deleted=deleted, detail=str(exc))

    status = "steady" if not created and not deleted else "applied"
    _err(
        f"{slug}: {status} — created={created} deleted={deleted} "
        f"name={desired['name']} eventTypes={desired['eventTypes']}"
    )
    return manifest(status, created=created, deleted=deleted)


def _origin(url: str) -> str:
    """scheme://host[:port] of a URL, for the unmanaged-at-our-url NOTE only."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def main() -> int:
    """stdin: intent JSON. stdout: manifest JSON. Always exit 0 on a handled
    condition (the orchestrator reads the manifest status); reserve non-zero for
    an unexpected crash so it surfaces in the boot log."""
    try:
        intent = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 - malformed stdin intent must still yield a manifest the orchestrator can read, never a trace
        print(
            json.dumps(
                {
                    "vendor": "smokeball",
                    "status": "error:bad_intent",
                    "detail": str(exc),
                }
            )
        )
        return 0
    print(json.dumps(reconcile(intent)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
