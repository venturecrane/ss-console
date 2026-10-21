---
title: Glossary
section: running
order: 25
owner: captain
review_every: 30
summary: The venture's vocabulary, defined for a newcomer with zero context
sources:
  - label: CLAUDE.md (project instructions)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: ADR 0037 (Operator Thesis)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
---

## How to use this page

Definitions are short on purpose - one to three sentences each. Where a term carries more weight than a definition can hold, the entry points at the page or ADR that owns it. Terms are grouped: the business, the product and its architecture, governance, infrastructure, and tooling.

## The business and the people

**Captain.** The human principal who authorizes decisions. In this venture the Captain is Scott Durgan, the founder. A decision becomes canon only when the Captain authorizes it; the agent can propose but never self-govern (ADR 0030).

**The fleet.** The set of machines and agent sessions that do the venture's work. Tasks can be dispatched to fleet machines (see the `crane_fleet_dispatch` tooling) and run in parallel. Distinct from the Operator's per-customer Machines, which are customer runtime, not the fleet.

**VCMS.** The enterprise memory and notes system (the "venture content management system") reached through the crane MCP. Agents never auto-save to VCMS without explicit Captain approval (CLAUDE.md, content-policy).

**crane MCP.** The shared enterprise tooling server (tools prefixed `crane_`) that loads session context, documentation, secrets verification, notifications, and memory. Every session starts by calling `crane_preflight` then `crane_sos` (CLAUDE.md, Session Start).

**A&P.** Short for Ashton & Price, the first client running an Operator in production. Handbook pages that cite the first live engagement use the abbreviation.

**obligation register.** The single list of every piece of work SMD owes a client, held in the D1 table `client_obligations` and read from a terminal with `.claude/bin/register list`. Most rows are imported nightly; obligations stated in client letters are recorded by the session that reads them (ADR 0088). See [What We Owe Clients](/admin/playbook/obligation-register).

## The Operator and its architecture

**Operator.** SMD's productized monthly-retainer product: a configurable AI worker stood up per customer (ADR 0004, named in ADR 0034). It competes with a hire, not with software; incumbent systems are connection targets, not competitors (ADR 0037). Renamed from "AI employee"; never write "AI employee" as the product. See [The Operator Thesis](/admin/playbook/operator-thesis).

**harness / guide / memory.** The three things that together form the Operator's moat, per ADR 0037 Tenet 4. The harness is the runtime and its governance; the guide is the operational expertise SMD supplies; the memory is what the Operator learns about the customer's business. No single feature is the moat - calling one feature "the moat" is a category error.

**Hermes (the substrate).** The third-party agent runtime the Operator is built on: `NousResearch/hermes-agent` (MIT-licensed). The architectural posture is "Hermes is the substrate, trust it" - skills, memory, the tool registry, MCP integration, and approval machinery are native and not reinvented (CLAUDE.md, Operator Architecture; ADR 0015).

**the overlay (hermes-smd-overlay).** SMD's own code that runs on top of Hermes, hosted in a separate repo `venturecrane/hermes-smd-overlay`. It is plugin-only: it MUST NOT modify Hermes core files. We build only what Hermes won't (CLAUDE.md, Operator Architecture; ADR 0015).

**customer.yaml.** The per-customer configuration artifact that declares everything about one Operator: skills, entitlements, voice, connectors, personas, memory, schedules. Git is its source of truth, with portal D1 and per-customer R2 as materialized replicas (ADR 0012). It is also a security boundary (ADR 0026).

**vertical pack.** A quick-start template that configures an Operator for a specific industry (law, marketing, insurance, etc.). Packs turn the universal capability into something recognizable to a customer (ADR 0037 Tenet 5; architecture in ADR 0022). A pack is a starting point, not a claim of expertise in the client's business. See [Vertical Packs](/admin/playbook/vertical-packs).

**role (hosted-agent role).** A packaged hosted-agent job: persona and safety rules, skills, default routines, privacy model, required connections and seat template, kept at `operator/roles/<role>/` and selected by `seat.role` (ADR 0090). It is a different axis from `seat.product` (how a seat is sold and governed), a surface (the interface, ADR 0084) and a vertical pack (an industry). Couples is the first role. The word never means a permission role.

**space.** On a seat shared by several people, the privacy partition a turn runs in: `shared`, or one person's own space. A person sees the shared space plus their own; the shared space sees only itself (ADR 0091).

**seat.** One running Operator instance for one customer, named by its customer slug (for example `smd-staging`). Each seat has its own `operator/customers/<slug>/customer.yaml`, its own Fly Machine, and one row in `fleet_status`; one client may hold several seats (migrations 0089, 0093).

**routine.** A named duty the client bought: what the Operator does, how it starts, its level of autonomy, and any volume limit. Its definition lives in six artifacts, from the agreement's schedule to `routine-grid.yaml` and the skill body (`docs/runbooks/operator/routine-lifecycle.md`, ADR 0075).

**Named Administrator.** The client's person, named in the signed agreement, who may administer their Operator: apply firm-wide rules, establish its voice, and change routine levels. In configuration these people are `scope.admins` (ADR 0085).

**hosted agent.** SMD's second recurring product: a self-serve, published-price personal agent subscription bought at `/agent` and provisioned by SMD on the Operator substrate (ADR 0067). Distinct from the Operator, whose pricing stays internal.

**heartbeat.** The status report each seat posts to `POST /api/internal/heartbeat` about every 60 seconds. The console upserts it into `fleet_status`, and the `ss-fleet-alerts` pager pages when it goes stale (ADR 0023; `src/pages/api/internal/heartbeat.ts`).

