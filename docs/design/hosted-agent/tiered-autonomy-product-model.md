# Hosted Agent — Tiered-Autonomy Product Model (proposal)

_Status: **ACCEPTED 2026-07-07** — all three open decisions approved as recommended; locked in [ADR 0071](../../adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md). Motivated by [`docs/research/hosted-agent/target-customer-and-demand.md`](../../research/hosted-agent/target-customer-and-demand.md) (the market wants action-taking; draft-only-as-ceiling is off-market) and the Captain directive that security is a **how**, not a capability ceiling. This document is the design rationale; ADR 0071 is the decision of record. No live seat changes until the implementation issues land._

## TL;DR

**We already built the tiered-autonomy engine. We just never used anything but its "off" switch.** `operator/hermes-smd-overlay/plugins/hermes-smd-trust/enforce.py` enforces autonomy **per action class**, calibrated to reversibility and blast radius, which is exactly the converged 2026 safe-action model the research found. Draft-only is an **authoring choice** in the Hosted Agent template, not a substrate limit.

The product-model change is therefore mostly **re-authoring**, plus **one genuine substrate addition**: a middle ceiling for external sends — _"the agent executes the send after a per-action confirmation"_ — which is the market's dominant safe-action pattern (Lindy-style) and the one posture our current three-value ceiling cannot express.

## What the substrate already does (grounded in `trust_ceiling.py`)

**Six action classes**, categorized by reversibility / blast radius:

| ActionClass      | Meaning                                                         |
| ---------------- | --------------------------------------------------------------- |
| `READ`           | Always allowed (breadth bounded by authored scope)              |
| `INTERNAL_WRITE` | Notes, drafts, internal state — autonomous OK per skill ceiling |
| `EXTERNAL_SEND`  | Email, SMS, external chat, posts — gated                        |
| `COMMITMENT`     | Sign, accept terms, agree to dates — never autonomous           |
| `DESTRUCTIVE`    | Delete, drop, irreversible — explicit per-call approval         |
| `CODE_EXECUTION` | Arbitrary code / shell / subagent — authored-only, fail-closed  |

**Three ceiling values:** `AUTONOMOUS`, `DRAFT_FOR_REVIEW`, `REFUSED`.

