---
title: Autonomy & Governance
section: product
order: 2
summary: How the Operator's autonomy is bounded - persona-level exposure ceilings plus per-skill initiation flags enforced in code, fail-closed when unauthored, and a taint gate that stops an injected message from driving a privileged action
sources:
  - label: ADR 0025 - Autonomy ceilings are configurable
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0025-autonomy-ceilings-configurable-exposure-vs-initiation.md
  - label: ADR 0056 - Persona exposure + skill initiation entitlements
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0056-persona-exposure-skill-initiation-entitlements.md
  - label: ADR 0037 - The Operator Thesis
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
  - label: Trust-ceiling decision logging (spec)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/operator/trust-ceiling-logging.md
  - label: Inbound trust boundary (spec)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/operator/inbound-trust-boundary.md
  - label: Safety invariants #6 and #7 (spec)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/operator/safety-invariants.md
---

## Autonomy is two axes, not one dial

The harness enforces, in code, a ceiling on what the agent may do without a human. Per ADR 0025, "autonomy" is not a single setting - it is **two independent axes**:

- **Initiation** - does the agent act unprompted? A cron-triggered or webhook-triggered run is high-initiation; a run that only ever responds to a human turn is low-initiation. This axis governs the "when."
- **Exposure** - does the agent's action cross the boundary to an external party, and does a human approve before it does? Drafting an internal note is low-exposure; firing an email to opposing counsel is high-exposure. This axis governs the blast radius.

These are orthogonal, and separating them is what lets a customer express the most common wanted posture: "the agent may initiate an accounts-receivable chasing run on a cron (high initiation), but every outbound message drafts for human review (low exposure)." A different trusted customer might author the inverse: "may send routine transactional email autonomously (high exposure), but only ever when a human triggers the run (low initiation)." Before ADR 0025, a single scalar ceiling conflated the two and neither posture was expressible. ADR 0025 named the two axes as a concept; ADR 0056 later made them the literal shape of the authored configuration.

## How the entitlements are authored: exposure per persona, initiation per skill

Every tool call the agent makes is classified by its blast radius into an **action class** (`trust_ceiling.py`): `READ`, `INTERNAL_WRITE`, `EXTERNAL_SEND`, `COMMITMENT`, `DESTRUCTIVE`, and `CODE_EXECUTION`. The Operator previously mixed authorization concepts across scalar per-skill ceilings, per-skill action overrides, scope-level ceilings, and mailbox-level overrides; that made exposure and initiation look like one setting and was not enforceable enough for a flag-day runtime contract. **ADR 0056** (accepted 2026-06-26, deployed fleet-wide) replaced all of it with one entitlement model in `customer.yaml`:

- **Exposure is persona-level.** Each persona carries a sparse `entitlements.exposure` map from action class to a ceiling value: `autonomous` (executes), `draft_for_review` (writes for human review and notifies), or `refused` (blocks and logs the attempt). Missing action classes fail closed at runtime and render as unconfigured in the UI. `read` is deliberately not customer-authored; the enforcement layer always allows reads.
- **Initiation is skill-level.** Every enabled skill declares `initiation.manual`, `initiation.scheduled`, and `initiation.webhook`. A cron entry requires `scheduled: true`, a webhook trigger requires `webhook: true`, and manual or on-demand surfaces require `manual: true`.

A persona can be `autonomous` for `internal_write` and `draft_for_review` for `external_send` in the same breath. `COMMITMENT` and `DESTRUCTIVE` carry current-turn approval floors that remain hard runtime floors regardless of authored exposure - they are never autonomous without current-turn approval - and those floors are a different concern from exposure (ADR 0025; ADR 0056).

The retired fields are gone with no compatibility shim: the per-skill scalar `trust_ceiling`, per-skill `action_ceilings`, the scope-level `trust_ceiling` and `action_ceilings`, the managed-mailbox `action_ceilings` overrides, and the provisioning `skill_trust_ceiling`. Validators reject them as legacy entitlement fields. The governance audit vocabulary follows the model: `entitlement_exposure`, `entitlement_initiation`, `skill_enabled` (ADR 0056).

## Fail-closed when unauthored

The most important governance property: **there is no imposed default.** Absent authored configuration, an entitled action class is fail-closed - refused, with no send and no draft (ADR 0025 as amended by ADR 0035; ADR 0037 Tenet 3).

This is a safety property of the unconfigured state, not an identity the product has. The Operator does not "assume draft-for-review" any more than it "assumes autonomous send." When reasoning about what an Operator does for a customer, the question is always "what did the engagement author?" - never "what does the system assume?" (ADR 0037, Tenet 3). Unconfigured is a fail-closed safety state, full stop.

This corrects a stale framing worth naming so it does not creep back: draft-for-review external send is **one authored option**, not the default and not the product's identity (ADR 0037, Tenet 3, correcting ADR 0025 §4's "default" language). For a regulated vertical, a vertical pack can pin an action class to a non-raisable floor - the law pack pins `EXTERNAL_SEND = draft_for_review` - so the compliance posture holds exactly where it is load-bearing without being imposed on customers who do not need it (ADR 0025 §4). Vertical floors only ever narrow exposure (ADR 0056), and customer configuration can never raise a ceiling above the most restrictive of the vertical floor and the authored ceiling.

