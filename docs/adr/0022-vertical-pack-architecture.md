---
title: Vertical Pack Architecture — three-layer model (platform / vertical+addons / customer), event-sourced state substrate enabling time-machine retrospect
date: 2026-05-25
status: accepted
captain: Scott Durgan
supersedes: none
related-prd: none
related-spec: docs/specs/operator/vertical-manifest-schema.md (to land alongside first migration PR)
related-issue: '#1091'
---

# ADR 0022 — Vertical Pack Architecture and Time-Machine Substrate

**Status:** Accepted (Captain decision, 2026-05-25).

> **Amendment (2026-11-09): what is built differs from the Layer 2 and Layer 3 layout below.** A pack directory under `operator/verticals/<slug>/` holds a manifest (`vertical.yaml`, and `addon.yaml` for an add-on), reference documents and fixtures. It has no `skills/`, `connectors/` or `personas/` subdirectories: skill bodies are flat and shared under `operator/skills/<skill>/SKILL.md`, and a manifest names the ones its pack uses. `vertical:` in `customer.yaml` is an unversioned label checked against an accepted list (`operator/hermes-smd-overlay/bootstrap/validate.py`, `src/lib/operator/customer-yaml/types.ts`); only an add-on line carries a version. No bootstrap code reads a pack manifest to fetch, merge or materialize a profile: what a seat runs is what its own `customer.yaml` lists. The layout below is the original design and is kept as history.

**Source:** A strategic design conversation on how to package the Operator
SKU for multiple business verticals (law, accounting, home services,
healthcare, etc.) without scattering vertical-specific changes across the
existing flat filesystem layout, and how to preserve a customer's evolution
history in a form that supports retrospective debugging without "factory
reset" UX.

## Context

The 2026-05-24 realignment (ADRs 0015–0020) and ADR 0021 (leverage Hermes
native primitives) produced a clean platform substrate: Hermes runtime with
SMD's plugin overlay, customer.yaml as configuration source-of-truth, MCP-first
connector strategy, mirror-don't-gate audit posture. What that work did NOT
address is how vertical-specific content (skills, connectors, personas,
templates, fixtures, compliance constraints) is packaged.

Today, vertical specialization exists as a naming convention:

- PI-specific skills live in `operator/skills/` with a `law-pi-*` prefix
- PI-specific connectors (`filevine`, `clio`, `lawpay`) live flat under
  `operator/connectors/` alongside generic ones (`gmail`, `qbo`)
- PI fixtures live at `operator/fixtures/law-firm/pi/`
- A single `operator/customers/_template/customer.yaml` exists; no
  vertical-default templates

Two failure modes if this stays informal:

1. **Onboarding a new vertical scatters changes** across `skills/`,
   `connectors/`, `fixtures/`, `templates/`, `bundles/`, with no "pack"
   boundary to clone, version, or test in isolation. Each new vertical costs
   linearly more than the last.

2. **Customer-specific evolution mixes with vertical-default behavior in ways
   that aren't reconstructable.** When a customer on the monthly retainer for six months
   reports "things have been drifting for the last few weeks," there is no
   structured way to show what changed when. The audit log captures events but
   the application logic to reconstruct historical state does not exist, and
   some state-changing operations (agent-authored skill file content,
   customer.yaml materialization diffs) are not yet captured at all.

The first concern affects build-time architecture. The second affects what
state the substrate has to capture _from now on_ if we want time-machine
retrospect later. Both decisions are load-bearing and cheap to make now;
expensive to retrofit.

## Decision

A three-layer architecture, with vertical as a first-class artifact, and an
event-sourced state substrate commitment.

### Layer 1: Platform (`operator/core/`)

Same for every customer regardless of vertical. Hermes substrate, plugin
overlay (`hermes-smd-audit`, `-trust`, `-voice`, `-memory-mirror`,
`-webhook-router`), capability contracts (`CRM`, `Email`, `Calendar`,
`PracticeManagement`, `DocumentStorage`, `PaymentProcessor`, etc.),
customer.yaml schema, OAuth substrate, bootstrap CLI, trust ceiling
enforcement, audit emission, voice transformation pipeline.

No vertical-specific code lives at this layer.

### Layer 2: Vertical Packs and Add-on Packs (`operator/verticals/`)

Filesystem layout:

```
operator/
  verticals/
    law/
      vertical.yaml          # manifest: declared skills, connectors, personas, compliance, templates, fixtures, evals, version
      skills/                # vertical-default skills (matter intake, conflict-check, time-entry, etc.)
      connectors/            # vertical-default connectors (filevine, clio, lawpay)
      personas/              # reference persona archetypes (intake-coordinator, paralegal, billing-clerk)
      compliance/            # vertical-level safety constraints (privilege, UPL boundaries)
      fixtures/              # vertical-grade test fixtures
      evals/                 # certification battery the pack must pass before GA
      addons/
        pi/
          addon.yaml         # add-on manifest
          skills/            # demand-letter-draft, settlement-prep, discovery-response, etc.
          fixtures/          # PI-specific synthetic matters
        defense/             # future
        estate/              # future
    accounting/
      vertical.yaml
      addons/
        bookkeeping/         # composable across verticals (a law firm can subscribe to law/pi + accounting/bookkeeping)
    home-services/
      ...
```

