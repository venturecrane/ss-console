---
title: The Operator Page Is the Operator's Full, Legible, Progressively-Configurable Face
date: 2026-07-07
status: accepted
captain: Scott Durgan
amends: 0052-operator-portal-management-console-not-data-surface.md
related-adr: 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md, 0026-config-surface-is-a-security-boundary.md, 0035-no-imposed-entitlement-defaults.md, 0041-operator-authority-posture.md, 0046-admin-ia-service-spine.md, 0012-customer-yaml-storage.md
related-doc: docs/design/operator/legibility-ground-truth-matrix.md
---

# ADR 0069 — The Operator Page Is the Operator's Full, Legible, Progressively-Configurable Face

**Status:** Accepted (Captain sign-off 2026-07-07). Companion/amendment to ADR 0052.

> **Amendment (2026-11-09).** Lock 3 says every client-configurable write persists to `customer.yaml`. Three client controls were built otherwise, on purpose: the entitlement level under the authored ceiling, pause, and the output-class specs change a seat without touching `customer.yaml`. They are runtime overrides or a separate vault object, each audited and each clamped by what `customer.yaml` authors. [ADR 0094](./0094-customer-configuration-as-built.md) section 8 is the record of them. No portal path writes `customer.yaml`; git stays its only authoring source (ADR 0094 section 1).

**Source:** A 2026-07-07 working session reviewing the client-portal Operator page against a live seat. The Captain's direction: the page must be the one place anyone can see **what the operator is, everything it can do, everything it does regularly, how it does it, what it remembers, and what it is allowed to do** — and configure any of that, to the degree the client has been granted control. Grounded in four code sweeps whose findings are recorded in the companion ground-truth matrix.

---

## Context

ADR 0052 fixed the boundary correctly — the portal is a **management console for the AI employee, not a data surface** — and named three jobs: Direct (config), Account (audit), Administer (relationship). But it drew **Direct** too thin. As built, Direct is a scatter of settings links; the landing is a table-of-contents grid that never introduces the employee. The review found:

- The page opens with a status band and activity log (correct), then hands the client eight section-link cards and a sidebar that repeats one of them.
- Several config facets a manager of an AI employee most needs — **its skills, its recurring schedule, its multi-step workflows, its memory** — have **no inspector at all**.
- Everything that _is_ configurable is Read + "Request a change"; the actual write paths record intent and **never persist to source of truth** (the theater pattern, live in `trust-ceiling.ts:68` and every governance/authority handler).
- Admin and client portals carry **parallel duplicate implementations** of the same config renderers over a shared foundation.

The Captain's reframe resolves it: **Direct is the heart of the page, and it must be deep.** The console is the operator's full legible face. This ADR extends ADR 0052's Direct job accordingly and locks the model as guards, because prose alone let the portal drift before (the per-page-prop tab drift ADR 0068 just repaired).

### The one distinction that keeps this inside ADR 0052

An operator has a **configured operational shape** — what it can do (skills), what it does on a schedule (recurring jobs), and how it runs a multi-step process end to end (workflows). The page surfaces that shape, generically, for **any** operator. Two things must never be confused:

- **The configured process** — the operator's _job description_: the steps it runs, what is automated at each, what needs a human. Authored config, read from `customer.yaml`/`vertical.yaml`, rendered by one generic viewer. **We show this in full — however deep or shallow a given operator's process is.**
- **The client's business data** — the actual records those processes act on (the client's system of record). **Still prohibited** (ADR 0052 §2; this is why the stored/rendered `MatterPhase` enum and "Matters" surface were removed).

**Depth is per-customer configuration, never product structure.** The same viewer renders a many-step process and a single-step one identically; no surface, field, enum, or schema key names or assumes a vertical. The product builds the generic capability; a seat's authored config supplies the specifics. Verifying that means rendering the deepest and simplest real seats **without the viewer knowing anything about either one's domain** (see the companion matrix for the complexity range).

### Ground truth (see the companion matrix for the per-facet detail)

- **Two data planes.** Config lives in the central `customer_configs` projection (readable now); runtime state lives in each Machine's own D1, reachable only through the **unwired** ADR 0043 seam. This sorts every facet into buildable-now vs needs-wiring.
- **The spectrum already exists.** Managed→self-managed is the built ADR 0041 authority model (`authority.ts` + `domain-surface.ts`): per-client, per-domain, `managed` by default, SMD control always retained. The build wires it through, it does not invent it.
- **The write-back is deferred everywhere.** No config edit persists to `customer.yaml` today. This is the universal spine that makes configurability real.
- **The workflow/process facet does not exist.** ADR 0052 mandated it as authored config; it was never built. It is comment-grouped skills + README prose. A generic schema must be designed first.

---

## Decision — four locks

### Lock 1 — Configured-config-and-own-actions only; never client business data

The page renders (a) the operator's **configuration**, (b) the operator's **own actions/output** (the governance/audit record), and (c) **relationship admin** — and nothing else (ADR 0052 boundary, unchanged). The **configured workflow/process is in** as authored config, read from `customer.yaml`/`vertical.yaml`, through a generic vertical-agnostic viewer. The client's **business data — the records those processes act on — is out**, full stop. No surface, field, enum, or schema key names or assumes a vertical.

> **Guard:** `tests/operator-legibility-boundary.test.ts` — the workflow/config viewers' data source is the config plane (customer.yaml / projection), never a runtime business-data surface; no client-business-data field renders (the `matterRef`-shaped leak ADR 0052 removed is the canonical anti-pattern); the workflow-viewer schema and code carry no vertical vocabulary; extends the existing no-vertical-hardcoding and forbidden-strings guards.

