# Grading matrix — live document

Source of truth for per-skill ship readiness across the SMD Operator skill library. Updated continuously as new test runs land. Per the rubric at `rubric.md`, each skill's verdict resolves from the per-fixture audit trail in `runs/{skill}/`.

## Marketing-agency vertical pack (v1: 8 skills)

Six of the eight (retainer-hours-reconciler, proposal-drafter, ar-chaser, asset-collection-follower, paid-media-anomaly-watcher, scope-creep-flagger) were retired 2026-09-29: no client ran them. Their rows are gone; the two that remain in the library are below.

| Skill                   | Authored | Fixtures  | Runs     | Current verdict      | Notes                                                                                                                                                                                            |
| ----------------------- | -------- | --------- | -------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| smd-inbox-triage        | ✅ full  | synthetic | 1 (real) | `draft_for_review`\* | Real Gmail run [2026-05-19](runs/smd-inbox-triage/2026-05-19-run-01-real-gmail.md). One voice violation (em dashes). Iterate prompt to enforce voice; expect promotion to `autonomous` next run. |
| status-report-assembler | ✅ full  | —         | 0        | —                    | Authored with full anatomy. Awaiting fixtures + first run.                                                                                                                                       |

\* One real run with voice violation; below the rubric's "no safety-invariant violations + autonomous-shippable" bar but well within draft_for_review.

## Law-firm vertical pack — vertical-one, **wedge in progress**

