# ADR 0075: Routine-grid enforcement is compositional — typed outbound roster, client/vendor send classes, commitment contract tests

- **Status:** Accepted (2026-07-13)
- **Decider:** Captain (plan approved this date)
- **Builds on:** ADR 0072 (recipient-aware proactive send), ADR 0073 (external-send floor removed), ADR 0071 (`confirm` ceiling), ADR 0031 (content-sensitivity floor), ADR 0035 (no imposed defaults, fail-closed)
- **Client commitment implemented:** `operator/customers/ashton-price/correspondence/07_2026-07-09_scott-to-christa_responses-and-routine-matrix.md` (in the private `venturecrane/engagements` repo)

## Context

The 2026-07-09 letter to Ashton & Price commits to a per-routine three-tier dial (auto-handle / prepare-and-route / flag-only) across 19 litigation routines, with permanent caps: nothing touching a deadline or money auto-handles; sends to opposing counsel or the court always take a person's send and never graduate; court-bound work product stays prepare-and-route; and two graduatable outside sends (the client-verification chase to the firm's own client, the records chase to the firm's records vendor) that the firm may raise to auto-handle with a one-line change.

The natural reading — a per-routine ceiling table the trust gate consults — is **unenforceable on this substrate**. The gate's only blocking hook (`pre_tool_call`) receives `tool_name, args, task_id, session_id, tool_call_id`; no Hermes hook carries an active-skill identifier, skills are prompt context rather than sandboxed execution units, and an agent-supplied skill name cannot be trusted as an entitlement input (the same forgery concern that strips `_current_turn_approval` from tool args). A prior per-skill `trust_ceiling` scalar was retired precisely because nothing enforced it. We do not modify Hermes core (ADR 0015).

## Decision

**The routine grid compiles to dimensions the gate already enforces trustworthily.** The grid remains the firm-facing organizing surface (verbatim in `operator/customers/<slug>/routine-grid.yaml`); each row's tier and caps are realized by composing:

| Grid tier / cap                                 | Enforcement composition                                                                                                                                              |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| flag-only                                       | skill contract: reads + internal writes only, no draft/send tool (authored discipline within the persona, verified by audit-journal observation — see Honesty below) |
| prepare-and-route (work product)                | person-invoked `initiation` + `internal_write`                                                                                                                       |
| prepare-and-route (outside send)                | recipient-class ceiling `draft_for_review`                                                                                                                           |
| auto-handle graduation (client / vendor)        | `external_send_client` / `external_send_vendor` ceiling flipped to `autonomous` (one authored line)                                                                  |
| money / deadline never auto                     | content-sensitivity floor (ADR 0031, extended to the typed classes) + banned `payments_*` / trust-write tools + read-not-compute skill contracts                     |
| opposing counsel / court always a person's send | structural: no roster class exists for them (see below) + the commitments contract pins `external_send` below autonomous                                             |
| no e-filing                                     | no e-file tool is mapped; unmapped tools are refused fail-closed                                                                                                     |

