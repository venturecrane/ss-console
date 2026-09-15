---
title: The Configuration Surface Is a Security Boundary
date: 2026-05-29
status: accepted
captain: Scott Durgan
related-adr: 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md
related-note: note_01KSS3TCTKWYVF6EZ04482X389, note_01KSTYSNC9CYPKYFJZ3TJ7F6RM
---

# ADR 0026 — The Configuration Surface Is a Security Boundary

**Status:** Accepted (Captain decision, 2026-05-29). Companion to ADR 0025; required by it.

> **Successor-endpoint note (2026-07-13).** The `trust-ceiling.ts` endpoint this ADR centers on was **retired** under the [ADR 0056](./0056-persona-exposure-skill-initiation-entitlements.md) flag-day model — it now returns **410 Gone** (the scalar trust ceiling is gone). The doctrine below (principal-authentication, immutable audit on success and on rejected attempts, floor/locked-field checks) is unchanged and now lives in its successor, `src/pages/api/portal/operator/settings/customer-yaml-update.ts`. Read that handler for the live implementation; read the endpoint references below as historical.

**Source:** Direct consequence of ADR 0025. Once autonomy ceilings are configurable, the act of _changing a ceiling_ is the act of changing what the agent may do without a human — which is a privileged operation. The 2026-05-29 build audit (`note_01KSTYSNC9CYPKYFJZ3TJ7F6RM`) found the one config-change surface that exists today, `src/pages/api/portal/operator/settings/trust-ceiling.ts`, logs the intent and returns (`:68`) — no persistence, and no write to the immutable audit log. That is the gap this ADR closes as doctrine.

---

## Context

ADR 0025 makes the trust ceiling a configured value rather than a hardcoded one. The safety of the entire harness then rests not only on enforcement (the agent obeys the ceiling) but on **governance of the ceiling itself** (who may set it, how the change is recorded, and the guarantee that the agent can never set its own).

The thesis note states this precisely: _"once exposure is configurable, the config surface becomes a security boundary — raising a ceiling is a privileged, audited act; the agent can never raise its own."_ A configuration system that lets the thing-being-governed edit its own governance is not a security boundary at all.

Today's reality, verified in code:

- The portal endpoint authenticates a **principal** and forbids operators/compliance (`trust-ceiling.ts:48-54`) — the access gate is correct.
- But the handler **does not persist** the change and **does not audit** it: it emits `console.info('settings.trust_ceiling.intent', …)` (`:68`) and returns an `ack` banner. A `console.info` line in Worker logs is not the append-only customer audit ledger (`operator/safety_substrate/` audit log, the one that is legal-hold-grade per the audit).

So the surface that will, under ADR 0025, raise an agent's exposure ceiling currently has no durable record and no audit entry. A ceiling change is the highest-privilege configuration act in the product, and it is the least recorded.

## Decision

**Every change to a trust ceiling, exposure setting, sender-identity posture, initiation grant, or any other autonomy-affecting configuration is a privileged, authenticated, durably-persisted, and immutably-audited act. The agent has no code path to perform it.**

Specifically:

### 1. Principal-authenticated, never agent-initiated

A ceiling-affecting change is authorized only by a human principal on the subscription (the existing `allowedRoles: ['principal']` gate generalizes to all autonomy config). No tool, skill, prompt, or agent-authored config write may raise a ceiling. The agent may _propose_ a change into the control plane for the principal to act on (e.g. "I keep drafting these; want me to send them autonomously?"), but the proposal is data for a human, not a self-executing write. This is the config-side mirror of ADR 0025's "the agent cannot raise its own ceiling."

### 2. Durably persisted to the owned artifact

The change is written to `customer.yaml` (git source of truth per ADR 0012) and projected to the materialized replicas (`customer_configs` / per-customer R2). The portal's intent-log-only behavior is replaced by a real write path. A change the principal cannot see reflected in their owned config did not happen.

### 3. Immutably audited as a config-change event

