---
title: Autonomy Ceilings Are Configurable — Split Initiation From Exposure
date: 2026-05-29
status: accepted
captain: Scott Durgan
amends: 0005-external-send-identity.md
related-issue: https://github.com/venturecrane/ss-console/issues/828
related-note: note_01KSS3TCTKWYVF6EZ04482X389, note_01KSTYSNC9CYPKYFJZ3TJ7F6RM
---

# ADR 0025 — Autonomy Ceilings Are Configurable

**Status:** Accepted (Captain decision, 2026-05-29), **amended 2026-06-02 by [ADR 0035](./0035-no-imposed-entitlement-defaults.md).** This ADR correctly made autonomy a configurable per-action-class ceiling, but it then introduced a contradiction: §Decision says "no autonomy posture is hardcoded," while §4 makes unauthored external action classes **default to `draft_for_review`** — a hardcoded posture. ADR 0035 resolves this in favor of §Decision: there is **no imposed default**. An unauthored entitled action is fail-closed (refused — no send, no draft). Draft-for-review external send (§4) is **one authored option**, not "the default"; the vertical-pack-lockable floor (§4) survives as an **authored constraint**, not a default. **Read every "default" / "secure default" / "conservative default" phrase below that describes `draft_for_review` external send as superseded** (this includes §4, §6, the Consequences bullets, and the migration plan): the unconfigured state is fail-closed, and "draft-everything" is one thing an engagement may author, never the product's default posture or identity. The two-axis model, enforced-in-code, agent-never-self-raises, and reversibility floors all stand.

This ADR records the decision and mandates a product modification; the code change is sequenced in the migration plan below and tracked by a follow-on issue.

**Source:** The 2026-05-28/29 working session that defined the Operator product as a **harness** — a set of functions and guarantees independent of the underlying engine (recorded in note `note_01KSS3TCTKWYVF6EZ04482X389`, "The Harness Is the Product"). A grounded code audit the next day (`note_01KSTYSNC9CYPKYFJZ3TJ7F6RM`) confirmed against live code that the autonomy posture is **hardcoded**, not configured: `operator/adapter/trust_ceiling.py:117-127` refuses every autonomous external send regardless of how the customer's ceiling is set. The session named this the keystone correction. This ADR locks it.

---

## Context

### The model: autonomy is two axes, not one

The harness enforces, in code, a ceiling on what the agent may do without a human. The session established that "autonomy" is not a single dial — it is **two independent axes**, and the current implementation collapses them:

- **Initiation** — _does the agent act unprompted?_ A cron-triggered or webhook-triggered run is high-initiation; a run that only ever responds to a human turn is low-initiation. This axis governs the **"when."**
- **Exposure** — _does the agent's action cross the membrane to an external party, and does a human approve before it does?_ Drafting an internal note is low-exposure; firing an email to opposing counsel is high-exposure. This axis governs the **"blast radius."**

These are orthogonal. A wanted configuration is "the agent may _initiate_ an AR-chasing run on a cron (high initiation) but every outbound message _drafts_ for human review (low exposure)." Another wanted configuration — for a customer who has built trust and wants velocity — is "the agent may send routine transactional email autonomously (high exposure), but may never sign or commit (exposure capped below COMMITMENT)." Today neither axis is independently expressible.

### What the code actually does today

`ActionClass` (`trust_ceiling.py:32-39`) is the right primitive: it classifies every tool call by blast radius — `READ`, `INTERNAL_WRITE`, `EXTERNAL_SEND`, `COMMITMENT`, `DESTRUCTIVE`. The ceiling vocabulary, however, is a **single per-skill enum** — `autonomous` / `draft_for_review` / `refused` (`src/lib/operator/customer-yaml/types.ts:118`, validated per-skill at `src/lib/operator/customer-yaml/sections-personas.ts:241`). And the `EXTERNAL_SEND` branch ignores that ceiling on the exposure axis:

