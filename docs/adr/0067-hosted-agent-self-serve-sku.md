# ADR 0067: Hosted Agent — self-serve concierge SKU

**Status:** Accepted 2026-07-06 (Captain decision; decision-stack #51)

**Amends:** [ADR 0004](./0004-productized-operator-offering.md) (adds a second recurring SKU beside the Operator retainer). **Carves out:** [ADR 0037](./0037-operator-thesis.md) Tenet 1 for this SKU only. **Supersedes for one page only:** the positioning-spine "no published dollar amounts / no signup" locks (see the spine decision log entry, same date). **Leans on:** [ADR 0032](./0032-inbound-webhook-architecture.md) (channel constraint), [ADR 0063](./0063-operator-launch-pricing.md) (Operator pricing unaffected), [ADR 0065](./0065-operator-offboarding-and-dunning.md) (dunning posture inherited).

> **Amended 2026-10-31 by [ADR 0100](./0100-one-operator-core-and-authored-capabilities.md) W6.** "Hosted Agent" is no longer a second product. There is one product, the Operator; what this ADR defines is the Operator's **self-serve offering**: its commercial terms (price, founding coupon, BYO key, plan-retirement clause, all unchanged) and its starting capabilities (one persona, allowlisted channels, `external_send: confirm`). What changed: the seat template `operator/customers/_hosted-template/` became the self-serve preset of the one template, `operator/customers/_template/presets/self-serve/customer.yaml`; the portal pages moved from `/portal/products/hosted-agent/*` into the Operator section at `/portal/products/operator/setup/*`, and the admin queue from `/admin/hosted-agent` to `/admin/operator/intake`, each old URL kept working by a 301 (`src/lib/routing/legacy-redirects.ts`); the portal shows one Operator tab, card and billing label for every Operator a client owns; buyer and team emails say Operator and self-serve. Stored values are unchanged and read as one product: `subscriptions.product_slug = 'hosted-agent'`, `seat.product: hosted-agent`, the `hosted_agent_intake` table, the API routes, the Stripe product and coupon, and the `/agent/terms` document, which still names the plan "Hosted Agent" until the Captain re-papers it. The decisions below stand for the self-serve offering; where they say "Hosted Agent" or "SKU", read the Operator's self-serve offering.

## Context

The Operator substrate — per-customer Fly Machines running the pinned Hermes Agent runtime with the SMD overlay, one-command idempotent provisioning, fail-closed entitlements, seven boot-enforced safety invariants, signature-gated webhooks, and a tamper-resistant audit log — is production-proven on customer-zero and the first paid seat. That machinery supports a second, lighter product with near-zero marginal build.

The market validated the demand and handed us the differentiator. Agent hosting is a crowded commodity ($3-59/mo, dozens of providers selling "deployed in 30 seconds"), the Hermes ecosystem is very large and growing, and the spring 2026 agent-security crisis (tens of thousands of exposed instances, a community skill registry found ~20% malicious) made **trust** the category's unmet need. The commodity hosts sell uptime; nobody sells a safely-run agent.

Beachhead buyer: the **aware-but-unwilling-to-operate** segment — people who know what an always-on agent is and will not run a VPS. They arrive pre-sold on the category; we win the "why you, not the $3 host" argument with the safety harness we already shipped.

## Decision

Launch **Hosted Agent** (working name; route `/agent`, product slug `hosted-agent`): a self-serve subscription for an always-on personal Hermes agent, run and safety-gated by SMD on the same substrate as the managed Operator.

### SKU shape

1. **Price: $79/month, published on the product page.** The first 25 subscriptions get a founding price of **$49/month for as long as the subscription stays active**, enforced by a Stripe coupon (`amount_off` $30, `duration=forever`, `max_redemptions=25`) — Stripe enforces both the cap and the for-life discount; no app-side counter. A churned founding seat is consumed (Stripe never decrements redemptions); accepted. The product terms include a plan-retirement clause (retirement with notice and a migration path) so the founding-price obligation is bounded by the plan's life, not the firm's.
2. **BYO Anthropic key.** The customer supplies their own Anthropic API key; our margin is hosting and management, insulated from inference usage. Intake requires the customer to create a **dedicated key with a spend limit set in their Anthropic console**, and the product page states plainly that we cannot see or cap their Anthropic spend. Key custody follows the write-only pattern (ADR 0042 core): the value flows from a portal form straight to an Infisical staging path via an injected transport, never touching D1, logs, or transcripts; until the transport credentials are wired the surface returns an honest `not_enabled` and the key is collected during the Captain-run go-live step instead.
3. **Concierge provisioning behind a self-serve storefront.** Checkout is fully self-serve (Stripe Checkout, subscription mode). Provisioning stays Captain-run: the checkout webhook creates the entity, the `subscriptions` row (`status='provisioning'`), the principal `product_roles` grant, and a `hosted_agent_intake` work-item row, then notifies team@smd.services. The Captain authors `customer.yaml` from `operator/customers/_hosted-template/`, stages the key, runs `operator/bin/reprovision.sh <slug>`, and activates. Every manual step is a named seam that automation replaces later without schema change.
4. **Channel constraint (launch):** Telegram plus allowlisted-sender email only. **No public inbound address**, `inbound_allow_from` required non-empty, `external_send: draft_for_review`. This maps one-to-one onto ADR 0032's deferred public-exposure checklist (outbound send-caps, DMARC gate, durable idempotency) — the constraint IS the mitigation, and it is sold on the page as the safety feature it is. Offering a public agent address requires building that deferred list first.

   > **Superseded by [ADR 0071](./0071-confirm-ceiling-and-hosted-agent-tier-ladder.md) (2026-07-07) for the external-send posture only.** The template default is now `external_send: confirm`, not `draft_for_review`: the agent completes the send after an explicit in-turn owner approval over channel (the approval gate is the mitigation), with the taint-gate still blocking a confirmed send on a tainted turn. The rest of this decision stands — no public inbound address, `inbound_allow_from` required non-empty, and the ADR 0032 deferred-list gate on a public agent address are all unchanged.

5. **Named day-one jobs.** The page sells concrete jobs the constrained agent completes autonomously end-to-end because they are outbound-to-owner only: a morning inbox digest, scheduled research briefs, cron-driven monitoring reports. "General assistant that drafts things" is not the pitch.

### Commercial identity

6. **New `product_slug='hosted-agent'`**, not `'operator'`. The Operator portal surfaces assume an SMD-authored engagement (authority switches, compliance views, change requests) and its pricing posture is deliberately unpublished (ADR 0063); this SKU's pricing is deliberately published. Same runtime substrate, separate commercial identity. Portal surfaces live under `/portal/products/hosted-agent/*`.
7. **Checkout is the provisioning grant for this SKU.** The `subscriptions` table doctrine ("provisioning owns row creation, billing never inserts") holds for the Operator; for Hosted Agent the checkout webhook IS provisioning's front half and inserts the row with `status='provisioning'` and the Stripe subscription id attached. The existing subscription-lifecycle mirror (`handleSubscriptionLifecycle`) then covers pause/cancel with no new code. A guard added with this ADR keeps billing transitions from ever promoting a `provisioning` row to `active` — activation is a Captain action.
8. **Dunning and offboarding inherit ADR 0065**: payment failure alerts team@ and waits for the Captain; no webhook ever touches a Machine.

### Positioning reconciliation

9. **ADR 0037 Tenet 1 carve-out.** The Operator competes with a hire and prices against a salary — unchanged. Hosted Agent deliberately competes with DIY and software, priced against the commodity floor's headroom. It is the entry rung of a coherent ladder (hosted self-serve → managed Operator), a funnel and a revenue line, not a repositioning of the flagship.
10. **The consulting funnel is untouched.** The assessment remains the firm's single primary front door; `/agent` is a product storefront, not a second consulting door. The spine's voice laws (no em dashes, no disparagement, anti-fabrication, firm "we" voice) apply to the new page in full; only the published-price and signup locks are exempted, for this page only, recorded in the spine decision log and enforced by a deliberate, commented exemption in `tests/landing-page.test.ts`.

## Concierge seams (manual now → automation later)

| Seam                           | Today (Captain)                                                        | Automation trigger                        |
| ------------------------------ | ---------------------------------------------------------------------- | ----------------------------------------- |
| customer.yaml authoring        | From `_hosted-template` + intake row                                   | Guided generator from intake fields       |
| Anthropic key staging          | Infisical staging path (or go-live collection while transport unwired) | Portal write-only relay (transport creds) |
| AgentMail inbox + Telegram bot | Dashboard / BotFather per runbook                                      | API automation / shared-bot design        |
| Provision + smoke test         | `reprovision.sh <slug>`                                                | Queue consumer on intake completion       |
| Activation                     | Admin queue action flips `active` + live email                         | Auto-activate on green smoke test         |

## Consequences

- Revenue can arrive before automation exists; every sale costs Captain minutes, bounded by selling in small waves.
- The COGS/MRR kill gate (ADR 0062) gains a second SKU: seat cost is a Fly Machine (~shared-cpu-1x/1024 + 10GB volume) plus support minutes; LLM spend is the customer's by design.
- The security posture marketed is the shipped one (invariants, isolation, audit, fail-closed entitlements). Outbound send-caps and DMARC remain honestly unbuilt and unmarketed; the channel constraint stands in for them at launch.

## Verification

- Guard tests: the `/agent` page is deliberately exempted from the no-dollar scan with a citation comment; all other content guards (forbidden strings, em dash, voice) apply and pass.
- Webhook pipeline: idempotent on `stripe_checkout_orders` (double-fire produces one subscription row); coupon-exhausted checkout falls back to full price; billing lifecycle events never promote `provisioning`.
- Key custody: the anthropic-key endpoint returns `not_enabled` until the Infisical transport is configured and never echoes the value in any response, log, or D1 row.
- Live dry-run before announcing: real checkout → provision → live agent → refund/cancel → portal degrades honestly.
