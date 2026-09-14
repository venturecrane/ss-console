---
title: No Imposed Entitlement Defaults — Configurable Across the Harness, Fail-Closed When Unauthored
date: 2026-06-02
status: accepted
captain: Scott Durgan
amends: 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md, 0005-external-send-identity.md
related-adr: 0026-config-surface-is-a-security-boundary.md, 0031-content-sensitivity-send-floor.md
---

# ADR 0035 — No Imposed Entitlement Defaults

**Status:** Accepted (Captain decision, 2026-06-02).

**Source:** Captain correction during the customer-zero connector session. The repeated friction — agents reasoning from, and designs being steered by, an assumed external-send draft default the Captain never authored — traced to an imagined default baked into both the doctrine docs and the enforcement code. This ADR removes it.

## Context

Entitlement in the Operator harness is meant to be **configurable across its full spectrum** — exposure, initiation, external send, autonomy level, tool access — per capability and per action-class, on independent axes (ADR 0025). The principal authors what the agent may do; the harness enforces exactly that, in code, audited, and the agent can never raise its own ceiling.

ADR 0025 stated this principle directly — §Decision: _"No autonomy posture is hardcoded."_ But the same ADR then contradicted itself in §4: _"Absent explicit configuration, every external action class defaults to `draft_for_review` with the approver as send identity."_ A `draft_for_review` fallback applied to unauthored action classes **is** a hardcoded posture. §4 contradicts the §Decision principle.

That contradiction was not academic. It was implemented:

- **In code.** `operator/adapter/trust_ceiling.py::_class_default()` returns `DRAFT_FOR_REVIEW` for `EXTERNAL_SEND` (and as the catch-all) when no per-action ceiling is authored. The overlay's ported copy (`hermes-smd-overlay/plugins/hermes-smd-trust/enforce.py`) and `shared/action_classes.py` carry the same "default `draft_for_review`" comments.
- **In the doctrine docs.** `0005-external-send-identity.md` (amendment note: _"the default exposure configuration"_; body: _"architectural, not advisory"_), `0025` §4 (_"draft-for-review is the default and a lockable floor"_), `0034` §line 24, `index.md`, `decision-stack.md` #45 (titled _"architectural invariant,"_ never amended), and `specs/operator/inbound-trust-boundary.md` (_"refused at the default ceiling"_).

The effect: every session inherited an assumed posture — draft-everything — that no engagement authored, and design decisions were repeatedly pulled toward it. The Captain's correction: **there are no imposed defaults. Draft-for-review external send is one configurable option, not the default and not an invariant.**

## Decision

**The harness imposes no entitlement default. It faithfully enforces what an engagement authors, and nothing more. An entitled action with no authored entitlement is fail-closed.**

### 1. No imposed posture, full spectrum

For every configurable axis of the harness (exposure, initiation, external send, autonomy level, tool access, identity posture), absence of authored configuration is **not** a cue to apply a "safe posture." There is no fallback posture. The configuration is the entitlement; the absence of configuration is the absence of entitlement.

**A safety property of the unconfigured state is not the product's identity.** The harness fails closed when nothing is authored — that is a correct _safety property_, and §2 below is exactly that. But fail-closed (and the older "draft-for-review" framing it replaced) must **never** be allowed to describe what the Operator _is_, its default posture, or its market position. The moment "it drafts for your review" becomes the product's identity, the product is back in the cage the configurability was meant to open. **The Operator has no default posture. It has whatever the engagement authors** — from draft-everything to trusted-autonomous-send, per action class. "Unconfigured is fail-closed" answers "what happens before you've said anything," not "what is this product."

### 2. Unauthored entitled action ⇒ fail-closed (REFUSED), not draft

When an externally-consequential action class (`EXTERNAL_SEND`, and the catch-all for any unrecognized entitled class) has **no authored ceiling**, `enforce()` resolves it to `REFUSED`: the action does not execute, and **no draft is produced**. A draft is itself a behavior the harness was never told to perform; producing one is an imposed posture by another name. No grant means no action.

This replaces ADR 0025 §4's _"defaults to `draft_for_review`."_ It makes ADR 0025's §Decision (_"no autonomy posture is hardcoded"_) literally true.

### 3. Draft-for-review external send is one configurable option

`draft_for_review` with the approver as send identity remains a fully valid, often-chosen entitlement value — authored explicitly per action class. It is no longer "the default," "the architectural foundation," or an "architectural invariant." It is a setting. ADR 0005's internal/external persona split, drafts mechanism, and audit preamble are preserved **as the behavior you get when you author that option**, not as a baseline imposed on engagements that authored nothing.