```python
# trust_ceiling.py:117-127
if action == ActionClass.EXTERNAL_SEND:
    if ceiling == Ceiling.AUTONOMOUS and current_turn_approval:
        return EnforcementDecision(allowed=True, ...)
    if ceiling == Ceiling.AUTONOMOUS:
        # Even autonomous skills don't send to external parties without explicit approval
        return EnforcementDecision(allowed=False, ...)   # ← hardcoded refusal
```

There is **no value of any configured ceiling** that permits an autonomous external send. `test_invariant_2_no_external_send_without_confirmation.py` Scenario B (lines 43-60) asserts this as a safety invariant: "autonomous external_send WITHOUT approval was refused." Live enforcement runs in the overlay `hermes-smd-trust` plugin's `pre_tool_call` hook (fail-closed, per the audit), which calls this same logic. So the hardcoding is enforced end-to-end.

### Why this contradicts the product we decided to sell

The harness thesis is explicit: **the harness invariant is "every action is gated by whatever ceiling is _configured_, enforced in code, audited" — not "a human always approves."** Per-message human approval on external send is _one value on the exposure axis_, not a property of the product. Some customers will want the employee to text, email, even call their customers autonomously, and will pay for exactly that. Hardcoding the strictest value forecloses the SKU's range (single agent → team) that ADR 0004 sells.

### The collision with ADR 0005, named honestly

ADR 0005 (external-send identity) decided that every customer-bound external message ships under the human reviewer's identity, and stated this is **"architectural, not configurable"** (§Decision, line 41), explicitly declining the hybrid because "weakening it to 'configurable per skill' surrenders the moat" (§Consequences, line 64). This ADR **overturns that specific modality.** It does not discard the draft-for-review posture. The distinction the session drew, and that this ADR makes load-bearing, is between:

- **The mechanism** (a human reviews each draft and sends from their own account) — this becomes the **default** and a **vertical-pack-lockable floor**, no longer a global absolute.
- **The residual invariant** (what stays architectural and non-configurable): every external action is enforced against the _configured_ ceiling in code; every external action is attributable to a **named human principal of record** and fully audited; and the agent can **never raise its own ceiling**.

The compliance and liability reasoning in ADR 0005 (ABA Formal Opinion 512, state AI-disclosure rules, the supervising-attorney requirement) does not evaporate — it is **why a regulated vertical would author and lock the draft-for-review posture.** A law-vertical pack (ADR 0022 compliance constraints) pins `EXTERNAL_SEND = draft_for_review` as a non-raisable floor, so the moat and the disclosure posture hold exactly where they are load-bearing, while the customers who want autonomy get the axis. The moat was never "humans always approve"; per the thesis it is **the harness + the guide** — configurable trust, enforced in code, audited, wired into a specific business by someone who sat in the owner's office.

### The consequence that makes the next ADR necessary

Once exposure is configurable, **the configuration surface becomes a security boundary.** Raising a ceiling — letting the agent send autonomously — is a privileged act that must be performed by the human principal, persisted, and audited; the agent must never be able to perform it on its own behalf. That governance requirement is its own decision and is recorded in ADR 0026 (Config Surface Is a Security Boundary), which this ADR requires as a companion.

---

## Decision

**Autonomy is enforced as two independent, configurable axes — initiation and exposure — expressed per action-class (and, where a connector supports it, per channel), enforced in code, and audited. No autonomy posture is hardcoded. The agent can never raise its own ceiling.**

Concretely:

### 1. The ceiling is an action-class map, not a single skill enum

A skill's (or persona's) trust configuration becomes a mapping from `ActionClass` to a ceiling value, rather than one scalar applied to the whole skill. The existing values (`autonomous` / `draft_for_review` / `refused`) are retained; what changes is that `EXTERNAL_SEND`, `COMMITMENT`, and `DESTRUCTIVE` each carry their own configured value. A skill can be `autonomous` for `INTERNAL_WRITE` and `draft_for_review` for `EXTERNAL_SEND` in the same breath. `READ` remains always-allowed; `COMMITMENT` and `DESTRUCTIVE` retain their current-turn-approval floors (invariants 1 and 3 are unchanged — those are about _reversibility_, a different concern from exposure).