### Lock 2 — Complete legibility: the facet spine is exhaustive-or-explicitly-suppressed

The page's spine is the **full authoritative facet list** (derived from `customer-yaml/types.ts` + `customer-yaml-blocks.yaml`). Every facet of the operator's configuration and behavior is either surfaced with a viewer **or** carries an explicit, reasoned "not-surfaced" decision — never silently absent. A new config facet is a decision, not an omission. (Same shape as the activity-language allowlist's mapped-or-suppressed exhaustiveness test that already works.)

> **Guard:** `tests/operator-facet-legibility.test.ts` — every member of the canonical facet registry appears in exactly one of {has-viewer, explicitly-suppressed-with-reason}; a new facet fails the test until someone decides its client surface. INERT facets (addons, practice_areas, gmail_push, business_hours.days/start/end) must be marked so the page never implies an effect that does not happen.

### Lock 3 — Progressive configurability on the existing authority model, governed and floored

Client configurability rides the **built ADR 0041 authority model**: per-client, per-domain managed vs client-operable, shipping `managed` (every client switch off), SMD control always retained (additive), reachable to `self_managed` per facet by an explicit authored change. Presets (a `managed`/`self_managed` default) sit on top of per-domain overrides — per-facet switches are the unit; tiers are presets.

**Any client-configurable write — and any concierge change — MUST meet the ADR 0026 governance bar:** principal-authenticated; persisted to `customer.yaml` (git source of truth, ADR 0012) and re-projected; **immutably audited**; **floor-checked** (ADR 0025 vertical floors are non-raisable; ADR 0035 — an unconfigured entitled action is fail-closed, never defaulted); the **agent can never write its own config**; raise (toward autonomy) is guarded harder than lower. **A handler that only records intent is not done.** The managed→self-managed switch decides _who operates_ the governed write path (client self-serve vs SMD-mediated); it never changes the bar the write must clear.

> **Guard:** `tests/operator-config-writeback.test.ts` — (a) every operable config endpoint persists to the projection and emits a `config_change_audit` event (no `console.info`-only / intent-only handlers — grep guard, ADR 0026 verification #4); (b) a raise above a vertical floor is rejected and audited; (c) `resolveDomainAuthority(null, …)` is `managed` for every domain (launch-safe). Verified live: a change persists **and** re-projects on a real seat.

### Lock 4 — One resolver + one viewer per facet, mounted in both portals

Each config facet has exactly **one resolver + one viewer component**, mounted in the client portal and the admin portal, differing only by role/authority overlay and the legitimate admin-only overlays (fleet health, cost, provisioning per ADR 0052 §8 / ADR 0046). The current parallel duplication — two governance labelers, two connector parsers, two custody labelers, two authority renderers — collapses to one each. No page-local reimplementation; per-page prop-shaping is prohibited (the ADR 0068 lesson).

> **Guard:** `tests/operator-shared-viewer.test.ts` — the per-facet viewer modules live in one shared location imported by both the portal and admin mounts; a grep guard fails a second parser of `personas_json`/`connectors_json`/`authority_json` outside the shared module.

### The upstream builds this decision creates (not hand-waved)

Three facets require upstream work **before** their viewer, each its own sequenced slice, none allowed to masquerade as done:

1. **Schedule** — extend the config projection to carry `cron[]`/`bundles[]` (dropped today) or read them from R2/git; then the Schedule viewer.
2. **Workflow/process** — design a new authored-config facet (a generic `workflow`/`phases` structure in `vertical.yaml` or `customer.yaml`, no vertical vocabulary in the schema) and populate the deepest and simplest existing seats to prove the viewer spans the full complexity range with zero domain knowledge; then the generic viewer.
3. **Runtime seam** — wire `OPERATOR_RUNTIME_READ_URL`/`_SECRET` (ADR 0043) for Activity; add resolvers for Memory (`memory_export`) and, for agent-authored skills/job-execution, new read-kinds.

And **Tier 0 (write-back)** is the foundational slice: until `customer.yaml` write-back (ADR 0012 §5 / ADR 0026) is real, every operable control is theater.

---

## Consequences

- ADR 0052's Direct job is deepened from "settings links" to "the operator's full legible configuration," and its no-data-surface boundary is preserved verbatim (Lock 1). The three-jobs model stands; Direct just gets its due weight.
- The build sequences off the companion matrix's tiers: Tier 0 (write-back) and the shared-viewer scaffold first; then Tier 1 facets (real now); then Tier 2 (seam-gated) and Tier 3 (workflow schema).
- Every future config facet integrates by extending the facet registry (Lock 2) and the shared viewer set (Lock 4) — never by threading props or reimplementing per portal.
- The admin and client operator surfaces converge on one viewer set, ending the parallel-duplication drift.

## Non-goals and open questions (for sign-off)

- **Directionally agreed (Captain, this session):** admin and client use the _same_ viewer at the _same_ depth for the client's own operator (admin adds only fleet/ops overlays); the spectrum's unit is per-facet switches with named tiers as presets on top.
- **Open — entitlements depth.** Surface the coarse projected model (persona-level exposure × per-skill initiation), or build the richer 7-action-class × reach matrix that today lives only as prose in `ENTITLEMENTS.md`? (Affects the Entitlements slice scope.)
- **Open — workflow schema home.** `vertical.yaml` (pack-level, shared by all customers on that pack) vs `customer.yaml` (per-customer phase vocabulary) vs both (pack default + per-customer override). Decided when the workflow slice is designed.
- This ADR changes no code. The build is Captain-directed and tracked as an epic + per-slice issues, each with a "verified live on pilot-smokeball" acceptance criterion.
