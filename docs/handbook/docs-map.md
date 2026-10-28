---
title: Docs Map
section: engineering
order: 14
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: Where every canonical document lives under docs/, so a newcomer can find anything
sources:
  - label: docs/README.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/README.md
---

This page says where each kind of document lives in the repository's `docs/` folder. An administrator needs it when an agent's report cites a document path and you want to know what sort of document it is, or when you are looking for a runbook, a spec or a template and do not know its folder.

## What this is

A map of the `docs/` tree. Each entry says what a subdirectory holds and names its key files. This is for finding documents. For code (where source files live), see [Repository Map](/admin/playbook/repository-map) instead - this page does not duplicate it.

Two things to know first, both from `docs/README.md`:

- `ss-console` is a **spoke repo**. The canonical enterprise-wide docs live in `crane-console` (the hub) and are fetched at runtime through the crane MCP (`crane_doc(...)`). The `docs/` tree here holds SMD-specific documentation only.
- Most subdirectories carry an `index.md` that introduces the directory. Start there when exploring a new area.

## The directories

### `docs/adr/` - Architecture Decision Records

The "why" canon. Numbered ADRs (`0001` through `0093`, 91 files, with gaps) capturing strategic and architectural decisions, plus two top-level files: `decision-stack.md` (the full 6-layer go-to-market decision corpus) and `index.md`. Most ADRs concern the Operator platform. For the full list with summaries and the numbering gaps, see the [ADR Index](/admin/playbook/adr-index); for the 6-layer model, see [The Decision Stack](/admin/playbook/decision-stack).

### `docs/specs/` - Implementation contracts

Formal specs, each the implementation contract for a build issue. Two subtrees:

- **`specs/operator/`** - the Operator platform specs (35 files including `index.md`). Examples: `customer-yaml-schema.md`, `capability-contracts.md`, `d1-schema.md` (the per-customer D1 that was never built; historical), `audit-emit-points.md`, `safety-invariants.md`, `inbound-trust-boundary.md`, `voice-ingestion.md`, `decommission-customer.md`, `vertical-manifest-schema.md`. `index.md` maps each spec to its P0/P1 issue.
- **`specs/verticals/`** - one spec per industry vertical pack: `law-firm.md`, `marketing-agency.md`, `insurance.md`, `accounting.md`, `dental.md`, `home-services.md`, `med-spa.md`, `mortgage.md`, `property-management.md`, `ria.md`, `title.md`, `veterinary.md`, plus `_template.md`.

### `docs/collateral/` - Sales and marketing collateral

The go-to-market deliverables. Key files: `assessment-call-script.md`, `proposal-sow-template.md`, `pricing-framework.md`, `one-pager.md`, `outreach-plan.md`, `operator-law-talk-track.md`, `google-business-profile.md`. `index.md` introduces the set.

### `docs/design/` - Functional design and UX specs

Design specifications for the portal and the Operator surfaces. Top-level: `brief.md`, `context.md`, `design-spec.md`, `index.md`. The `design/operator/` subtree holds the Operator portal designs: `00-foundations.md`, `01-admin-portal.md`, `02-client-portal.md`, `03-mcp-server-exposure.md`, `04-console-structure.md`, `05-console-blueprint.md`, `durable-task-execution-substrate.md`, `legibility-ground-truth-matrix.md`, `mcp-clerk-setup.md`, `substrate-watch.md`, the `b3-sticky-stop-live-wiring-review.md` review, and `surface-briefs/` (one locked brief per Operator console surface). `design/admin/surface-briefs/` holds the admin surface briefs (`clients.md`), `design/hosted-agent/` the Hosted Agent product model (`tiered-autonomy-product-model.md`), and `design/draft-crane/` prior-art product shape carried over from another venture. Note: the canonical venture design spec is also fetched at runtime via `crane_doc('ss', 'design-spec.md')`.

### `docs/templates/` - Document and deliverable templates

Reusable templates filled in per engagement. Top-level: `sow-template.md`. Subtrees: `templates/operator/` (contracts and legal-adjacent templates - `service-contract.md`, `data-processing-addendum.md`, `baa-equivalent-confidentiality.md`, `design-partner-addendum.md`, `signing-flow.md`), `templates/packs/` (`pack-handoff-prompt.md`), and `templates/delivery-sops/` (`operator-pack-delivery-sop.md`).

### `docs/runbooks/` - Operational runbooks