**Properties of the vertical model:**

- **6–10 verticals projected** at the industry level (law, accounting, home
  services, healthcare, real estate, marketing agency, retail/hospitality,
  professional services). Industry-level cardinality, not practice-area-level.
- **Add-on packs are optional specialization layers**, not parallel verticals.
  PI is an add-on on top of `law`; bookkeeping is an add-on under `accounting`
  that can compose into a `law` customer.
- **Cross-vertical add-on composition is supported.** A customer can subscribe
  to one vertical and any number of add-ons from any vertical's namespace:
  `addons: [law/pi, accounting/bookkeeping]`. Add-ons are namespaced by their
  origin vertical for provenance.
- **Flat manifest in v1, no inheritance machinery.** The vertical manifest
  declares its assets as flat lists. Industry-to-specialty inheritance
  (`extends:`) is reserved syntactically but not implemented. Adding it later
  is mechanical if the manifest is designed correctly from v1; expensive to
  retrofit if scattered file conventions are the only "manifest."
- **Internal-only labeling.** No customer-facing vertical names, descriptions,
  tier metadata, or SKU mapping in the manifest. The customer portal does not
  surface "you're on the law plan." Verticals are config that exists to
  assemble the customer's substrate.
- **Each pack has its own version and certification battery.** Verticals can
  ship at different maturity levels (e.g., `law` GA, `home-services` alpha).
  Customer.yaml binds to a pack version, not floating-latest, so vertical-pack
  evolution is opt-in per customer.

### Layer 3: Customer Configuration (`customer.yaml`)

Customer subscribes to one vertical and zero or more add-ons:

```yaml
vertical: law@1.4.0
addons:
  - law/pi@2.1.0
```

The bootstrap CLI reads the customer's vertical+addons declarations, fetches
the named pack versions, merges their manifests with the platform defaults,
and materializes the customer's Hermes profile. Customer-specific
configuration (brand, preferences, OAuth bindings, specific persona tuning)
lives in customer.yaml alongside.

### Evolution and overrides

**No formal override machinery in v1.** Customer-specific evolution happens
organically via Hermes' native learning surface:

- Hermes' `skill_manage` creates new skills at runtime in response to
  customer-specific patterns; the `hermes-smd-audit` plugin emits
  `AGENT_SKILL_CREATED` audit rows on every such event (ADR 0017).
- Honcho conclusions mirror to D1 with provenance (ADR 0016); Captain
  dismissal physically archives them.
