---
title: Docs Map
section: reference
order: 3
summary: Where every canonical document lives under docs/, so a newcomer can find anything
sources:
  - label: docs/README.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/README.md
---

## What this is

A map of the `docs/` tree. Each entry says what a subdirectory holds and names its key files. This is for finding documents. For code (where source files live), see [Repository Map](/admin/playbook/repository-map) instead - this page does not duplicate it.

Two things to know first, both from `docs/README.md`:

- `ss-console` is a **spoke repo**. The canonical enterprise-wide docs live in `crane-console` (the hub) and are fetched at runtime through the crane MCP (`crane_doc(...)`). The `docs/` tree here holds SMD-specific documentation only.
- Most subdirectories carry an `index.md` that introduces the directory. Start there when exploring a new area.

## The directories

### `docs/adr/` - Architecture Decision Records

The "why" canon. Numbered ADRs (`0001` through `0051`, with gaps) capturing strategic and architectural decisions, plus two top-level files: `decision-stack.md` (the full 6-layer go-to-market decision corpus) and `index.md`. Most ADRs concern the Operator platform. For the full list with summaries and the numbering gaps, see the [ADR Index](/admin/playbook/adr-index); for the 6-layer model, see [The Decision Stack](/admin/playbook/decision-stack).

### `docs/specs/` - Implementation contracts

Formal specs, each the implementation contract for a build issue. Two subtrees:

- **`specs/operator/`** - the Operator platform specs (about 30 files). Examples: `customer-yaml-schema.md`, `capability-contracts.md`, `d1-schema.md`, `audit-emit-points.md`, `safety-invariants.md`, `inbound-trust-boundary.md`, `voice-ingestion.md`, `decommission-customer.md`, `vertical-manifest-schema.md`. `index.md` maps each spec to its P0/P1 issue.
- **`specs/verticals/`** - one spec per industry vertical pack: `law-firm.md`, `marketing-agency.md`, `insurance.md`, `accounting.md`, `dental.md`, `home-services.md`, `med-spa.md`, `mortgage.md`, `property-management.md`, `ria.md`, `title.md`, `veterinary.md`, plus `_template.md`.

### `docs/collateral/` - Sales and marketing collateral

The go-to-market deliverables. Key files: `assessment-call-script.md`, `proposal-sow-template.md`, `pricing-framework.md`, `one-pager.md`, `outreach-plan.md`, `operator-law-talk-track.md`, `google-business-profile.md`. `index.md` introduces the set.

### `docs/design/` - Functional design and UX specs

Design specifications for the portal and the Operator surfaces. Top-level: `brief.md`, `context.md`, `design-spec.md`, `index.md`. The `design/operator/` subtree holds the Operator portal designs: `00-foundations.md`, `01-admin-portal.md`, `02-client-portal.md`, `03-mcp-server-exposure.md`, `durable-task-execution-substrate.md`, `mcp-clerk-setup.md`, and the `b3-sticky-stop-live-wiring-review.md` review. Note: the canonical venture design spec is also fetched at runtime via `crane_doc('ss', 'design-spec.md')`.

### `docs/templates/` - Document and deliverable templates

Reusable templates filled in per engagement. Top-level: `sow-template.md`. Subtrees: `templates/operator/` (contracts and legal-adjacent templates - `service-contract.md`, `data-processing-addendum.md`, `baa-equivalent-confidentiality.md`, `design-partner-addendum.md`, `signing-flow.md`), `templates/packs/` (`pack-handoff-prompt.md`), and `templates/delivery-sops/` (`operator-pack-delivery-sop.md`).

### `docs/runbooks/` - Operational runbooks

Step-by-step operational procedures. Currently one subtree, `runbooks/operator/`: `first-boot.md` (standing up a customer Machine end to end), `curator-supervised-consolidation.md`, `ms-graph-azure-ad-setup.md`, `enable-gate-checklist.md` (the per-routine promotion instrument: three rungs, one recorded artifact per rung, and the pre-committed demotion rule a SEV1 triggers), and the Hermes fleet promotion runbooks `hermes-v0.18-upgrade-plan.md` and `hermes-v0.20-upgrade-plan.md` (how a pinned Hermes version is evaluated, staged across the seats, and blessed). Nested under it, `runbooks/operator/incidents/` holds the dated post-incident notes mandated by [Incident Response](/admin/playbook/incident-response), plus `_TEMPLATE.md` and a `README.md` index.

### `docs/security/` - Security documents

`smd-services-security-overview.md` (external-facing controls overview), `operator-threat-model.md` (living document), `phase2-design-package.md` (security design tracks awaiting review), and `demo-reply-relay-design.md`.

### `docs/style/` - Content and visual style rules

Enforcement rules for content quality: `empty-state-pattern.md` (render nothing rather than fabricate), `UI-PATTERNS.md` (the six visual/component rules), and `diagnostic-artifact-content-rules.md`.

### `docs/process/` - Process and workflow guides

Agent and operational process: `assessment-extraction.md`, `branch-protection-setup.md`, `global-guardrails-upload.md`, and `index.md`.

### `docs/pm/` - Product management

Product requirements: `prd.md`, `pack-production-operating-model.md`, and `index.md`.

### `docs/handbook/` - This handbook

The Venture Handbook source - the markdown pages rendered in the admin portal, including this one. One file per page (for example `overview.md`, `operator-thesis.md`, `decision-stack.md`).

### Records, audits, and history

- **`docs/reviews/`** - code-review and platform-audit records, dated (`code-review-2026-06-12.md`, etc.), plus `index.md`.
- **`docs/audits/`** - content audits (`client-facing-content-2026-04-15.md`).
- **`docs/ci-verification/`** - CI verification records (`semgrep-initial-canary.md`).
- **`docs/spikes/`** - technical research spikes and feasibility investigations (`d1-batch-api.md`, `forme-wasm-pdf.md`), plus `index.md`. The README notes this is equivalent to the hub's `research/`.
- **`docs/decisions/`** - standalone decision notes outside the ADR sequence (`vertical-selection-phase-1.md`).
- **`docs/wireframes/`** - UI wireframes (Stitch-generated, committed before issues move to ready); `index.md`.
- **`docs/handoffs/`** - session handoff records for continuity between agent sessions; `index.md`.
- **`docs/archive/`** - retired or point-in-time documents kept for the historical record: closure audits, code reviews, the workers-migration and booking-cutover validations, the lead-gen-pivot validation, and more.

## Gaps to know about

> TODO(why): ADR 0051 references `docs/operator/task-execution-framework.md` (and an ADR 0050) as the full design for the task-execution framework, but no `docs/operator/` directory and no `0050-*.md` exist in this worktree. The durable-task-execution design that does exist here is `docs/design/operator/durable-task-execution-substrate.md`. Whether the referenced `docs/operator/` path lives on another branch or was never committed is unverifiable from this tree.

## Related

- Source-code locations (not duplicated here): [Repository Map](/admin/playbook/repository-map)
- The decision corpus: [ADR Index](/admin/playbook/adr-index) and [The Decision Stack](/admin/playbook/decision-stack)
- Vocabulary: [Glossary](/admin/playbook/glossary)