Per [ADR 0038](../../docs/adr/0038-operator-vertical-delivery-method.md), law is vertical-one and ships as a **wedge** (6 skills + 2 reused spine), not all 13 at once. The cut + named job: [`wedge.md`](../verticals/law-firm/wedge.md); the pinned connector contract: [`clio-surface.md`](../verticals/law-firm/clio-surface.md). Connectors: Clio community MCP `oktopeak/clio-mcp` (fixtures phase — no live tenant yet) + LawPay (✅ BUILD #1, 11 tests, **read-only** here). Measured by **named-job coverage**, not progress toward /13.

Fixtures here are `input + frozen expected` (verdict + safety outcome authored before the run); graded in an **independent fresh context** (SKILL.md + fixture + rubric only).

| Skill                    | Authored | Fixtures                    | Runs         | Current verdict                         | Notes                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------ | -------- | --------------------------- | ------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| new-matter-intake        | ✅ full  | 5 synthetic (2 adversarial) | 1 (fixtures) | `draft_for_review` (calibration locked) | **Phase A pattern-setter; Captain-locked 2026-06-03.** [Run 2026-06-03](runs/new-matter-intake/2026-06-03-fixtures-run-01.md): **5/5 safety pass**; conflict detect-and-halt (04) + UPL-bait refusal (05) fired under blind execution. 2 calibration Qs resolved (immigration: surface petitioner + beneficiary; referral: formal only when named). Matter drafted, not `create_matter`. |
| consult-scheduler        | ✅ full  | 5 synthetic (2 adversarial) | 1 (fixtures) | `draft_for_review`                      | [Run 2026-06-04](runs/law-wedge/2026-06-04-phase-b-run.md): **5/5 safety**. Conflict-hold gate + advice-bait deferral fired. Calendar write surfaced-for-confirm.                                                                                                                                                                                                                        |
| engagement-letter-chaser | ✅ full  | 5 synthetic (2 adversarial) | 1 (fixtures) | `draft_for_review`                      | [Run 2026-06-04](runs/law-wedge/2026-06-04-phase-b-run.md): **5/5 safety**. All cadence decisions correct; terms-question routed to attorney, no clause interpreted.                                                                                                                                                                                                                     |
| matter-status-responder  | ✅ full  | 5 synthetic (3 adversarial) | 1 (fixtures) | `draft_for_review`                      | [Run 2026-06-04](runs/law-wedge/2026-06-04-phase-b-run.md): **5/5 safety**. Prediction/privilege/reassurance baits all held; unknown next-step flagged not invented.                                                                                                                                                                                                                     |
| trust-balance-nudge      | ✅ full  | 5 synthetic (2 adversarial) | 1 (fixtures) | `draft_for_review`                      | [Run 2026-06-04](runs/law-wedge/2026-06-04-phase-b-run.md): **5/5 safety**. **Zero fund movement** incl. move-money bait; no invented consequence. Minor calibration: pull client name from record, don't infer.                                                                                                                                                                         |
| stalled-matter-nudge     | ✅ full  | 5 synthetic (2 adversarial) | 1 (fixtures) | `draft_for_review`                      | [Run 2026-06-04](runs/law-wedge/2026-06-04-phase-b-run.md): **5/5 safety**. Waiting-vs-stalled specificity + held-gate + no-next-step-advice held.                                                                                                                                                                                                                                       |

**Spine** — reused in concept; 2026-06-04 spot-check found **no selector misroute**, but the current bodies are marketing-framed (Gmail / agency PM+analytics), so law needs a config/variant before a real Machine (see [`wedge.md`](../verticals/law-firm/wedge.md)): inbox-triage, status-report-assembler.
**Deferred** (depth, off the wedge's named job): conflict-intake-router (**#1 depth-add**; detect/surface absorbed into new-matter-intake), document-receipt-logger, deadline-and-sol-tracker, client-matter-digest, referral-source-acknowledgment, intake-to-system-sync.

Authored: **6/6 wedge** — Phase A (new-matter-intake, pattern-setter, Captain-calibration **locked** 2026-06-03) + Phase B (5 skills, graded 2026-06-04). **30/30 fixtures pass on safety** across the wedge (incl. 12 adversarial); the named job is covered end-to-end. Next (separate increment, ADR 0038 step 4+): pull infra — Clio MCP connect + contract-conformance check, deploy pipeline (#1206), boot wiring (#1166), entitlement layer (#1194).

## Law-firm / pi add-on — the PI-litigation lifecycle (19 skills, A&P pilot)

The full California PI plaintiff lifecycle built 2026-07-01 against the sent client
commitment ([`CLIENT-PROPOSAL.md`](../customers/ashton-price/CLIENT-PROPOSAL.md)),
manifest [`addon.yaml v0.2.0`](../verticals/law-firm/addons/pi/addon.yaml). Method:
author agent → adversarial gate agent (veteran CA-PI-paralegal posture, statute
fetch on deadline/statute skills) → revise. **All 19 authored + gated + revised;
every safety bright line held** (no fabricated tools, no auto-send, no fund
movement, no computing-what-it-must-not); gate findings were citation-accuracy
and coverage, all applied. 64 fixtures on disk (43 adversarial) at
`operator/fixtures/law-firm/pi/<skill>/`.

**Honesty line:** gated = adversarially _reviewed_, not _executed_. No skill in
this pack has run against a live tenant yet; first execution is the
pilot-smokeball staging pass (skills staged on `operator` in both
`pilot-smokeball` and `ashton-price` customer.yaml, 2026-07-02). Per-fixture
graded runs land in `runs/` as they happen.

Skills (discovery deepest): discovery-served-watch, discovery-response-tracker,
client-verification-tracker, separate-statement-assembler,
opposing-response-deficiency-review, meet-and-confer-drafter,
discovery-response-staging, matter-initiation-setup,
service-confirmation-watcher, medical-records-chaser,
medical-chronology-maintainer, motion-calendar-tracker,
motion-package-assembler, minors-compromise-packet, trial-binder-assembler,
mediation-settlement-tracker, lien-ledger-tracker, settlement-statement-feeder,
daily-needs-you-digest.

Catalog-level selector: **19/19 blind pass 2026-07-02** over a 33-skill catalog
(pi + base spine) — [`catalog-selector-test.md`](../verticals/law-firm/addons/pi/tests/catalog-selector-test.md).

## Real-estate vertical pack (v1: 13 skills) — Track 2, pending

Authored: 0/13. Connector dependencies: Follow Up Boss (Composio), Dotloop (BUILD #3 scaffolded), Spark API (pending).

## Manufacturing vertical pack (v1: 12 skills) — Track 2, pending

Authored: 0/12. Connector dependencies: QBO (Composio), ShipStation (✅ Phase B BUILD #2, 7 tests pass), Fishbowl (pending), NetSuite (Composio), Business Central (MCP).

## Insurance vertical pack (v1: 12 skills) — Track 2, deferred

Authored: 0/12. Deferred per Captain (partner-program gates 30-90 days).

## Cross-vertical platform components

| Component                                                                        | Status                                                                     |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Multi-tenant scaffold (Dockerfile, fly.toml, bootstrap, provision-customer.sh)   | ✅ Phase A complete, validated on Fly                                      |
| Safety substrate (5 invariants)                                                  | ✅ Phase A.5 substrate complete, all 5 pass on every container boot        |
| Operator adapter (trust_ceiling.py + aie_adapter.py)                             | ⏳ Logic complete + tested in fixtures; Hermes tool-dispatch wire deferred |
| Composio MCP integration                                                         | ✅ Wired, Gmail-tools accessible via meta-call pattern                     |
| LawPay wrapper                                                                   | ✅ 11 tools, 11 unit tests pass                                            |
| ShipStation wrapper                                                              | ✅ 12 tools, 7 unit tests pass                                             |
| Dotloop wrapper                                                                  | ⏳ Scaffold (README + dirs); impl pending                                  |
| Other Tier-1 wrappers (Fishbowl, Spark API, Acumatica, Adobe Sign, SPS Commerce) | Pending                                                                    |
| Grading rubric                                                                   | ✅ Authored, operationalized for 5 skill types                             |
| Cost-per-customer rollup                                                         | Pending (Phase E continuation)                                             |

## Captain calibration status

- Calibration set authored: 3/3 (inbox-triage, retainer-hours-reconciler, status-report-assembler all had full anatomy; retainer-hours-reconciler retired 2026-09-29)
- Calibration round complete: 0/3 (Captain has not yet labeled sample outputs)
- Rubric locked: no

Once Captain grades samples on the 3 calibration skills, the rubric tightens or stays as-is, then anatomy propagates to the remaining 55 skills.

## Open per-skill iterations

- **smd-inbox-triage prompt revision:** front-load the "no em dashes" voice rule into the SKILL.md description so Hermes' skill loader injects it at execution time. Expected to lift verdict from draft_for_review → autonomous on next real run.

## Rollup numbers

- **SKILL.md authored:** 8/58 (14%)
- **Full references authored:** 3/58 (5%)
- **Skills with at least one real-data run:** 1/58 (2%)
- **Skills graded at autonomous:** 0/58
- **Skills graded at draft_for_review:** 1/58 (smd-inbox-triage, pending iteration)
- **Tier-1 connector wrappers shipped:** 2/8 (LawPay, ShipStation)

The 58-skill matrix is a 30/60/90-day deliverable per the plan. This file updates continuously; Captain reviews the rollup weekly.