Every autonomy-config change emits an entry to the same append-only audit log that records tool calls, LLM calls, and sends — capturing _who_ changed _what_, from which value to which value, _when_, and the authenticated principal. This is distinct from a debug log line; it is part of the legal-hold record. A regulator or the customer's compliance counsel can reconstruct "on what date did this employee become allowed to send autonomously, and who authorized it."

### 4. Floors are not raisable through this surface

Per ADR 0025, a vertical-pack floor (e.g. law pins `EXTERNAL_SEND = draft_for_review`) cannot be raised by principal configuration. The config-change handler validates the requested value against the effective floor and rejects a raise above it, auditing the rejected attempt.

### 5. Raise vs. lower asymmetry

Lowering a ceiling (toward more restrictive) takes effect immediately and is audited. Raising a ceiling (toward more autonomy) is the privileged direction and is what the persistence + audit + floor-check guard most strictly. The system is biased so that the safe direction is frictionless and the risky direction is recorded.

## Alternatives Considered

### A. Treat config like ordinary app settings (status quo)

**Rejected.** Ordinary-settings framing is what produced the intent-log-only handler. Autonomy config is not a theme preference; it changes the blast radius of an autonomous agent. It earns the audit ledger.

### B. Audit only the runtime enforcement, not the config change

**Rejected.** The enforcement audit tells you what the agent _did_; the config audit tells you what it was _permitted_ to do and who permitted it. After an incident, "who raised the ceiling, and when" is the first question. Without the config-change audit, the answer is in ephemeral Worker logs or nowhere.

### C. Allow the agent to raise its own ceiling with principal pre-authorization

**Rejected.** "Pre-authorized self-raise" collapses the boundary — a compromised tool result or prompt-injected instruction could exploit a standing authorization. The principal acts on each raise; the agent only proposes. (Inbound injection that could carry such an instruction is itself addressed by ADR 0027.)

## Consequences

**Positive.**

- The config surface becomes a real boundary: privileged, persisted, audited, floor-checked.
- ADR 0025's "agent can never raise its own ceiling" gains its enforcement on the config side, not just the runtime side.
- The legal-hold audit answers "when did autonomy change and who authorized it" — a concrete compliance and trust artifact.

**Negative / accepted.**

- The portal raise path now depends on the `customer.yaml` write path shipping (referenced as pending in `trust-ceiling.ts:66-67` and `src/lib/portal/customer-config.ts`). Per ADR 0025's sequencing, the autonomy axis is configurable in git-authored `customer.yaml` first; the self-serve portal raise lands only after this ADR's persist+audit path is real. That ordering is deliberate, not a gap.

## Verification

1. The trust-ceiling endpoint persists to `customer.yaml`/projection and emits an audit-log event (not just `console.info`).
2. The audit event records principal, skill/action-class, old value, new value, timestamp.
3. A request to raise an action class above its vertical floor is rejected and the rejected attempt is audited.
4. No agent/skill/tool code path writes an autonomy-affecting config value; grep for config writes outside the principal-authenticated handler returns nothing.
5. Lowering a ceiling is audited and immediate; raising is audited and floor-checked.

## References

- [ADR 0025 — Autonomy ceilings are configurable](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) (the decision that makes config a security boundary)
- [ADR 0011 — Multi-persona per customer](./0011-multi-persona-per-customer.md) (principal-only configuration surface)
- [ADR 0012 — customer.yaml storage](./0012-customer-yaml-storage.md) (git source of truth → materialized replicas; where the change persists)
- [ADR 0027 — Inbound trust boundary](./0027-inbound-trust-boundary.md) (injection vector that a self-raise path would expose)
- `src/pages/api/portal/operator/settings/trust-ceiling.ts` (the intent-log-only handler this ADR replaces)
- `src/lib/portal/customer-config.ts` (the pending write path)
- Strategy notes: `note_01KSS3TCTKWYVF6EZ04482X389`, `note_01KSTYSNC9CYPKYFJZ3TJ7F6RM`