## The agent can never raise its own ceiling

Three residual invariants stay architectural and non-configurable (ADR 0025 §5):

- **Enforced in code, not in prompt.** The model can ask all it wants; the trust gate decides. The gate runs live in the overlay `hermes-smd-trust` plugin's `pre_tool_call` hook.
- **Accountable to a named human principal.** Every external action is attributable to a specific human of record - the channel owner or configured sender identity - and recorded in the audit log regardless of ceiling.
- **The agent cannot raise its own ceiling.** A ceiling change is a control-plane act performed by the human principal, persisted and audited, never performed by the agent or by a prompt.

Because exposure is configurable, the configuration surface itself is a security boundary - raising a ceiling is a privileged act, which is why it lives in Captain-authored, git-reviewed `customer.yaml` before any self-serve surface exists (ADR 0025; governance companion ADR 0026).

## The broker validates identity, not intent

For the Google Workspace path, the agent reaches Google only through first-class tools that mint a single-use, signed grant from a **capability broker** running as a separate OS principal. The broker authenticates the calling process by kernel-attested peer credentials and independently re-validates the impersonation subject and sender identity against its own read of `customer.yaml` - it never trusts the gateway's claim (operator threat model, §2.2).

The boundary worth stating plainly: the broker validates **identity** (who is this, who may it act as), never **intent** (is this particular action a good idea). Intent is the trust gate's job and the human reviewer's job. See `/admin/playbook/security-trust` for the full threat model, including where the registered-tool wall is sound and where the exposure underneath it lives.

## The taint gate: an injected message cannot drive a privileged action

The Operator reads channels anyone on earth can write to - email above all. So untrusted inbound content (email bodies, webhook payloads, connector and MCP results, fetched pages) is **attributed** with its provenance and trust class and then **structurally separated** from the instruction channel before it reaches the engine's reasoning context (inbound trust boundary spec, ADR 0027). Each inbound surface builds an envelope; the overlay's `pre_llm_call` hook wraps the untrusted content in a nonce-fenced quarantine block at one convergence point. The closing sentinel carries an unguessable per-item nonce, so content cannot end its own quarantine early.

The fence is defense-in-depth, not the wall. The enforcing control is a **sticky `SessionTaint` plus a taint gate**: a turn that has ingested untrusted inbound is taint-marked, and a taint-marked turn cannot autonomously send, destroy, or execute code, regardless of how the fence behaved (inbound trust boundary spec; ADR 0026 - inbound text can never drive a ceiling raise). The agent may reason about untrusted content; it may not take a privileged action because untrusted content told it to. The load-bearing CI assertion is that an injected `external_send` is refused by the gate when the action class is unauthored, fence or no fence (inbound trust boundary spec §CI corpus).

## The CODE_EXECUTION class

Arbitrary code execution is the deepest exposure under the registered-tool layer. `execute_code` is its own action class and is **fail-closed unless authored** - on a customer with no authored code-execution entitlement, it is fully shut (CLAUDE.md, Operator security hardening). This closes the hole where ungoverned code execution could bypass the registered-tool wall entirely, and it is enforced by the same `pre_tool_call` gate as every other class.

## Every decision is logged

The trust gate's `enforce()` returns an allow / draft / refuse decision per tool invocation, and a logging wrapper writes one audit row per decision with a closed-vocabulary reason (trust-ceiling-logging spec). The closed enums - the `Decision`, `CeilingLevel`, `ActionClassName`, and `DecisionReason` sets - keep the dashboard aggregations stable as the product evolves. Substantive payloads (the email body the agent wanted to send) go to R2; the audit row carries only the decision, its reason, and provenance, never the content bytes or PII (trust-ceiling-logging spec).

This is what makes the governance legible rather than opaque: a human employee's judgment is a black box, but the Operator's every gated decision is a row a human can read, aggregate, and act on.

Two spec caveats the handbook should carry honestly. Several of these enforcement seams are specified and unit-tested in the safety_substrate but are marked **TARGET STATE - not yet wired** into the live Hermes dispatch path: citation enforcement (`enforce_citations`), the `verify_storage_bindings` boot check, and the `log_decision` dispatch integration each note that the function exists and is tested but is not yet called from production dispatch (safety-invariants spec; trust-ceiling-logging spec). The contracts are settled; the wiring into the Hermes runtime surface is tracked separately. The live, deployed controls are the `pre_tool_call` trust gate, the inbound fence, and the taint gate.

> TODO(why): The safety-invariants and trust-ceiling-logging specs mark several seams "TARGET STATE - not yet wired," but I could not confirm from the docs alone the current wired-vs-unwired status on the live customer-zero Machine as of this writing. The Operator security audit memory and threat model suggest the trust gate, inbound fence, and taint gate are live, but a precise per-seam current status would need a runtime check against the deployed overlay ref. Confirm before citing any specific seam as "live in production."