### 4. Non-raisable pins (compliance locks) are retained — as authored constraints, not defaults

The vertical-floor mechanism (`vertical_floors` + `_most_restrictive` in `trust_ceiling.py`) stands. A vertical pack or engagement MAY author a non-raisable pin for an action class (e.g., a regulated-legal pack pinning `EXTERNAL_SEND = draft_for_review` so customer config cannot raise it). This is a deliberate, authored configuration where compliance requires it — never a posture imposed on engagements that did not author it. A pin constrains; it does not default.

### 5. What is unchanged

- **Enforced in code, not prompt** (ADR 0025 §5). The model can ask; the gate decides.
- **The agent never raises its own ceiling** — a control-plane act by the human principal (ADR 0026).
- **Reversibility floors stand**: `COMMITMENT` and `DESTRUCTIVE` still require explicit current-turn approval (safety invariants 1 and 3). These are reversibility controls, not autonomy postures, and they are already fail-closed.
- **`READ` remains allowed at the ceiling layer**, because read _breadth_ is governed by the authored scope envelope (`scope.email_folders_visible`, etc.). An engagement that authors no scope can read nothing — fail-closed by the same principle, enforced one layer over.
- **The content-sensitivity floor (ADR 0031)** applies on top of an authored ceiling: money / contract / scope / legal classes drop to draft even under an authored `autonomous` ceiling. The floor narrows an authored grant; it is not a default applied to an unauthored one.

## Consequences

**Positive.**

- The harness stops carrying a posture no one authored. "Configurable trust, enforced in code, audited" becomes literally true — there is no hidden baseline contradicting it.
- Designs stop being steered into an imagined default. The question is always "what did the engagement author?" — never "what does the system assume?"
- Fail-closed is the strictly safer unauthored behavior: an unconfigured external-send capability does nothing, rather than silently emitting drafts an engagement never asked for.

**Negative / accepted.**

- An engagement that enables an external-send-capable capability but forgets to author its `EXTERNAL_SEND` ceiling gets `REFUSED`, not a draft. This is intended: it surfaces the missing grant loudly instead of papering over it with an assumed posture. The validator follow-on (below) makes the omission a config-time error rather than a runtime surprise.
- The safety_substrate invariant set changes: assertions of "unauthored external_send ⇒ draft" become "unauthored external_send ⇒ refused."

**Customer-zero impact: none.** `operator/customers/smd/customer.yaml` authors `action_ceilings.external_send: autonomous` explicitly, so Crane's autonomous AgentMail send is an authored grant and is unaffected by the fail-closed change.

## What this supersedes

- **ADR 0025 §4** ("draft-for-review is the default") and **§6** ("ADR 0005's persona split stands as the default"): the _default_ framing is removed. §4's lockable-floor and §6's configurable-identity-posture survive as authored options/constraints. The rest of ADR 0025 (two axes, enforced-in-code, agent-never-self-raises, reversibility floors) stands.
- **ADR 0005's residual absolutism**: the body's "architectural, not advisory" and "promotion to autonomous is not available for any skill whose output crosses the external boundary" were already overturned by ADR 0025; this ADR completes the reconciliation by also removing the _default_ status. ADR 0005's persona split, drafts mechanism, and compliance reasoning persist as the rationale for _why a customer would author_ the draft-for-review posture and _why a regulated vertical would pin it_.

## Verification

1. `resolve_ceiling(EXTERNAL_SEND, …)` with no authored `action_ceilings` returns `REFUSED` (both `operator/adapter/trust_ceiling.py` and the overlay `enforce.py`).
2. An authored `external_send: autonomous` still permits send; an authored `draft_for_review` still routes to draft; a vertical pin still cannot be raised. (Authored behavior unchanged.)
3. The doctrine docs carry no surviving "the default" / "architectural invariant" framing for the draft-for-review posture; all point here.
4. Customer-zero (`smd`) boots and Crane's autonomous send path is unaffected (authored ceiling).

## References

- [ADR 0025 — Autonomy ceilings are configurable](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) (amended: removes the `draft_for_review` _default_; retains the two-axis model, the lockable floor as an authored constraint, and the residual invariants)
- [ADR 0005 — External-send identity](./0005-external-send-identity.md) (amended: declassified from default/invariant to one authored option; persona split + drafts + compliance reasoning preserved as the rationale for choosing it)
- [ADR 0026 — Config surface is a security boundary](./0026-config-surface-is-a-security-boundary.md) (the agent never raises its own ceiling)
- [ADR 0031 — Content-sensitivity send floor](./0031-content-sensitivity-send-floor.md) (narrows an authored grant; not a default)
