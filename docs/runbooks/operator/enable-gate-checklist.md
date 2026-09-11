# Enable-gate checklist: the per-routine promotion instrument

**Status:** active runbook. **Owner:** Operator platform.
**Sources:** the 2026-07-02 security re-audit (issue [#1634](https://github.com/venturecrane/ss-console/issues/1634)) and the 2026-06-15 audit for the platform preconditions; issue [#2391](https://github.com/venturecrane/ss-console/issues/2391) (hardening epic [#2392](https://github.com/venturecrane/ss-console/issues/2392), Track 6) for the ladder and the demotion rule.

## What this file is

Routines used to graduate informally: crons switched off at go-live, `draft_for_review` set by hand, autonomy widened when someone felt ready. This file is the instrument that replaces the feeling. **One routine, one ladder, three rungs, one named piece of recorded evidence per rung, and a demotion rule that was decided before the incident rather than after it.**

Two things are being gated, and they are different:

1. **Per-routine exposure** (this file's ladder). A routine climbs from rehearsed, to drafting on the client seat under review, to acting on its own. Every routine climbs separately. Nothing climbs by default and nothing climbs by age.
2. **Platform preconditions** (Gates A, B, C at the bottom). Defense-in-depth items that are dormant while every routine drafts, and become **required for the whole seat** the moment any routine reaches Rung 3 with an external send, or a persona is exposed to `code_execution`. A single routine's readiness does not open these; they are seat-wide.

The point is honesty in both directions. A routine that has not produced its evidence is not held back by caution, it is held back by a missing artifact anyone can look for. And a shelved platform item is legitimately shelved _because_ it is gated here, not because it was forgotten.

---

## The exposure ladder

The rungs map onto the exposure vocabulary the seat actually enforces (`refused` / `draft_for_review` / `confirm` / `autonomous`, resolved per action class in `src/lib/operator/entitlement-compiler.ts:133`) and onto the routine grid's tier vocabulary (`flag-only` / `prepare-and-route` / `auto-handle`, `operator/customers/ashton-price/routine-grid.yaml`). The ladder does not add a new setting. It states what evidence has to exist before an existing setting is changed.

| Rung                         | Live posture                                                                      | Evidence slot (the artifact that must exist)                                                                                                                                | Who records it                               |
| ---------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **1. Rehearsed**             | Not on the client seat. Runs against a staging or probe seat only.                | **Shadow-firm run id** for a green run of this routine's scenario, on the candidate overlay ref.                                                                            | Shadow-firm runner (#2389)                   |
| **2. Drafting under review** | `draft_for_review` on the client seat for every action class the routine touches. | **Dated review-period observations**: one line per drafted output actually reviewed, with the date, the reviewer, and whether it was sent as drafted, edited, or discarded. | The reviewing human, in the promotion record |
| **3. Acting on its own**     | `autonomous` (or `confirm`) for the named action class.                           | **Captain sign-off**: a dated line naming the routine, the action class, the rung-2 observation window it rests on, and the Captain as the person accepting the exposure.   | Captain                                      |

Rules that hold across all three rungs:

- **No rung is skipped.** A routine at Rung 1 cannot be set autonomous because the drafts looked good in staging. Staging is Rung 1 by definition.
- **A rung is claimed by its artifact, never by a report.** "We rehearsed it" is not a run id. "It has been drafting for weeks" is not a dated observation list. If the slot is empty, the routine is on the rung below.
- **The rung is per action class, not per routine.** A routine that is autonomous for `internal_write` and drafting for `external_send` is at Rung 3 for the first and Rung 2 for the second, and each carries its own evidence.
- **Rung 3 also requires the seat-wide preconditions** for the posture being opened (Gate A for autonomous external send, Gate B for `code_execution`, Gate C before a second paying customer shares control-plane paths).
- **A ceiling in the routine grid is a permission to climb, never a schedule.** Where `ceiling_tier` is `auto-handle` and the day-one value is `draft_for_review`, the client authorized the climb; the evidence still has to exist first. This is stated in the grid's own header (`operator/customers/ashton-price/routine-grid.yaml`, CEILING vs LIVE).
- **Rung 2 has seat preconditions: the seat must already carry every incident fix the routine class has accrued, proven for THIS firm's data shapes.** A routine pause is not a steady state; routines that are off pending client approval will turn on, and they must turn on into a seat that predates none of their known failure classes. Two named preconditions as of 2026-08-24, both from the degraded-digest incident (`docs/runbooks/operator/incidents/2026-08-24-degraded-digest-and-inert-handoff.md`):
  1. **The seat runs overlay `f16d4920` or later** (pre-run handoff binds live, matter numbers projected in code, empty-register refusal without the removal hatch, degraded-run withhold-and-page). A routine drafting on an older seat can still produce the incident artifact.
  2. **The identifier and fence machinery is proven against the firm's own matter-number format.** Bare-digit numbers (Ashton & Price's `201537`, `4853`; ss#2458) are handled by MEMBERSHIP, never by shape: the pre-run handoff's seeding seam accepts a bare `\d{3,10}` `matterNumber` from the gate's own connector pull, the provenance register scans the body only for numbers seeded via `add_record`, and the matter gate's citation pass searches only the membership's number aliases. No extraction pattern was widened — a bare digit run that was never read still matches nothing. The probe for this slot therefore expects THREE observations on the firm's own data shapes: (a) a seeded bare number mispaired with another matter's date is flagged (and the correctly paired line passes), (b) unseeded bare digit runs (zips, page counts, invoice numbers) produce zero hits — the collision guard, and (c) the stated residual: a FABRICATED bare-digit number that was never read stays invisible to the identifier gate — the gate's claim shrinks to what it measures, and promotion rests on (a)+(b) plus the drafting-review rung, never on a claim of fabrication coverage for bare digits. Evidence slot: a `crane_verify` id per firm.

### Where the run ids come from

Rung 1's evidence is a **shadow-firm run id**. The shadow firm is the standing adversarial rehearsal being built under [#2389](https://github.com/venturecrane/ss-console/issues/2389): a scenario registry under `operator/rehearsal/scenarios/`, a runner that drives scenarios through the probe mailboxes against a staging seat, and a run report scored mechanically from audit rows and gate events. Until that runner emits ids, Rung 1 has no valid evidence slot and **no routine advances past Rung 1 on this instrument**. That is the intended reading, not a gap: the ladder is deliberately unable to certify a rung whose instrument does not exist yet.

Two adjacent tools are not substitutes for a run id, and should not be recorded in the slot:

- `operator/bin/rehearse-card.py` reads the stand-up script to a seat and **deliberately does not grade it**. It produces no pass or fail, so it cannot fill an evidence slot.
- `operator/bin/seat-readiness.py` checks seat-level readiness, not per-routine behaviour. Run it before calling a seat ready; it does not promote a routine.

### The promotion record

One table per seat, kept next to that seat's `routine-grid.yaml`. A routine appears here or it is at Rung 1.

| Routine           | Action class | Rung | Evidence | Dated |
| ----------------- | ------------ | ---- | -------- | ----- |
| _(none recorded)_ |              |      |          |       |

The table is empty today, and that is the accurate state: zero routines have a recorded shadow-firm run id, because the runner does not exist yet (#2389). It is not empty because nobody filled it in.

---

## The demotion rule (pre-committed)

Decided in advance so that the decision is not made by whoever is holding the incident.

> **Any SEV1 pauses the seat and demotes every routine involved in the incident to Rung 1, and the routine stays there until BOTH of the following are true: (a) the root cause has landed as a merged change with its own evidence, and (b) the incident exists as a shadow-firm scenario that is observed to fail against the unfixed state before it passes against the fixed one.**

SEV1 is defined by the ADR 0064 ladder as rendered in the handbook (`docs/handbook/incident-response.md`): the Operator is down past the heartbeat threshold, or it acted outside authorized entitlements.

Mechanically:

1. **Pause first, investigate second.** `operator/bin/pause-customer.sh <slug> --reason "<text>"` writes the `/opt/data/.paused` sentinel; `bootstrap.sh` halts the agent loop while keeping the Machine warm for diagnosis. Resume is `--resume` on the same script.
2. **Demote the involved routines** to Rung 1 in the promotion record, with the incident note's filename as the reason. Demotion is a config change on the seat, not a note: the exposure value moves back to `draft_for_review` or `refused` for the affected action class.
3. **Write the post-incident note** the same day, from the template in `docs/runbooks/operator/incidents/_TEMPLATE.md`.
4. **Root cause lands** as a merged change carrying the evidence its own acceptance criteria demand. A repo-layer fix satisfies (a) only for a repo-layer cause; a runtime cause needs a runtime observation (Law 9, `docs/doctrine/agent-operating-doctrine.md`).
5. **The incident becomes a scenario** in the shadow-firm registry, and the scenario is shown to FAIL against the unfixed state before it is trusted. A scenario that has never failed has measured nothing.
6. **Re-climb from Rung 1.** The routine repeats the ladder in full. There is no restoration to the prior rung, because the prior rung's evidence is exactly what the incident falsified.

Two clarifications that have already cost us once each:

- **A fix that closes the reported symptom does not satisfy (a) if the class survives.** Prefer a structural fix that makes the layer converge on authored state over a one-time sweep (CLAUDE.md, "Gone means gone", rule 3).
- **Report-only is a staging state with an expiry date, never a steady state.** A gate re-enabled in report-only mode does not return a routine to Rung 3; it keeps it at Rung 1 until the gate refuses for real. This is the standing rule adopted with the hardening epic (#2392).

---

## Platform preconditions

These are seat-wide. They are dormant while every routine drafts, and they become **required** the moment a customer's `customer.yaml` authors one of two high-risk postures:

- **`external_send: autonomous`** for any routine at Rung 3, so the Operator sends outbound without a human reviewing the draft.
- **`code_execution` initiation authored**, so a persona is exposed to `execute_code` autonomously (not read-only, not draft-gated).

The Operator is fail-closed by default (ADR 0056): an unconfigured capability is a safety state, not an identity. **Do not merge a `customer.yaml` change that authors either posture until the corresponding items below are green for that customer.** Each item names what green means and where the fix lives.

### Gate A: before `external_send: autonomous` for any customer

#### A1. SEC-05/12: empty-session turns must not be able to autonomously send

- **Risk.** The taint gate refuses autonomous sensitive actions on a turn tainted by untrusted content. Today an **empty `session_id` reads as `TRUST_CLASS_INTERNAL`** (`shared/inbound.py` `trust_class("")`), so an injection-fed turn that arrives with no session id is treated as clean and an autonomous send is not blocked.
- **Why it is dormant.** The pilots draft, so an autonomous send never happens and the fail-open path is unreachable. It also only bites if a real flow can present an empty `session_id`: the MCP route sets `webhook:mcp:<correlation_id>` and inbound reads the gateway-supplied id, so prod may never actually hit empty. **First step is to confirm whether prod can produce an empty `session_id` at all**; if it provably cannot, this item is closed by observation.
- **Green.** Either (a) prove empty `session_id` is unreachable in prod for the send paths, or (b) the taint gate treats an empty or unauthenticated session as tainted for send classes without breaking the authored-autonomous-clean-send path (the naive default-taint broke `test_autonomous_clean_send_is_allowed`; the real fix is a session-independent recovery index, which needs a key to exist at `pre_tool_call`, design open).
- **Where.** `hermes-smd-overlay/shared/inbound.py`, `plugins/hermes-smd-trust/`.

#### A2. EFF-12: memory-poisoning fence

- **Risk.** Recalled memory re-enters future turns; a planted preference could steer a later autonomous send.
- **Current state (2026-07-03 scoping).** Lower-severity than the audit implied. The overlay peer-memory **capture path is already taint-fenced** (ADR 0048 §2f: a preference cannot be written from an injected or tainted turn, only by a trusted roster peer on a clean turn). The injected block _is_ framed as a directive (`store.py render_preference_block`), but the resulting action is still bounded by ADR 0056 entitlements plus `draft_for_review`, so under the pilots' posture the worst case is a biased draft a human reviews. The audit's literal target, native Hermes `MEMORY.md` recall, is upstream and may not be used by the Operator at all (unconfirmed).
- **Green (only needed before autonomous send).** (a) Confirm whether native Hermes memory recall is used; if so, fence or disable it. (b) Reframe injected preferences as untrusted-provenance data the agent weighs, not a directive it obeys.

### Gate B: before `code_execution` initiation is authored for any customer

#### B1. SEC-10: egress allowlist inside `execute_code`

- **Risk.** An autonomous code-executor has unbounded network egress and can exfiltrate to any host.
- **Why it is dormant.** No pilot authors `code_execution` autonomously; the account-wide R2 key is already stripped from the agent (SEC-23), shrinking what is reachable to exfiltrate.
- **Green.** Egress from the code-execution sandbox is bound to an allowlist; a blocked host is denied and audited. (WS6, not built.)
- **Where.** Overlay code-execution path; ADR 0050 B-class.

#### B2. 0045: Smokeball token behind the broker

- **Risk.** For a Smokeball-connected customer, the refresh token is hermes-readable on the Fly volume; a code-executor could read it.
- **Why it is dormant.** Reachable only via `code_execution`, which the Smokeball pilot does not author.
- **Green.** The Smokeball token moves behind the uid-split broker (like the Google DWD creds), so the agent process cannot read it.
- **Where.** `operator/templates/entrypoint.sh` broker stage; `hermes-smd-overlay` connector.

### Gate C: cross-machine binding isolation (before any second paying customer on shared control-plane paths)

#### C1. SEC-22: wire `verify_at_boot`

- **Risk.** A provisioning bug could bind a Machine to another tenant's storage, and nothing at runtime catches it.
- **Status update.** ADR 0009 records this as **wired on 2026-07-13** (`docs/adr/0009-cross-machine-query-prohibition.md:84`): `operator/templates/entrypoint.sh` runs the `invariant_7` `__main__` shim as the last root gate before the `setpriv` drop, exiting `3` on any non-zero result, and a missing or unimportable module is itself a fail-closed refusal rather than a silent skip. The earlier text on this line, which said the check was hardcoded to slug `"smoke"` and validated nothing real, described the state as of 2026-07-03 and is superseded by that entry. **The wiring has not been re-probed by this file's author**; treat the ADR entry as the claim of record and re-probe the running Machine before citing it as live (Law 10).
- **Green.** The boot check runs against the real slug on the deployed Machine, observed, with the fatal path exercised.
- **Where.** `operator/safety-substrate/invariants/invariant_7.py`, `operator/templates/entrypoint.sh`.

#### C2. SEC-31: root-own `run_invariants.py`

- **Depends on C1.** Only meaningful once the invariant check it protects is live.
- **Risk.** The runner is agent-writable, because of `__pycache__`, not a boot log (the Dockerfile comment claiming "boot log" is wrong).
- **Green.** `PYTHONDONTWRITEBYTECODE=1` for the check plus root ownership, so the agent cannot tamper with the isolation check.

### What is NOT on this list (already shipped)

For reference, the audit items that were **not** deferred and are already live: console-sole MCP door (ADR 0057 amendment), fabrication-gate on autonomous `EXTERNAL_SEND` (EFF-01), Unicode-normalized citation scan (EFF-03), `_current_turn_approval` self-approval strip (SEC-36/16), account-wide R2 key strip from the agent (SEC-23), runtime-read seam plus heartbeat key strips from the agent (SEC-28, ADR 0023), and the ADR 0023 heartbeat emitter.

### SEC-21 note

The CI test that asserts `R2_SKILL_BODIES_ACCESS_KEY_ID` and `R2_ACCESS_KEY_ID` hold **different values** is worth adding (cheap, catches a real misconfiguration). The proposed runtime "widening check" is **not**, because the agent cannot write R2, so all config is Captain-authored and a blanket reject would block legitimate Captain changes. Do not build the widening check.

---

## Related

- `docs/handbook/incident-response.md`: the severity ladder, detection surfaces, and the escalation path this instrument's demotion rule keys off.
- `docs/runbooks/operator/incidents/`: the post-incident notes and their template. Every SEV1 that demotes a routine leaves one.
- `docs/doctrine/agent-operating-doctrine.md`: Law 9 (built, wired, and tested are three different claims) is why a rung is claimed by an artifact rather than a report.