**How enforcement resolves (per action class):** effective ceiling = most-restrictive of {the customer's explicit per-action override, the unauthored resolution, the vertical-pack floor}. Key invariants already live:

- **Fail-closed when unauthored (ADR 0035):** an entitled class with no authored ceiling resolves to `REFUSED`. `DRAFT_FOR_REVIEW` is a value an engagement authors, never a fallback.
- **Per-action autonomy, not one scalar (ADR 0025):** each class carries its own ceiling; a vertical floor can only narrow, never widen; the agent can never raise its own ceiling (control-plane act, ADR 0026).
- **Reversibility floor:** `COMMITMENT` and `DESTRUCTIVE` require `current_turn_approval` — explicit approval in _this_ invocation (prior-turn/session approvals are invalid). This **is** the per-action-confirmation mechanism — but today it exists only for commitment/destructive.
- **Taint-gate (injection defense):** a turn that ingested untrusted inbound content cannot fire `EXTERNAL_SEND` / `DESTRUCTIVE` / `COMMITMENT` / `CODE_EXECUTION` autonomously; read and draft still allowed. This is precisely the ingress→egress prompt-injection tie the research flagged as the core 2026 risk.

**Adjacent controls already present:** spend/rate caps via `safety.sticky_stop` (daily-cents ladder: warn 80% / soft-stop 100% pins to draft / hard-stop 200% refuses; inbound daily wake cap); credential isolation via write-only key custody (ADR 0042, key never in model context); tamper-evident audit log.

## The research tiers map ~1:1 onto our action classes

| Research reversibility tier                                                                 | Our ActionClass             | Already expressible?                      |
| ------------------------------------------------------------------------------------------- | --------------------------- | ----------------------------------------- |
| Tier 1 — read-only, autonomous                                                              | `READ`                      | ✅ (always allowed)                       |
| Tier 2 — reversible (drafts, tags, CRM adds, calendar holds), autonomous + logging          | `INTERNAL_WRITE`            | ✅ (autonomous per skill ceiling + audit) |
| Tier 3 — external / irreversible-to-others (send email, post), confirm / stage / rate-limit | `EXTERNAL_SEND`             | ⚠️ **partial — see the gap**              |
| Tier 4 — money / contracts / access / delete, mandatory approval                            | `COMMITMENT`, `DESTRUCTIVE` | ✅ (`current_turn_approval` required)     |
| (mechanism) arbitrary execution                                                             | `CODE_EXECUTION`            | ✅ (authored-only, fail-closed)           |

## The one real gap: a confirm-on-send tier

For `EXTERNAL_SEND`, the substrate offers only `AUTONOMOUS` (agent sends unprompted), `DRAFT_FOR_REVIEW` (agent drafts, **the human** sends), or `REFUSED`. It deliberately does **not** gate external send on `current_turn_approval` (the code notes external-send autonomy is governed by the configured ceiling, ADR 0025).

But the market's dominant safe-action pattern — Lindy's "Ask for Confirmation," and the general confirmation-on-risky tier — is **confirm-then-the-agent-executes**: the agent prepares the send, pauses, asks the human "send this to X? (approve/deny)", and on approval **the agent completes the send itself**. That is neither `AUTONOMOUS` nor `DRAFT_FOR_REVIEW`.

**Proposal: add a fourth ceiling value** (working name `CONFIRM`) meaning _"the agent may perform the action after explicit per-action approval in this turn."_ It slots between `DRAFT_FOR_REVIEW` and `AUTONOMOUS` on the restrictiveness ordering. Mechanically it reuses the existing `current_turn_approval` plumbing that already gates `COMMITMENT`/`DESTRUCTIVE`, extended to `EXTERNAL_SEND`. The taint-gate still applies (a tainted turn cannot even reach the confirmation). This is the single substrate change the product model needs; everything else is authoring.

Confirmation UX rides existing channels: for the Hosted Agent (Telegram + email), the agent sends an approval prompt over the same channel and the owner replies to approve — the Lindy pattern adapted to our surfaces.

## Proposed Hosted Agent default authoring

Re-author `operator/customers/_hosted-template/customer.yaml` from "draft-only everywhere" to a real tier ladder:

| ActionClass      | Proposed Hosted Agent default                                  | Rationale                                                                                          |
| ---------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `READ`           | `autonomous` (scope-bound)                                     | Tier 1; no reason to gate reads                                                                    |
| `INTERNAL_WRITE` | `autonomous`                                                   | Tier 2 reversible; already the default                                                             |
| `EXTERNAL_SEND`  | **`confirm`** (new), laddering to `autonomous` as trust builds | Tier 3; the agent **acts** (sends) but with per-send approval — this is the unlock from draft-only |
| `COMMITMENT`     | mandatory current-turn approval (never autonomous)             | Tier 4; unchanged invariant                                                                        |
| `DESTRUCTIVE`    | mandatory current-turn approval                                | Tier 4; unchanged invariant                                                                        |
| `CODE_EXECUTION` | unauthored → `refused` (off by default)                        | Fail-closed; opt-in only if a buyer's use needs it                                                 |

**Graduated trust** = the owner (or SMD, on request) raises `EXTERNAL_SEND` from `confirm` to `autonomous` per skill once they trust it — a control-plane re-authoring (ADR 0026), which fits the managed model (we watch, we raise). Automatic/learned graduation is future design space, not this proposal.

## What's covered vs. what's still design space

**Covered by the substrate today:** reversibility-tiered autonomy, mandatory approval on irreversible classes, the injection taint-gate, spend/rate caps, credential isolation, audit. These are the load-bearing "confident" pieces the research named — we have them.

**To build (this proposal):** the `CONFIRM` ceiling for `EXTERNAL_SEND` (the core change) + the confirm-over-channel UX + the re-authored template.

**Future design space (named, not proposed here):** staging queue / dry-run preview for outbound; confidence-based escalation (agent flags uncertainty rather than always proceeding); time-boxed approval lanes; automatic trust graduation. All of these layer onto the same approval plumbing.

## Application to both SKUs

- **Hosted Agent:** ship the tier ladder above. This turns it from "drafts, you send" into "**acts, with per-send confirmation**" — the capability the market actually pays for — without loosening any irreversible-action invariant. Positioning follows: _the agent that actually does things, and can't go rogue._
- **Operator:** philosophy unchanged. It already authors per action class per engagement (ADR 0035); regulated verticals may floor `EXTERNAL_SEND` at `draft_for_review` or `confirm`. This proposal does not change Operator authoring; it makes the Hosted Agent finally use the same engine at a sensible default.

## Decisions (resolved 2026-07-07 — see ADR 0071)

1. **Add the `CONFIRM` ceiling value** — ✅ approved. Reuses `current_turn_approval` extended to `EXTERNAL_SEND`; restrictiveness ordering `autonomous` < `confirm` < `draft_for_review` < `refused`.
2. **Hosted Agent default authoring** — ✅ approved. Confirm-on-send default, laddering to autonomous per skill.
3. **Naming** — ✅ `confirm`.
4. **(Added to scope)** owner can promote `confirm` → `autonomous` per skill from the portal (graduated trust; control-plane act per ADR 0026).

Locked in [ADR 0071](../../adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md). Implementation tracked in issues (substrate ceiling value + enforcement in both cores; schema/validator; template re-author; confirm-over-channel UX; portal promotion surface). No live seat changes until they land and are verified on `pilot-smokeball`.