**request card.** A one-line record the seat posts for every request a person sends it (`replied`, `no_reply`, or `job_done`), which the console emails to SMD ops, so an unanswered request does not look like a quiet day (migration 0122).

**shortfall.** A time the Operator could not give a person what they asked: refused, hit a limit, failed, or did only part of it. The seat reports shortfalls on its heartbeat and SMD is paged (migration 0120).

**persona.** A distinct identity an Operator can present, implemented as a Hermes profile (ADR 0011). One customer can have multiple personas; v1 ships one. Switching is done through Hermes' native `/handoff`.

## Governance and safety

**action class.** A coarse grouping of operations (for example read, send, destructive, code-execution) used to express entitlement at a manageable grain. Entitlement is set per action class per identity, which keeps the policy linear rather than per-operation exponential (ADR 0025).

**autonomy ceiling (initiation x exposure).** The cap on what an Operator may do, expressed on two independent axes: autonomy-of-initiation (may it act on its own?) and autonomy-of-exposure (may its output reach the outside world?). Ceilings are configurable per action class, code-enforced, audited, and the agent can never raise its own ceiling (ADR 0025, companion ADR 0026).

**fail-closed.** The default safety state: an entitled action with no authored entitlement is refused - no send, no draft, nothing (ADR 0035). Unconfigured is a safety state, not an identity; the harness imposes no defaults (ADR 0037 Tenet 3).

**taint gate.** An injection defense: a turn that has been fed untrusted external content is marked (a sticky session taint) and cannot then autonomously send, destroy, or execute. It is an integrity control, not an entitlement - authoring it off opens a hole and grants no capability (ADR 0027).

**broker.** A mediating service that holds connector credentials outside the agent runtime and validates each operation against what the engagement authored, so the agent never holds the raw credential. It is the authorization boundary for mediated connectors (ADR 0045).

**sticky stop / HARD_STOP.** The seat's automated circuit breaker. It moves from `OK` to `HARD_STOP` on recorded conditions such as cost spend or repeated tool failures, and the stopped seat stays stopped until it is cleared through the authenticated clear surface (ADR 0062; `docs/runbooks/operator/sticky-stop-clear.md`).

**DWD (domain-wide delegation).** A Google Workspace mechanism that lets a service account act as a specific user for a specific operation. The Operator uses per-operation DWD subjects, validated by the broker against the authored managed-mailbox list, to manage a principal's mailbox.

## Infrastructure and connectivity

**MCP.** Model Context Protocol - the standard interface by which the Operator connects to external tools and services. The connector strategy is MCP-first: use a vendor or vetted-community MCP server where one exists (ADR 0020).

**connector backends (`mcp:` / `build:` / `synthetic:`).** The three kinds of connector an Operator can wire, by `customer.yaml.connectors{}` prefix. `mcp:` is a vendor or vetted-community MCP server (preferred). `build:` is a Python adapter SMD maintains, used only when no acceptable MCP exists. `synthetic:` is the no-PM substrate (a stand-in when the customer has no practice-management system). Composio was dropped in the 2026-05-30 revision of ADR 0020.

**Honcho.** A memory subsystem within Hermes that draws conclusions about people the Operator interacts with. SMD's disposition is "mirror, don't gate": conclusions are mirrored to per-customer D1 with provenance, and Captain dismissal triggers a physical delete from Honcho (ADR 0016).

**D1.** Cloudflare's SQLite database service. The console's database is the D1 `ss-console-db`; see [Data Model](/admin/playbook/data-model).

**R2.** Cloudflare's object storage. It holds documents, SOW PDFs, and each seat's published config under `vaults/<slug>/` (`wrangler.toml`).

**Fly Machine.** The Fly.io virtual machine a seat runs on, one per customer, named `hermes-<slug>` (ADR 0007).

**reprovision.** Rebuilding or redeploying one seat's Machine from its authored config with `operator/bin/reprovision.sh <slug>`, which wraps `provision-customer.sh` under Infisical credentials.

**ring (rollout).** A group of seats that takes a fleet release together. `operator/fleet/rings.yaml` orders them from SMD's own staging seat to the paying client's seat, and a failure in one ring halts the rollout before the next. See [Deployment & Release](/admin/playbook/deployment-release).

**Infisical.** The one secrets vault for SMD; every credential is stored at the `/ss` path and pushed from there to the runtime stores. See [Secrets & Access](/admin/playbook/secrets-access).

**Clerk.** The identity service behind sign-in for both the admin console and the client portal (`src/middleware.ts`).

## The two-layer taxonomy

**The two-layer taxonomy.** SMD keeps two deliberately distinct lists of what it does (ADR 0001). The **delivery taxonomy** has six categories (process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, AI & automation) and is the marketing and doctrine source of truth. The **client-assessment extraction taxonomy** has five IDs (`process_design`, `tool_systems`, `data_visibility`, `customer_pipeline`, `team_operations`, in `src/portal/assessments/extraction-schema.ts`) and structures what the assessment call captures. It began as the observation taxonomy of the automated lead-gen layer, which ADR 0060 retired on 2026-07-01. Assessments speak the extraction taxonomy, marketing speaks delivery, and the consultant translates between them - do not conflate them.

## Related

- The decisions these terms come from: [The Decision Stack](/admin/playbook/decision-stack) and the [ADR Index](/admin/playbook/adr-index)
- Where the documents live: [Docs Map](/admin/playbook/docs-map)
