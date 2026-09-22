# ADR 0071: The `confirm` ceiling and the Hosted Agent tier ladder

**Status:** Accepted 2026-07-07 (Captain decision)

**Amended by [ADR 0089](./0089-staff-send-as-on-approval.md) (2026-09-21):** for sends AS a staff member, the emailed approval is bound to the verified intra-tenant sender, an exact match to the draft's approver, a digest over the stored message, and a forgery guard against the Operator's own Sent Items, rather than the signed single-use token sketched in section 6. The confirm ceiling's other semantics are unchanged.

**Amends:** [ADR 0025](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) (adds a fourth trust-ceiling value and extends `current_turn_approval` enforcement to `EXTERNAL_SEND` under it). **Amends:** [ADR 0067](./0067-hosted-agent-self-serve-sku.md) (replaces the launch draft-only external-send posture with a confirm-on-send default). **Leans on:** [ADR 0035](./0035-no-imposed-entitlement-defaults.md) (`confirm` is an authored value, never a fallback; unauthored entitled classes stay fail-closed), [ADR 0026](./0026-config-surface-is-a-security-boundary.md) (raising a ceiling is a control-plane act), [ADR 0012](./0012-customer-yaml-storage.md) (customer.yaml is the config source of truth).

## Context

The demand-first research pass ([`docs/research/hosted-agent/target-customer-and-demand.md`](../research/hosted-agent/target-customer-and-demand.md)) found that the market defines a personal agent by what it **does**, not what it drafts, and that draft-only as a product ceiling is off-market. The product-model analysis ([`docs/design/hosted-agent/tiered-autonomy-product-model.md`](../design/hosted-agent/tiered-autonomy-product-model.md)) found that our substrate (`operator/adapter/trust_ceiling.py`) already enforces autonomy **per action class** calibrated to reversibility, so draft-only was an authoring choice in the Hosted Agent template, not a substrate limit.

The one genuine gap: `EXTERNAL_SEND` can only be authored `autonomous` (agent sends unprompted), `draft_for_review` (agent drafts, **the human** performs the send), or `refused`. There is no **confirm-then-the-agent-executes** posture — the market's dominant safe-action pattern (Lindy's "Ask for Confirmation"), where the agent prepares the send, asks the owner a lightweight yes/no, and on approval **the agent itself** completes the send. That gap is the difference between a drafting tool and an agent that acts under supervision.

## Decision

**1. Add a fourth ceiling value, `confirm`.** Semantics: _the agent may perform the action after explicit per-action approval in the current turn._ It reuses the existing `current_turn_approval` mechanism that already gates `COMMITMENT` and `DESTRUCTIVE`, extended to `EXTERNAL_SEND`. Restrictiveness ordering (least → most restrictive): `autonomous` < `confirm` < `draft_for_review` < `refused`. This ordering is deliberate: `draft_for_review` is **more** restrictive than `confirm` because the agent never performs the send at all under draft, whereas under confirm it performs the send after approval. The `_most_restrictive` combine therefore lets a vertical floor of `draft_for_review` narrow an authored `confirm` (never the reverse), preserving ADR 0025 floor semantics.

**2. Enforcement.** Under a `confirm` ceiling, an `EXTERNAL_SEND` executes only when `current_turn_approval` is true for that specific action in that invocation (prior-turn/prior-session approvals remain invalid, safety invariant #1). Without approval, the action does not execute and the agent surfaces the pending send for approval. The **taint-gate is unchanged and still dominates**: a turn that ingested untrusted inbound content cannot reach the confirmation for `EXTERNAL_SEND` / `DESTRUCTIVE` / `COMMITMENT` / `CODE_EXECUTION` — read and draft only. `confirm` is scoped to `EXTERNAL_SEND` at this ADR; `COMMITMENT`/`DESTRUCTIVE` keep their stricter mandatory-approval floor, and `confirm` on other classes is out of scope until a use demands it. This amends the ADR 0025 note that external-send autonomy is governed solely by the configured ceiling and never by in-turn approval: under the new `confirm` value, in-turn approval **is** the enforcement mechanism for that ceiling.

**3. The two enforcement cores stay in lockstep.** The in-tree `trust_ceiling.py` and the overlay's live `pre_tool_call` gate must agree on the new value and its enforcement (the taint-gate comment already notes the two cores must match). The overlay change ships alongside and `OVERLAY_REF` is bumped.

**4. Hosted Agent default tier ladder.** Re-author `operator/customers/_hosted-template/customer.yaml` from draft-only to:

| Action class     | Hosted Agent default                                                |
| ---------------- | ------------------------------------------------------------------- |
| `READ`           | `autonomous` (scope-bound)                                          |
| `INTERNAL_WRITE` | `autonomous`                                                        |
| `EXTERNAL_SEND`  | **`confirm`** (laddering to `autonomous` per skill as trust builds) |
| `COMMITMENT`     | mandatory current-turn approval (never autonomous)                  |
| `DESTRUCTIVE`    | mandatory current-turn approval                                     |
| `CODE_EXECUTION` | unauthored → `refused` (off by default)                             |

Confirm-on-send is the default rather than draft-for-review because defaulting to draft is the off-market draft-only posture, and the confirmation gate means nothing leaves without an explicit yes (and the taint-gate blocks even a confirmed send on a tainted turn). This makes the out-of-box Hosted Agent an actor, safely.

**5. Graduated trust needs a control surface.** The owner can promote a skill's `EXTERNAL_SEND` ceiling from `confirm` to `autonomous` once they trust it. Raising a ceiling is a control-plane act (ADR 0026) against the customer.yaml source of truth (ADR 0012); a portal control surface for this promotion is in the implementation scope, not a later discovery.

**6. Confirmation UX rides existing channels.** For the Hosted Agent (Telegram + email) the agent sends an approval prompt over the same channel and the owner replies to approve, then the agent completes the send. The pause/approve/resume primitive and the per-channel approval capture (Telegram inline action vs. email reply-with-signed-token) are the substance of the UX work.

**7. Applies to the Operator too, unchanged in philosophy.** `confirm` becomes an available authored value fleet-wide. The Operator continues to author per action class per engagement (ADR 0035); regulated verticals may floor `EXTERNAL_SEND` at `draft_for_review` or `confirm` as the pack requires. This ADR does not change any live Operator seat.

## Consequences

- The Hosted Agent becomes an agent that **acts** (sends, with per-send confirmation), which the research identifies as the capability the market actually pays for, without loosening any irreversible-action invariant.
- Positioning shifts from "drafts, you send" to "acts safely": _the agent that actually does things, and cannot go rogue._
- ADR 0025's external-send/in-turn-approval note is amended (see decision 2); the floor semantics and every irreversible-class invariant are preserved.
- New build surface: the ceiling value + enforcement (both cores), schema/validator, the re-authored template, the confirm-over-channel UX, and the portal promotion surface. Tracked as implementation issues.
- No live seat changes until the implementation lands and is verified.

## Verification

- Implementation is tracked in discrete issues; each carries its own "done means wired" gate.
- Definition of done for the capability: a live `confirm` round-trip on `pilot-smokeball` — the agent prepares an external send, requests approval over channel, the owner approves, the agent completes the send, and the audit log records the confirm decision — proven before `confirm` is authored on any paid seat (standing rule: fixtures → pilot-smokeball → paid seat, never direct). Recorded in the verify ledger.