- Customer-directed customizations are either generalizable (lift into the
  vertical or add-on pack) or one-off (live as customer-specific skills in
  the agent's Hermes state, audit-mirrored to D1).

A formal `overrides:` block in customer.yaml is deferred until a real case
demands it. The combination of base pack + organic Hermes evolution + audit
trail is sufficient to express what was original and what evolved.

### Time-machine substrate commitment

**Every state-changing operation in the Operator substrate MUST be
event-sourced into D1 with enough fidelity for retrospective reconstruction.**
This is a substrate constraint that applies to every new state-mutating
subsystem from this ADR forward, not a feature.

State classes and current capture status:

| State class                                          | Current capture                           | Time-machine ready?                            |
| ---------------------------------------------------- | ----------------------------------------- | ---------------------------------------------- |
| customer.yaml (source)                               | Git-tracked                               | Yes                                            |
| customer.yaml (materialized to D1+R2 per ADR 0012)   | Materialized but not version-historied    | **Gap — fix before first production customer** |
| Agent-created skill events                           | `AGENT_SKILL_CREATED` audit rows          | Yes                                            |
| Agent-created skill file content                     | Not persisted                             | **Gap — fix before first production customer** |
| Honcho memory conclusions                            | Mirrored to D1 with provenance (ADR 0016) | Yes                                            |
| Trust ceilings (per-skill frontmatter)               | Git-tracked                               | Yes                                            |
| Voice samples (R2)                                   | Versioned in R2                           | Yes                                            |
| Connector configurations / OAuth tokens (Fly volume) | Not versioned                             | Acceptable (changes rarely)                    |

The two gaps marked above must be closed before any production customer
onboards onto the SKU, or retrospect data is lost retroactively.

### Time-machine UX

Two flavors, materially different engineering cost:

1. **Retrospect + surgical revert.** Captain-facing surface (CLI or admin UI)
   showing what evolved in the last N days plus targeted undo handlers
   (archive a skill, archive a memory conclusion, revert a customer.yaml
   field). Achievable today once the two substrate gaps are closed. Ships
   when the first real customer-support case demands it; not before.

2. **Full state rewind.** Event-sourced reconstruction of the agent's full
   state at an arbitrary timestamp. Possible if the substrate stays
   disciplined, but the application logic to replay events back into Hermes'
   runtime is material engineering. **Deferred indefinitely.** The substrate
   commitment keeps the door open; we do not build the rewind feature
   speculatively.

### Sequencing relative to existing work

This ADR is additive to ADR 0021. The customer.yaml schema fields ADR 0021
adds (`personas[].bundles[]`, `webhook_triggers[]`) stay as designed and
co-exist with the new `vertical:` and `addons:` fields. Existing PI-specific
assets (the `law-pi-*` skills, `filevine`/`clio`/`lawpay` connectors,
`fixtures/law-firm/pi/`, the law-PI bundles authored in #1079) live where
they are until the vertical manifest schema lands; migration into
`verticals/law/addons/pi/` ships as a follow-on PR.

Order of operations:

1. **Vertical manifest schema PR** — `docs/specs/operator/vertical-manifest-schema.md`,
   `vertical.yaml` and `addon.yaml` reference structures, customer.yaml schema
   extension for `vertical:` + `addons:`.
2. **Substrate gap PRs** — agent-authored skill file content persistence to
   D1; customer.yaml materialization version history in D1. Both required
   before any production customer onboards.
3. **PI migration PR** — move existing law-PI assets into
   `verticals/law/addons/pi/`. Touches a lot of paths but is mechanical.
4. **Future verticals** — each new vertical is one pack-authoring PR plus
   any new vertical-default connectors.

## Consequences

### Positive

- **Onboarding a new vertical becomes a single pack-authoring job**, not a
  scatter of edits across the codebase.
- **Vertical packs are versionable, testable, and isolatable.** Each pack
  has its own certification battery and ships at its own maturity level.
- **Cross-vertical add-on composition** supports realistic customer shapes
  (multi-practice firms, mixed-function businesses) without forking packs.
- **Customer-specific drift is observable and reconstructable** via the
  existing audit substrate plus the two new state-capture commitments.
- **Time-machine retrospect becomes a near-term capability**, not a future
  re-architecture, because the state substrate is captured from day one.
- **No customer-authored pack burden.** Authorship stays SMD-only by
  design; no SDK, no plugin marketplace, no security/governance surface to
  build.

### Negative

- **Substrate work front-loads onto the critical path.** Two state-capture
  gaps (agent-authored skill content, customer.yaml materialization history)
  must close before the first production customer. Skipping this is
  unrecoverable — retrospect data lost retroactively can't be recovered.
- **PI migration PR is real refactor work.** Moving existing law-PI assets
  into the verticals/ tree touches many file paths and updates every
  reference to the moved paths.
- **Cross-vertical add-on composition opens a small validation surface.**
  A `law/pi` add-on assuming a `PracticeManagement` connector that an
  `accounting/bookkeeping` customer doesn't have must fail loudly at
  bootstrap, not silently at runtime. Manifest validation has to catch this.
- **Pack versioning introduces upgrade UX.** When `law@1.4.0` becomes
  `law@1.5.0`, existing customers stay pinned to 1.4.0 until explicitly
  upgraded. That's the right call but it means we need an upgrade-rollout
  surface eventually.

## Out of Scope

- **Customer-authored vertical packs.** Not a product feature at any tier.
  Non-technical buyers cannot author packs; the curation problem of
  promoting customer-authored skills into shared packs is deferred until
  aggregate signal exists across many customers.
- **Marketing-grade vertical metadata.** Verticals are internal-only — no
  public names, descriptions, tier labels, or SKU mapping in the manifest.
- **Full state rewind UX.** Substrate stays disciplined to enable it; the
  application logic to reconstruct historical Hermes runtime state from
  events is deferred indefinitely.
- **Industry-to-specialty manifest inheritance.** Manifest design
  accommodates `extends:` syntactically for forward compatibility, but
  implementation waits until at least two specialties under the same
  industry exist and share substantial assets.
- **Vertical-specific pricing or SKU encoding in customer.yaml.** Pricing
  lives in the SOW and the admin billing surface, not in the agent's
  runtime config.

## Cross-References

- ADR 0006 — Capability-adapter pattern (vertical packs use the same typed contracts).
- ADR 0007 — Per-customer Machine isolation (vertical packs materialize into the customer's Machine at bootstrap).
- ADR 0011 — Multi-persona per customer (vertical packs declare reference persona archetypes).
- ADR 0012 — customer.yaml storage (vertical + addons fields layer on top of the existing schema).
- ADR 0015 — Hermes fork posture (vertical packs ship as customer-config + plugin overlay; no Hermes core changes).
- ADR 0016 — Honcho disposition (mirror-don't-gate principle is the substrate that enables time-machine retrospect).
- ADR 0017 — Skill Curator disposition (skill_manage mirroring is the substrate that enables agent-authored evolution capture).
- ADR 0019 — customer.yaml → per-profile config translation (bootstrap CLI extends to merge vertical + addon manifests).
- ADR 0020 — Connector strategy (vertical-pack connectors follow MCP-first rule).
- ADR 0021 — Hermes native primitives (customer.yaml schema additions land first; vertical layer composes with them).
