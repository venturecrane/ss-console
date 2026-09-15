---
title: Inbound Webhook Architecture — Front-Door Gate, Native-Adapter Routing, and Deferred Hardening
date: 2026-06-01
status: accepted
captain: Scott Durgan
related-adr: 0005-external-send-identity.md, 0021-leverage-hermes-native-primitives.md, 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md, 0027-inbound-convergence.md, 0031-content-sensitivity-send-floor.md
related-interview: operator/customers/smd/onboarding-interview-2026-05-31.md
related-issue: '#1103, #1165, #1166'
related-pr: 'hermes-smd-overlay#27, hermes-smd-overlay#29 (v0.4.3); ss-console#1178'
---

# ADR 0032 — Inbound Webhook Architecture

**Status:** Accepted (Captain decision, 2026-06-01). Proven live on customer-zero: Scott emailed `smdcrane@agentmail.to`, AgentMail webhooked it, and Crane read + replied autonomously (~33s, no operator), witnessed.

## Context

The Operator must **receive** events and act on its own — inbound email today (Crane's AgentMail inbox), inbound vendor webhooks tomorrow (Filevine `matter.created`, Clio activity, e-sign, CRMs). This is a recurring, core product primitive; the `customer.yaml` schema already reserves `connectors[].webhook_url` + `webhook_triggers[]` for it (ADR 0021 Stream E). We chose the public-webhook transport over AgentMail's WebSocket option deliberately: most vendors push webhooks, so solving the secure public-inbound pattern now is reusable; AgentMail is just the first instance.

Constraints discovered while building (verified live, not assumed):

- **Hermes' native webhook adapter** (`gateway/platforms/webhook.py`, port `8644`, `POST /webhooks/{route}`) validates **only** GitHub / GitLab / Generic (`X-Webhook-Signature` = hex HMAC-SHA256 of body). A configured secret + an unrecognized signature header → `401`.
- **AgentMail delivers via Svix** (`svix-id` / `svix-timestamp` / `svix-signature` headers, `whsec_` secret, base64 `v1` scheme; the webhook id prefix `ep_` is the tell) — a different verification entirely. The Context7 quick-start example showing a simple `X-AgentMail-Signature` hex-HMAC is misleading; the live signature is Svix. (This cost a `401` on the first real email and is why we test live.)
- The overlay `hermes-smd-webhook-router` plugin (`pre_gateway_dispatch`) is **inert for HTTP webhooks** on the pinned Hermes ref — its dispatch directive isn't honored. Routing must go through the native adapter's `routes` config.
- The per-customer Fly Machine was deliberately **worker-only (no public ports)**; inbound requires exposing a surface.
- A 3-critic review established two non-negotiables: **injection defenses must be deterministic code, not enforced inside the injectable LLM**; and the autonomous-send path must not face the public internet without those gates being real.

## Decision

A reusable inbound path with a deterministic security boundary at the edge:

```
public POST (vendor) → Fly [http_service] → front-door GATE → Hermes adapter :8644 → skill → autonomous action
```

1. **Front-door gate** (`hermes-smd-overlay/webhook_gate.py`, stdlib): the single public listener. Per-vendor signature verification (Svix for AgentMail) using the per-vendor secret `WEBHOOK_SECRET_<SOURCE>`. Verified → forward verbatim to the adapter on machine-local `:8644` signed with the adapter's generic HMAC V2 (`X-Webhook-Signature-V2` = hex HMAC over `"<timestamp>.<exact bytes>"` plus `X-Webhook-Timestamp`, same secret string, verified inside the adapter's 300 s window; the body-only V1 header was retired 2026-09-15 when v0.21 began warning it replays) and `X-Request-ID` = the delivery id (idempotency). Forged/missing signature → `401` **before any agent work**. Route names are charset-validated; the forward target is a fixed loopback host:port. Only the gate is exposed publicly (Fly `[http_service]` → `8643`); the adapter's `8644` stays private.
2. **Routing** is materialized by the overlay `translate.py` (`_materialize_webhook_platform`) from `customer.yaml.connectors[].webhook_url` + `webhook_triggers[]` into a native `platforms.webhook` route block (secret from the Fly secret, **fail-closed** without it; prompt delivers the inbound body as delimited UNTRUSTED data; `skills` = the mapped handler).
3. **Handler**: the routed skill (customer-zero: `inbox-triage` Mode B). Trusted-sender gate (the `from` domain must be in `scope.trusted_sender_domains`), reply via the vendor's in-thread reply tool (**structural recipient-lock** — reply target is the original sender, never a body-derived address), in the persona's own voice, under the existing trust-gate + content floor (ADR 0025/0031).
4. **Cost/availability**: `min_machines_running = 1` (always-on) for customer-zero so inbound answers promptly.

**Evolution seam (future vendors = config, not rearchitecture):** one front-door per-vendor verify rule + one `connectors[].webhook_url` + one `webhook_triggers[]` row + one handler skill. Reusable: the gate, the native adapter, the trust/floor machinery, the materializer.

## Customer-zero security posture vs. deferred hardening

Customer-zero (`smd`) is an internal, unadvertised address; residual public-attack risk is near-nil, so the Captain authorized going straight to autonomous inbound with a **deliberately scoped** floor:

**Kept now (cheap, real):** deterministic HMAC/Svix verification at the gate; structural recipient-lock; the existing `enforce.py` trust-gate + ADR-0031 content floor; trusted-sender allowlist (in the skill); native-adapter rate-limit/idempotency.

**DEFERRED — required before a public, external customer's address is exposed (build then, not speculatively now):**

- **Deterministic DMARC/DKIM gate in code.** AgentMail surfaces the receiving MTA's verdicts in `message.headers['Authentication-Results']`; parse it and gate trust on `dmarc=pass` aligned to the `from` domain — not a domain-string match that a spoofer defeats. (The allowlist alone trusts a spoofable `From`.)
- **Durable idempotency** on the Fly volume (delivery-id keyed, committed before send) so a cold-start retry across a Machine restart cannot double-send.
- **Send-caps** (per-message and per-day) enforced in code.
- **Non-LLM content pre-filter** as belt-and-suspenders to the content floor.
- **Scale-to-zero** (`min_machines_running = 0` + auto-start + the gate's `503`-invites-retry) to remove always-on cost once cold-start latency is acceptable.

These are recorded here so the hardening path is a known checklist, not a rediscovery, when the first external customer lands.

## Consequences

- Crane is a reachable, self-acting employee (proven). The pattern generalizes to webhook-push connectors.
- The Machine carries a continuous (small) Fly cost while `min_machines_running = 1`.
- A new public surface exists (the gate). It is authenticated (HMAC/Svix), charset-validated, body-capped, and the only exposed port; the agent runtime stays private.
- The deferred items above are the gate to safely pointing this at the public internet for an external tenant.