**The recipient axis is enriched** (overlay #156 / ss #1856, following ADR 0072's `external_send_internal` precedent exactly):

- `RecipientClass` gains `CLIENT` and `VENDOR`; `ActionClass` gains `EXTERNAL_SEND_CLIENT` / `EXTERNAL_SEND_VENDOR`, each with its own authored ceiling in `personas[].entitlements.exposure`.
- A new human-authored `scope.outbound_roster` maps exact addresses or `@domain` grants to a **closed class vocabulary: `client` | `records_vendor`**. Validators (both repos) reject duplicates across roster classes and against `inbound_allow_from`, and reject whole-`@domain` grants at public-mail providers while allowing exact consumer addresses (a PI client on gmail is rosterable; all of gmail is not).
- Mixed-recipient sends resolve by a homogeneity rule: any UNKNOWN wins, then any OUTSIDE; internal CCs ride along under the counterparty class; a client+vendor mix resolves to OUTSIDE (draft). No ceiling shopping.
- The taint gate, content-sensitivity floor, and voice live-gate all cover the new classes. Only `external_send_internal` remains floor-exempt (deliberate, ADR 0072).
- **Voice-gate decision:** an autonomous client- or vendor-bound send requires the voice transform when a voice binding exists, exactly like an outside send. pilot-smokeball has no voice binding (gate silent there); the A&P go-live checklist carries a voice-binding parity probe so graduation cannot silently degrade to drafts at production.

**Permanence is structural at the class level, procedural at the identity level — stated honestly.** Opposing counsel and the court are deliberately NOT roster classes; an autonomous outside send requires rostering, and the validator vocabulary offers no class that could ever roster them, so "never graduates" has no configuration path. What remains procedural: nothing mechanical distinguishes an opposing-counsel firm domain from a client business domain, so mis-rostering is prevented by review, not code. The per-customer **commitments contract test** (`operator/customers/<slug>/commitments.json` + `tests/customer-commitments.test.ts`) makes that review mechanical-adjacent: it pins the exact roster entries (any roster change fails CI without a same-PR commitments bump), pins `external_send` below autonomous on A&P-lineage seats, pins grid-to-config value traceability, and rejects any `PLACEHOLDER` marker under `operator/customers/ashton-price/` so the client's unanswered numbers (verification escalation count, treatment-gap days) are a hard go-live gate.

**No vertical floor returns.** ADR 0073 stands: outside-send is the firm's authored dial. The cap on this engagement is the firm's own authored posture plus the commitments contract, not a vertical-wide pin.

## Honesty boundaries (what this does NOT claim)

1. **flag-only is not a gate.** Within one persona, a flag-only routine's session has the same tool surface as any other; separation is authored skill discipline verified by detective controls (audit-journal tripwire: zero draft/send tool calls in those runs). If drift is ever observed, the named hardening path is persona-partitioned tiers (flag-only crons under a persona whose exposure authors no send classes — persona is a native Hermes profile, ADR 0011, resolved from trusted env).
2. **Graduation covers proactive sends only.** The inbound reply relay (ADR 0055) auto-relays a rostered sender's in-thread reply only to `inbound_allow_from`. Proactive graduated delivery to a rostered client/vendor is a separate path: the chase skill calls `mcp_agentmail_send_message`, and the trust gate governs it by the recipient-class ceiling (`external_send_client` / `external_send_vendor`) — the same single enforcement point, not a second relay (see the #1868 amendment below for the relay detour that was built and removed). Clients are never dumped into `inbound_allow_from`.
3. **Taint starvation is designed around, not wished away.** Fenced reads (message bodies, document text) taint the session and forfeit that turn's autonomous send. Chase skills pin their state checks to matter-metadata reads (signature detection via `get_files_on_matter`), recorded per grid row.
4. **Tool-level narrowings of the letter are recorded in the grid rows**, not hidden: `create_matter` stays commitment-gated even at auto-handle; the Smokeball e-sign leg of client verification is human-sent (graduation covers the chase emails).

## ADR 0073 proof supersession

The `*/17` proof cadence (ss #1854) awaited an in-gateway autonomous **outside** send. Under the grid posture the commitments contract forbids authoring `external_send: autonomous` on this seat, so that exact proof is unprovable here by design. The mechanism it targeted — an authored autonomous send actually SENDS through the gateway's clean-trust scheduled path — is proven instead through `external_send_client` / `external_send_vendor`, which run the identical ceiling semantics in the identical enforcement path (probes 3, 6, and 15 of the verification ladder). The proof cron is removed with this ADR; the production weekly slot stands.

## Amendment 2026-07-14 — #1868: the delivery mechanism is `send_message` + the gate (a relay detour, built and removed)

**What we shipped in the end:** graduated chase delivery runs through the model calling `mcp_agentmail_send_message`, which the trust gate classifies to the recipient class (`external_send_client` / `external_send_vendor`), re-applies the content + voice floors to, and then **holds at `draft_for_review` or sends at `autonomous`**. That is the whole mechanism, and it is exactly what the chase skills specify ("whether it is sent or drafted follows the ceiling"). The only skill-side requirement is that a chase to a rostered recipient **pin `send_message`** (never `create_draft`, which is an `internal_write` that nothing delivers). `client-verification-tracker` already carried that pin (ss #1855); ss #1868 added it to `medical-records-chaser` — the two graduatable outside chases the 07-09 letter names.

**The detour (recorded honestly).** #1868 first mis-diagnosed the gap. An audit snapshot showed `send_message` at zero across three weeks and concluded the model had an unbreakable **drafting prior**, so we built a **proactive outbound relay** (overlay #162) to deliver the model's `create_draft` out-of-band — a second send path mirroring the inbound reply relay. It was wrong on the facts and wrong on the design:

- The snapshot was **stale** — it predated `client-verification-tracker` ever running its scheduled slot. Live on the seat (2026-07-14) the skill ran and **did** call `send_message` autonomously; the gate delivered it. There is no unbreakable drafting prior on the pinned chases.
- The relay only fired on `create_draft` → a rostered client/vendor, a path the authored chases do not take. It **duplicated the gate's floors** in a second place, added a send surface **outside** the single enforcement point, and escalated an `internal_write` draft into an external send. It did nothing the gate did not already do.

**Resolution (Captain decision 2026-07-14): rip the relay out** (overlay #164 reverts #162 — `proactive_disposition`, `_try_proactive_relay`, `send_draft`, `parse_created_draft`, the `PROACTIVE_*` verbs, and the relay-only `CustomerConfig.persona_exposure` accessor are all gone; `hermes-smd-reply` is inbound-reply-only again) and close the real gap with the one-line `send_message` pin at the skill layer. No debt code, single enforcement path. The load-bearing enrichment (`external_send_client` / `external_send_vendor` classes + `outbound_roster`, overlay #156) stays — that is what the gate uses to deliver.

**What actually blocks the flagship client-verification chase (the real go-live items, neither touched by the relay):**

1. **The e-sign send is unwired.** The initial verification goes out through the firm's Smokeball e-sign, and there is **no confirmed e-sign send tool in the connector surface** — today it is surfaced for a human to send. The Operator's autonomous part is the follow-up chase, not the initial send.
2. **Approval authentication is deferred.** The signer-bound send is released only by the responsible attorney's **authenticated** approval; the deterministic DMARC gate is a known deferred substrate item, so an inbound "approve" reply is never trusted on its own. The client chase does not release autonomously until that lands.

**Risk 4 (still stands, #1878).** A verification chase body that says "please **sign and return** the **verification**" trips the content floor's `contract` category and is **held** — on the **gate** send path too (the gate re-applies the content floor to the typed send classes), so this is independent of the removed relay. Fix at the template layer: author chase bodies floor-clean, never loosen the floor.

## Amendment 2026-09-17 — the agreement supersedes the letter as the grid's source, and the mapping is pinned too

Three changes, all from one finding: the firm's portal described a form of routine 11 the agreement had retired the day before, and routine 5's §2.8 review threshold had been stale on the portal since the 08-28 amendment. The grid was pinned to the July 9 letter, so an amendment to the paper reached no check.

1. **Source of record.** A grid may declare `source_agreement` (a path inside the private engagements repo). Where it does, that agreement's Exhibit A / Schedule A-1 is the definition of record and `source_letter` becomes history. Every row's `routine`, `start_verbatim` and `ceiling_verbatim` equals the agreement row verbatim, in the agreement's order.

2. **The gate lives in the engagements repo.** ss-console is public and engagements is private, so this repo's CI cannot read the agreement, while engagements CI can read this repo with no credential. `tests/routine-grid-parity.test.ts` there reads ss-console at a SHA recorded in engagements and compares the rows. Divergence during an open amendment is declared in `agreements/pending-amendments.yaml` rather than tolerated, so the gate stays meaningful while paper is in flight instead of going red for its duration.

3. **The normalization is pinned, not only the quote.** `start_tier` is what the client's page renders as the routine's level, and mapping the agreement's prose onto the closed tier vocabulary is a judgment (the chronology's "On request ..." to auto-handle; the lien ledger's dual start to its lower bound). A row whose `start_verbatim` is not a plain tier name now requires a `start_tier_note` quoting the phrase the mapping rests on, and the parser refuses the row without it. The parser also refuses a `start_tier` above its own `ceiling_tier`, which was previously unchecked.

**Two portal defects fixed with it, both in the level reader (`src/lib/operator/entitlement-compiler.ts`).** A routine with no send action class resolved to flag-only, so five prepare-and-route work-product routines and the internal-record chronology read one or two levels below the agreement on the client's Settings page. The first correction returned the grid's authored tier instead, which is the worse error in the other direction: it would claim "Handles it" from a historical field while the seat held `internal_write: refused`. The resolved level is now the lower of the authored tier and what the live writing ceiling permits, an absent or refused write reads as "not currently authorized", and a level the Machine did not confirm is marked unconfirmed per row rather than asserted. The Duties grid, Settings and the Activity feed now share one client vocabulary (`src/lib/portal/operator/tier-language.ts`) instead of three copies.

Lifecycle for all of it, including retirement: `docs/runbooks/operator/routine-lifecycle.md`.

## Consequences

- A&P go-live inherits `routine-grid.yaml` + `commitments.json` + the probe ladder; Christa's two numbers and the grid-delta review swap the placeholders (config-only).
- Follow-ons filed rather than silently absorbed: graduated chase delivery is `send_message` + the gate (the reply-relay extension was built as overlay #162 then reverted as #164 — see the #1868 amendment; the real client-verification blockers are the unwired e-sign send and deferred approval auth); zero-delta wake suppression (gated on verifying Smokeball's `updated_since` wire format at connect, per `pre_run_gate.py`); the smokeball-surface.md dependency-map row for the deadline skill's new confirm-gated memo write; **chase-template floor-clean authoring pass (Risk 4, #1878)**.
- Every future vertical inherits the pattern: grid as client surface, composition as enforcement, commitments contract as the review gate.