Step-by-step operational procedures, in two subtrees. `runbooks/hosted-agent/` holds the Hosted Agent runbooks: `concierge.md` (the per-sale manual half of ADR 0067) and `couples-setup.md` (ADR 0090, ADR 0091). `runbooks/operator/` holds the Operator runbooks, among them `implementation-playbook.md`, `sticky-stop-clear.md`, `first-boot.md` (standing up a customer Machine end to end), `curator-supervised-consolidation.md`, `ms-graph-azure-ad-setup.md`, `enable-gate-checklist.md` (the per-routine promotion instrument: three rungs, one recorded artifact per rung, and the pre-committed demotion rule a SEV1 triggers), `routine-lifecycle.md` (how a routine definition is created, changed, added and retired across the six artifacts that hold it, and which check catches a skipped step), and the Hermes fleet promotion runbooks `hermes-v0.18-upgrade-plan.md`, `hermes-v0.20-upgrade-plan.md` and `hermes-v0.21-upgrade-plan.md` (how a pinned Hermes version is evaluated, staged across the seats, and blessed). Nested under it, `runbooks/operator/incidents/` holds the dated post-incident notes mandated by [Incident Response](/admin/playbook/incident-response), plus `_TEMPLATE.md` and a `README.md` index.

### `docs/security/` - Security documents

`smd-services-security-overview.md` (external-facing controls overview), `operator-threat-model.md` (living document), `phase2-design-package.md` (security design tracks awaiting review), and `branch-protection.md` (the documented snapshot of `main` branch protection).

### `docs/operator/` - Operator design documents

`task-execution-framework.md`, the full design that [ADR 0050](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0050-operator-task-execution-framework.md) and ADR 0051 point to for the task-execution framework.

### `docs/doctrine/` - Agent operating doctrine

`agent-operating-doctrine.md` (the registry of agent operating laws, each with its incidents and enforcement tier), `wired-contract.md` (the reachability contract `/wired` produces), and `report-is-a-probe-checklist.md`.

### `docs/legal/` - Legal templates

`operator-dpa-template.md`, the Operator data processing addendum template.

### `docs/marketing/` - Marketing source documents

`positioning-spine.md`, `elevator-pitch.md`, `pack-standard.md`, `proof-ledger.md`, and `paid-ads-launch-kit.md`.

### `docs/research/` - Research

Background research kept for reference: `paid-ads/` (competitive landscape, channel economics, funnel and tracking), `hosted-agent/`, `voice-distillation/`, and `draft-crane/` (prior art carried over from another venture's console).

### `docs/skills/` - Captain-side skill sources

The tracked source of venture-local slash commands (`medchron/SKILL.md`, `backlog-review/SKILL.md`), installed by `scripts/install-captain-skills.sh` (CLAUDE.md, Captain-Side Skills).

### `docs/style/` - Content and visual style rules

Enforcement rules for content quality: `empty-state-pattern.md` (render nothing rather than fabricate), `UI-PATTERNS.md` (the six visual/component rules), `surface-brief.md` (the per-surface definition exercise every portal and admin surface goes through before design), and `diagnostic-artifact-content-rules.md`.

### `docs/process/` - Process and workflow guides

Agent and operational process: `assessment-extraction.md`, `branch-protection-setup.md`, `global-guardrails-upload.md`, and `index.md`.

### `docs/pm/` - Product management

Product requirements: `prd.md`, `pack-production-operating-model.md`, and `index.md`.

### `docs/handbook/` - This handbook

The Venture Handbook source - the markdown pages rendered in the admin portal, including this one. One file per page (for example `overview.md`, `operator-thesis.md`, `decision-stack.md`).

### Records, audits, and history

- **`docs/api/`** - the API inventory (`README.md`), one row per file under `src/pages/api` with its methods, auth gate, and purpose. Generated by `scripts/api-inventory.ts`; `tests/api-inventory.test.ts` fails when it and the tree disagree, so it is never hand-edited.
- **`docs/reviews/`** - code-review and platform-audit records, dated (`code-review-2026-06-12.md`, etc.), the dated claims reviews (`claims-<date>.md`, each with the `.probe.sh` that re-runs its checks), the 2026-07-04 Operator strategic review and its evidence, plus `index.md`.
- **`docs/audits/`** - content audits (`client-facing-content-2026-04-15.md`, `operator-output-provenance-2026-07-31.md`).
- **`docs/ci-verification/`** - CI verification records (`semgrep-initial-canary.md`).
- **`docs/spikes/`** - technical research spikes and feasibility investigations (`d1-batch-api.md`, `forme-wasm-pdf.md`), plus `index.md`. The README notes this is equivalent to the hub's `research/`.
- **`docs/decisions/`** - standalone decision notes outside the ADR sequence (`vertical-selection-phase-1.md`).
- **`docs/wireframes/`** - UI wireframes (Stitch-generated, committed before issues move to ready); `index.md`.
- **`docs/handoffs/`** - session handoff records for continuity between agent sessions; `index.md`.
- **`docs/archive/`** - retired or point-in-time documents kept for the historical record: closure audits, code reviews, the workers-migration and booking-cutover validations, the lead-gen-pivot validation, and more.

## Related

- Source-code locations (not duplicated here): [Repository Map](/admin/playbook/repository-map)
- The decision corpus: [ADR Index](/admin/playbook/adr-index) and [The Decision Stack](/admin/playbook/decision-stack)
- Vocabulary: [Glossary](/admin/playbook/glossary)