### 2. Initiation is authorized separately from exposure

Whether the agent may run unprompted (cron, webhook, delegated task) is a distinct grant from what it may do once running. A cron schedule in `customer.yaml` authorizes _initiation_; it does not implicitly raise the _exposure_ ceiling of anything the cron run does. (Today the same scalar ceiling is re-applied, conflating the two — see the audit's "initiative" PARTIAL verdict.)

### 3. `EXTERNAL_SEND` autonomy is permitted when, and only when, it is configured

The hardcoded refusal at `trust_ceiling.py:117-127` is removed. `enforce()` consults the configured `EXTERNAL_SEND` ceiling: `autonomous` permits send without per-turn approval; `draft_for_review` routes to a draft (routing to a draft); `refused` blocks. The same applies through the overlay `hermes-smd-trust` hook.

### 4. Draft-for-review external send is a lockable floor, not an absolute

> _**Amended by [ADR 0035](./0035-no-imposed-entitlement-defaults.md):** the "default" half of this section is superseded. There is no imposed default. Absent authored configuration, an external action class is **fail-closed (refused — no send, no draft)**, not `draft_for_review`. Draft-for-review external send is one authored option; the lockable floor below survives as an authored constraint._

- **Default.** Absent explicit configuration, an external action class authored to `draft_for_review` ships under the approver as send identity. The secure posture is what you get for free; autonomy is an opt-in the principal must take deliberately.
- **Vertical floor.** A vertical pack (ADR 0022) may declare a non-raisable ceiling for an action class. The law pack pins `EXTERNAL_SEND = draft_for_review`. Customer configuration cannot raise above a vertical floor — the existing "cannot raise above authored" rule (`trust_ceiling.py` docstring) generalizes to "cannot raise above the most restrictive of {vertical floor, authored ceiling}."

### 5. The residual invariants stay architectural (non-configurable)

- **Enforced in code, not prompt.** Unchanged from invariant 5. The model can ask all it wants; the adapter and the overlay hook decide.
- **Accountability to a named human principal.** Every external action is attributable to a specific human of record (the channel owner / configured sender identity). A draft-for-review send is one realization (the approver _is_ the principal and the sender); an autonomous-send configuration still names the principal who owns the channel and authorized the ceiling, recorded in the audit log.
- **The agent cannot raise its own ceiling.** A ceiling change is a control-plane act performed by the human principal, never by the agent or by prompt. (Governance: ADR 0026.)

### 6. Identity posture is configurable alongside exposure

ADR 0005's internal/external persona split (the persona is fully visible internally; external presence is the reviewer) stands as the **default**. A customer who configures autonomous external send may also configure an agent-as-sender identity for that channel; doing so is the same kind of privileged, audited config act as raising the exposure ceiling, and is subject to any vertical floor. ADR 0005's drafts mechanism, audit-trail preamble, and internal-persona rules are otherwise preserved.

### 7. Two enforcement layers — this ADR governs the gate, not the adapter surface

There are **two** code layers that today encode "no autonomous external send," and this ADR addresses only the first:

1. **The trust-ceiling gate** (`adapter/trust_ceiling.py::enforce()`, run live by the overlay `hermes-smd-trust` `pre_tool_call` hook). This is the configurable authority layer — the subject of this ADR. After implementation, the gate consults the configured per-action ceiling and permits a send tool to fire when `external_send` is raised to `autonomous` (floored by the vertical).
2. **The capability-adapter surface ban** (`src/lib/operator/capabilities/conformance.ts` `NO_AUTONOMOUS_EXTERNAL_SEND` + `BANNED_METHOD_NAMES`). Our own `build:` capability adapters are _structurally_ forbidden from exposing a send method at all — the `Email` adapter has no `send`, only draft. This is the deeper, structural form of draft-only external send (ADR 0006).

The consequence: raising the ceiling makes autonomous send real on the **MCP-connector path** (ADR 0020's primary path — an MCP server exposes a `send` tool the gate governs). It does **not** make our `build:` adapters send, because those have no send method to call regardless of ceiling. That is the intended conservative posture: build adapters stay structurally draft-only; autonomous send is reached through a ceiling-gated MCP tool, never by an adapter that smuggles in a `send`. Whether to ever lift the `build:`-adapter ban is a separate architectural decision, deliberately **not** decided here.

---

## Alternatives Considered

### A. Keep ADR 0005 fully absolute; make only non-message action classes configurable

**Rejected by Captain.** This was the narrow reading (exposure-as-config applies to everything except external messaging, which stays hardcoded draft-only). It preserves the moat framing untouched but forecloses the autonomous-communication configurations the SKU is meant to sell, and it leaves the product unable to express "this trusted customer's employee may send routine email on its own." The Captain's direction is explicit: _modify the product to make this configurable._

### B. Make exposure configurable but leave initiation fused to it

**Rejected.** Re-applying one scalar ceiling to both axes is the current behavior and is exactly the conflation the audit flagged. "May run on a cron" and "may send autonomously" are different grants with different risk; collapsing them means a customer can't have a self-starting agent that still drafts everything for review — the single most common wanted posture.

### C. Make it configurable with no vertical floor

**Rejected.** Without a non-raisable floor, a regulated customer (or a careless one) could configure away the disclosure/liability posture that ADR 0005 correctly identified as a compliance requirement in law and similar verticals. The vertical floor is what lets the same product be maximally flexible for an e-commerce shop and maximally locked for a law firm. It also preserves ADR 0005's moat where the moat actually lives.

### D. Leave the hardcoding; document it as a known limitation

**Rejected.** This is the status quo the audit flagged. A hardcoded strictest-value is safe but is not the product — it caps the SKU and contradicts the harness thesis. "Safe by accident of being inflexible" is not the same as "safe by configurable design with a code-enforced floor."

---

## Consequences

**Positive.**

- The product can finally express its own range: from draft-everything (the conservative default) to trusted-autonomous-send, per action class, per customer — the spread ADR 0004's SKU promises.
- Initiation and exposure decouple cleanly, so "self-starting but always-drafting" and "human-triggered but autonomous-send" both become expressible.
- Draft-for-review external send survives as an authored option and the regulated-vertical floor, so the compliance posture and the law-vertical moat are preserved precisely where they matter, without imposing them on customers who don't need them.
- The configuration surface is promoted to a security boundary, which forces the governance discipline (ADR 0026) that the audit found missing (the trust-ceiling change endpoint currently logs intent only, no persist, no audit — `src/pages/api/portal/operator/settings/trust-ceiling.ts:68`).

**Negative / accepted.**

- This overturns the "architectural, not configurable" framing that ADR 0005 leaned on as a competitive claim. The mitigation is the vertical floor + the accountability invariant: the defensible claim shifts from "we never let the AI send" to "trust is configurable, code-enforced, audited, and floored by your vertical's compliance constraints" — a stronger and more honest claim, but a different one.
- Removing a hardcoded safety refusal raises the stakes on the config-governance work. Until ADR 0026 lands, the ceiling map must ship with the secure default and **no portal path to raise `EXTERNAL_SEND`** — i.e., the axis is configurable in `customer.yaml` (Captain-authored, git-reviewed) before it is configurable via any self-serve surface. Sequencing below enforces this.
- The safety_substrate invariant set changes shape. Invariant 2 stops asserting "always refuse autonomous external send" and starts asserting "enforce the configured `EXTERNAL_SEND` ceiling, and never exceed the vertical floor." That is a more complex property and needs fixtures for each ceiling value plus a floor-violation case.

**Out of scope.**

- The reversibility floors (COMMITMENT never autonomous without current-turn approval; DESTRUCTIVE same) are unchanged. This ADR is about _exposure and initiation_, not _reversibility_. Invariants 1 and 3 stand as-is.
- The inbound direction (sanitizing/attributing content that arrives _from_ outside before it reaches the engine) is a separate membrane edge, governed by ADR 0027.

---

## Migration plan (sequenced; pre-launch, zero-customer venture)

The product has never booted a paying customer, so the sequencing optimizes for "secure default first, self-serve raising last," not for backward compatibility.

1. **This ADR + companion 0026.** Record the decision and the config-governance requirement together; 0026 defines how a raise is persisted and audited. (0026 is the next ADR in this batch.)
2. **Ceiling-as-action-class-map in the schema.** Extend `customer.yaml` trust configuration from a per-skill scalar to an `ActionClass → ceiling` map, defaulting every class to `draft_for_review` (external) / `autonomous` (internal write) / the existing reversibility floors. Update `types.ts`, `sections-personas.ts`, and the validator. Authored-only at this step (git source of truth, ADR 0012) — no portal raise path yet.
3. **De-hardcode `enforce()`.** Remove the `trust_ceiling.py:117-127` refusal; consult the configured `EXTERNAL_SEND` ceiling; apply the floor rule (`min(vertical_floor, authored)`). Mirror the change in the overlay `hermes-smd-trust` `pre_tool_call` hook so live enforcement matches.
4. **Rewrite invariant 2 + add fixtures.** Replace `test_invariant_2`'s "always refuse" assertions with per-ceiling-value assertions and a floor-violation refusal case. Add a vertical-floor fixture (law pack pins `EXTERNAL_SEND`).
5. **Vertical floor mechanism.** Wire the ADR 0022 vertical pack to declare non-raisable action-class floors; assert in code that authored/portal config cannot raise above them.
6. **Amend ADR 0005's status to reference this ADR.**
7. **(After 0026) Self-serve raise path.** Only once config-governance (persist + audit + principal-authentication on a ceiling change) is real does the portal expose a path to raise `EXTERNAL_SEND`. This is the last step deliberately.

Steps 2–7 are a follow-on issue, not part of this ADR's PR; this PR lands the ADR and the 0005 cross-reference.

---

## Verification

How we know we are following this decision:

1. `customer.yaml` accepts an `ActionClass → ceiling` map; the validator rejects a config that raises an action class above its vertical floor.
2. `enforce()` contains no unconditional `EXTERNAL_SEND` refusal; a config with `EXTERNAL_SEND = autonomous` permits send without current-turn approval, and the overlay hook agrees.
3. `test_invariant_2` asserts per-ceiling-value behavior and a floor-violation refusal, not a blanket "always refuse."
4. A law-vertical fixture demonstrates `EXTERNAL_SEND` cannot be raised above `draft_for_review` by customer config.
5. Every external action in the audit log names a human principal of record regardless of ceiling.
6. No code path lets the agent (or a prompt) raise a ceiling; raises occur only through the governed control-plane act defined in ADR 0026.

---

## References

- [ADR 0004 — Productized Operator offering](./0004-productized-operator-offering.md) (the SKU whose range this unblocks)
- [ADR 0005 — External-Send Identity](./0005-external-send-identity.md) (amended: declassified from architectural-absolute to default + vertical-pack-lockable floor; identity/persona split, drafts mechanism, and compliance reasoning preserved)
- [ADR 0011 — Multi-persona per customer](./0011-multi-persona-per-customer.md) (internal/external persona identity)
- [ADR 0022 — Vertical pack architecture](./0022-vertical-pack-architecture.md) (the compliance-constraint / floor mechanism)
- [ADR 0026 — Config surface is a security boundary](./0026-config-surface-is-a-security-boundary.md) (companion; governs how a ceiling raise is persisted and audited)
- Strategy notes: `note_01KSS3TCTKWYVF6EZ04482X389` (harness thesis), `note_01KSTYSNC9CYPKYFJZ3TJ7F6RM` (build audit)
- `operator/adapter/trust_ceiling.py` (the `enforce()` logic and `ActionClass` enum)
- `operator/safety_substrate/tests/test_invariant_2_no_external_send_without_confirmation.py` (the invariant being reshaped)
- `src/lib/operator/customer-yaml/types.ts`, `sections-personas.ts` (the ceiling vocabulary and validator)
- [Issue #828](https://github.com/venturecrane/ss-console/issues/828) (external-send identity origin)
