#!/usr/bin/env python3
"""Egress webhook-subscription reconciler — ORCHESTRATOR (vendor-agnostic).

Reads the declared intent from customer.yaml and ensures each push connector's
vendor-side subscriptions match it. Runs as boot/connect infrastructure in the
hermes venv (has PyYAML); does the vendor-specific work by dispatching to that
connector's own venv (`/opt/connectors/<vendor>/.venv`, has httpx + the client).

Triggers (see plan):
  --trigger connect : the OAuth callback just wrote a fresh token → always reconcile.
  --trigger boot    : reconcile ONLY if the declared intent changed since the last
                      success (a local hash on the volume) — else a pure local
                      no-op with zero vendor calls.

NON-FATAL by contract: every handled condition exits 0 and records a terminal
status; the caller (bootstrap.sh / the callback) never crashes on a reconcile
failure. The intent hash + key fingerprint are stored LOCAL-only (never sent to
the vendor); the fingerprint lets us detect a re-vaulted signing key (which GET
can't reveal) and force a delete+recreate so the new key takes effect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

import yaml

_STATE_DIR = "/opt/data/.smokeball-mcp"
_HASH_FILE = os.path.join(_STATE_DIR, "webhook-reconcile.json")  # {connector_key: {hash, key_fp}}
_STATUS_FILE = os.path.join(_STATE_DIR, "webhook-reconcile.status")  # one terminal line
_SUBPROCESS_TIMEOUT_S = 20


def log(msg: str) -> None:
    print(f"[webhook-reconcile] {msg}", file=sys.stderr, flush=True)


def _env_secret_name(adapter: str) -> str:
    return f"WEBHOOK_SECRET_{adapter.upper().replace('-', '_')}"


def build_intents(customer: dict) -> list[dict]:
    """One intent per enabled `mcp:<vendor>` connector that declares a webhook_url.
    event_types = union of webhook_triggers[].event_type where source == adapter
    (asserting one connector per adapter — two would collide on the same events)."""
    triggers = customer.get("webhook_triggers") or []
    by_adapter: dict[str, list[str]] = {}
    for t in triggers:
        if not (isinstance(t, dict) and t.get("source") and t.get("event_type")):
            continue
        # SYNTHETIC triggers never reach the vendor. `vendor_emitted: false`
        # marks an event the gate routes but the vendor does not emit (a signed
        # rehearsal injection, or an internal domain signal). Absent → True,
        # because a real event type belongs in the subscription and the safe
        # default must not silently drop one.
        #
        # This is not defensive tidiness. The vendor validates eventTypes as a
        # SET: one unrecognized member fails the whole POST /webhooks. On
        # 2026-08-28 (#2622) pilot-smokeball gained a synthetic
        # `responses.served` trigger on the smokeball adapter; the union put it
        # beside `matter.updated`, Smokeball answered HTTP 400 "Invalid
        # EventTypes", and because the changed intent hash also set
        # force_recreate the reconciler DELETED the working subscription before
        # the failing create. The seat then had no webhook feed at all until
        # 2026-09-02 — the flagship matter-memo-on-update skill simply never
        # woke. Blast radius is every real event type sharing the adapter.
        if t.get("vendor_emitted") is False:
            continue
        by_adapter.setdefault(str(t["source"]), []).append(str(t["event_type"]))

    intents: list[dict] = []
    seen_adapters: set[str] = set()
    slug = str(customer.get("customer_id") or "")
    for cname, conn in (customer.get("connectors") or {}).items():
        if not isinstance(conn, dict) or not conn.get("enabled"):
            continue
        backend = str(conn.get("backend") or "")
        webhook_url = conn.get("webhook_url")
        if not backend.startswith("mcp:") or not webhook_url:
            continue
        vendor = backend.split(":", 1)[1]
        adapter = str(conn.get("adapter") or vendor)
        if adapter in seen_adapters:
            log(f"WARN two connectors share adapter {adapter!r}; skipping extra {cname!r} (events would collide)")
            continue
        seen_adapters.add(adapter)
        intents.append(
            {
                "vendor": vendor,
                "connector_key": str(cname),
                "adapter": adapter,
                "slug": slug,
                "webhook_url": str(webhook_url),
                "event_types": sorted(set(by_adapter.get(adapter, []))),
                "key": os.environ.get(_env_secret_name(adapter), ""),
            }
        )
    return intents


def _canonical_hash(intent: dict) -> str:
    """Local-only hash of the declared intent INCLUDING the key (so a re-vaulted
    key changes it). Never leaves the box."""
    payload = json.dumps(
        {
            "vendor": intent["vendor"],
            "connector_key": intent["connector_key"],
            "webhook_url": intent["webhook_url"],
            "event_types": intent["event_types"],
            "key": intent["key"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _key_fp(intent: dict) -> str:
    return hashlib.sha256((intent.get("key") or "").encode()).hexdigest()[:12]


def _load_state() -> dict:
    try:
        with open(_HASH_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _HASH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, _HASH_FILE)
    except OSError as exc:  # non-fatal: we just lose the hash-gate next boot
        log(f"WARN could not persist reconcile state: {exc}")


def _write_status(line: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        log(f"WARN could not write status marker: {exc}")


def _dispatch(intent: dict) -> dict:
    """Run the vendor adapter in its own venv: intent JSON on stdin, manifest JSON
    on stdout. Missing connector venv / module → skip; timeout → skip."""
    vendor = intent["vendor"]
    py = f"/opt/connectors/{vendor}/.venv/bin/python"
    if not os.path.exists(py):
        log(f"{vendor}: no author-built connector venv ({py}) — skipping (e.g. a vendor MCP)")
        return {"vendor": vendor, "status": "skipped:no_connector_venv"}
    try:
        proc = subprocess.run(
            [py, "-m", f"{vendor}_connector.webhook_reconcile"],
            input=json.dumps(intent),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log(f"{vendor}: reconcile subprocess timed out — skipping (retry next trigger)")
        return {"vendor": vendor, "status": "skipped:timeout"}
    if proc.stderr:
        for ln in proc.stderr.splitlines():
            log(ln)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        log(f"{vendor}: reconcile produced no manifest (rc={proc.returncode}) — skipping")
        return {"vendor": vendor, "status": "skipped:no_manifest"}


def reconcile_all(customer_yaml: str, trigger: str) -> int:
    try:
        with open(customer_yaml, encoding="utf-8") as f:
            customer = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        log(f"could not read {customer_yaml}: {exc} — nothing to do")
        _write_status("error:bad_customer_yaml")
        return 0

    intents = build_intents(customer)
    if not intents:
        _write_status("ok:no_push_connectors")
        return 0

    state = _load_state()
    outcomes: list[str] = []
    for intent in intents:
        ckey = intent["connector_key"]
        cur_hash = _canonical_hash(intent)
        prev = state.get(ckey) or {}
        if trigger == "boot" and prev.get("hash") == cur_hash:
            log(f"{intent['vendor']}/{ckey}: intent unchanged — steady (no vendor calls)")
            outcomes.append(f"{intent['vendor']}=steady")
            continue
        # Key rotation: same connector, the signing key fingerprint changed → the
        # subscription's key is stale; force a delete+recreate (GET can't reveal it).
        intent["force_recreate"] = bool(prev.get("key_fp") and prev["key_fp"] != _key_fp(intent))
        manifest = _dispatch(intent)
        status = str(manifest.get("status", "skipped:unknown"))
        outcomes.append(f"{intent['vendor']}={status}")
        if status in ("applied", "steady"):
            state[ckey] = {"hash": cur_hash, "key_fp": _key_fp(intent)}
            _save_state(state)

    _write_status(("ok" if all("error" not in o for o in outcomes) else "partial") + ": " + " ".join(outcomes))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Egress webhook-subscription reconciler")
    ap.add_argument("customer_yaml")
    ap.add_argument("--trigger", choices=("boot", "connect"), default="boot")
    args = ap.parse_args()
    try:
        return reconcile_all(args.customer_yaml, args.trigger)
    except Exception as exc:  # noqa: BLE001 — never crash the boot/callback path
        log(f"unexpected error (non-fatal): {exc}")
        _write_status(f"error:unexpected:{type(exc).__name__}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
