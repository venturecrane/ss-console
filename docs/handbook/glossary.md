---
title: Glossary
section: running
order: 25
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: The venture's vocabulary, defined for a newcomer with zero context
sources:
  - label: CLAUDE.md (project instructions)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: ADR 0037 (Operator Thesis)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
  - label: ADR 0064 (severity ladder)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0064-operator-service-commitments.md
  - label: ADR 0071 (confirm and draft-for-review)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md
  - label: ADR 0075 (routine levels)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0075-routine-grid-compositional-enforcement.md
  - label: Ops triage agent orders
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/TRIAGE.md
  - label: Lane-proof runbook
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/lane-proof.md
  - label: Routine promotion checklist (rungs)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/enable-gate-checklist.md
  - label: Invoice actions (send and present)
    href: https://github.com/venturecrane/ss-console/blob/main/src/pages/api/admin/invoices/[id].ts
  - label: Release rings
    href: https://github.com/venturecrane/ss-console/blob/main/operator/fleet/rings.yaml
---

Every term an agent's report or a Playbook page is likely to use, defined in plain words. When a word in a report is not here, ask the agent to explain it and to add it to this page.

## How to use this page

Definitions are short on purpose - one to three sentences each. Where a term carries more weight than a definition can hold, the entry points at the page or ADR that owns it. Terms are grouped: the business, running SMD day to day, the product and its architecture, governance, infrastructure, and tooling.

## The business and the people

**Captain.** The human principal who authorizes decisions. In this venture the Captain is Scott Durgan, the founder. A decision becomes canon only when the Captain authorizes it; the agent can propose but never self-govern (ADR 0030).

**The fleet.** Two uses. In agent tooling, the set of machines and agent sessions that do the venture's work; tasks can be dispatched to fleet machines (`crane_fleet_dispatch`) and run in parallel. In Operator operations (a "fleet release", the "fleet dashboard"), the set of all client and SMD seats (`operator/fleet/rings.yaml`).

**VCMS.** The enterprise memory and notes system (the "venture content management system") reached through the crane MCP. Agents never auto-save to VCMS without explicit Captain approval (CLAUDE.md, content-policy).

**crane MCP.** The shared enterprise tooling server (tools prefixed `crane_`) that loads session context, documentation, secrets verification, notifications, and memory. Every session starts by calling `crane_preflight` then `crane_sos` (CLAUDE.md, Session Start).

**A&P.** Short for Ashton & Price, the first client running an Operator in production. Handbook pages that cite the first live engagement use the abbreviation.

**obligation register.** The single list of every piece of work SMD owes a client, held in the D1 table `client_obligations` and read from a terminal with `.claude/bin/register list`. Most rows are imported nightly; obligations stated in client letters are recorded by the session that reads them (ADR 0088). See [What We Owe Clients](/admin/playbook/obligation-register).

## Running SMD day to day

**dossier.** The private file an agent must read before touching a client's engagement: what the client asked for, what SMD has agreed, and how to talk to them. It lives at `operator/customers/<slug>/dossier.md` in the private engagements repository, and a safety check blocks writes about a client whose dossier was not read first ([Handoff Companion](/admin/playbook/handoff-companion)).

**SOW.** Statement of Work: the signed scope, price and payment schedule for a consulting engagement. Signing it through SignWell creates and sends the deposit invoice ([Money](/admin/playbook/money)).

**DPA.** Data Processing Addendum: the contract terms on how SMD handles a client's data, signed alongside an Operator service agreement (`docs/legal/operator-dpa-template.md`).

**verify ID.** An id starting `vfy_` that names a recorded check of the live system: what was probed and what came back. A claim that something works on a real seat is accepted only with one, and the merge gate refuses runtime claims without it ([Building the Platform](/admin/playbook/building-the-platform)).

**projection drift.** When the copy of a client's configuration the console shows (`customer_configs`) or the copy a seat reads no longer matches what was approved in the repository. A nightly check opens an issue when it happens ([The Administrator's Week](/admin/playbook/administrators-week)).

**wake row.** The record a seat writes each time a scheduled routine is due: one saying it ran, or one saying it deliberately did not. A due time with neither means the schedule silently failed, and a nightly check reports it ([The Administrator's Week](/admin/playbook/administrators-week)).

**attested.** Evidence SMD can show exists but cannot check itself, such as a filing inside a client's own system where SMD's checks hold no credential. Different from evidence a check can probe directly ([What We Owe Clients](/admin/playbook/obligation-register)).

**`/eos` Check I.** The end-of-session step that asks whether the session read client correspondence and, if so, whether every promise in it was recorded in the obligation register. It records; it never blocks ([What We Owe Clients](/admin/playbook/obligation-register)).

**team@.** `team@smd.services`, the shared inbox where SMD's systems send every alert and report, from "SMD Services Ops". Reading it is the first step of the daily routine ([The Administrator's Week](/admin/playbook/administrators-week)).

**healthchecks.io.** An outside service that expects a regular "still running" signal from each of SMD's watchers (the 2-minute alerter, the triage agent, each ops workspace) and emails when one goes silent. It is what makes an empty inbox trustworthy ([The Administrator's Week](/admin/playbook/administrators-week)).

**triage agent.** The AI agent that runs every hour on `smd-ops`, investigates each new work-health item with read-only tools, and emails team@ one report: what it handled, what needs you, and fixes for the Captain to approve. It may not change code, restart anything or message a client (`operator/ops-machine/TRIAGE.md`; [Work Health](/admin/playbook/work-health)).

**work health.** The hourly check of the work on every live seat: unanswered requests, stuck or held jobs, missed scheduled runs, dead sign-ins ([Work Health](/admin/playbook/work-health)).

**ACTION page.** An email whose subject starts `ACTION:`, sent every hour while a client sign-in is dead or a connection keeps failing, because that seat's work in that system has stopped until it is fixed ([Work Health](/admin/playbook/work-health)).

**ops workspace.** A client's own locked-down machine in the cloud (`ops-<slug>`), the only place that client's documents are opened and worked on; never a laptop ([Client Ops Workspaces](/admin/playbook/ops-machine)).

**smd-ops.** SMD's own operations machine. It runs the hourly triage agent and keeps archived agent transcripts for 30 days; it holds no client documents ([Client Ops Workspaces](/admin/playbook/ops-machine)).

**break-glass.** Doing something the normal rules forbid because a client is waiting and the normal path is down, such as opening client documents on a laptop when the workspace is broken. It must name a reason, and it is recorded ([Client Ops Workspaces](/admin/playbook/ops-machine)).

**SEV1, SEV2, SEV3.** The three severity levels of an incident. SEV1: the Operator is down or acted outside what the client authorized; work starts at once and the client hears within 24 hours. SEV2: degraded, worked the same business day. SEV3: a question or small request, answered the next business day (ADR 0064; [Incident Response](/admin/playbook/incident-response)).

**NO REPLY.** The alarm kind of **request card** (defined below): a person at the client asked the Operator something and had no answer after 30 minutes.

**SHORTFALL.** The team@ email `[SMD Ops] SHORTFALL <seat>`, sent when the Operator could not give someone what they asked; see **shortfall** below.

**lane.** A kind of job a seat runs in the background, such as a medical chronology, a demand letter, or drafting one class of document (`docs/runbooks/operator/lane-proof.md`).

**NEW KIND OF ASK / proof run.** When a client asks for a job its seat has never done, the seat holds the job and emails team@ "NEW KIND OF ASK". An agent then does a proof run: the whole job on the client's real matter with nothing filed or sent, so you can read the result and accept it or say no before it runs for real (`docs/runbooks/operator/lane-proof.md`).

**held job.** A job that stopped on purpose before delivering, usually because an independent review found something to fix or a check failed. Nothing was filed; a person decides whether to release, redo or explain ([Incident Response](/admin/playbook/incident-response), History).

**staged send.** A message SMD needs a client's seat to send with exact words and files SMD made. The Captain approves the words, recipient and files, and an agent sends it through the seat's own safety checks and record (`docs/runbooks/operator/staged-send.md`).

**SMD note.** A message in which the Operator tells a person at the client, in words the Captain approved, something SMD did for them (`docs/runbooks/operator/smd-note.md`).

**change request.** A client's request to change their Operator, made with the **Request a change** button in their portal; it emails team@ and appears at `/admin/operator/requests` ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

**pause.** Stopping a seat's Operator from working while keeping its machine running so an agent can look at it; an agent runs `operator/bin/pause-customer.sh`, and the same script resumes it ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

**decommission.** Ending a client's seat for good with `operator/bin/decommission-customer.sh`, which also destroys its ops workspace. The Captain decides; an agent runs it ([Customer Lifecycle](/admin/playbook/customer-lifecycle)).

**present vs send (invoice).** **Present** makes an invoice payable in the client's portal without emailing anyone. **Send** has Stripe email the invoice and the system send its own notice (`src/pages/api/admin/invoices/[id].ts`; [Money](/admin/playbook/money)).

**stand-up fee.** The one-time fee for setting up a client's Operator, billed as its own implementation invoice, separate from the monthly retainer ([Money](/admin/playbook/money)).

**ACH (bank debit).** Paying straight from a bank account. It is each seat's default payment method and carries no fee; a card adds a 3% processing fee ([Money](/admin/playbook/money)).

**P0, P1 (issue priority).** Labels on GitHub issues (`prio:P0`, `prio:P1`); P0 is the most serious. The nightly safety checks report as P0 or P1 issues, and reading them is part of the daily routine ([The Administrator's Week](/admin/playbook/administrators-week)).

**rolling issue.** One GitHub issue that a scheduled check keeps updating run after run, instead of opening a new one each time, and closes when there is nothing to report. "Handbook: pages due for review" and "Obligation register: overdue or unverifiable work" are rolling issues.

**pull request (PR) and required checks.** Every change to SMD's code and configuration is proposed as a pull request; it merges only when every required automated check passes, with no person's approval needed ([Who Does What](/admin/playbook/who-does-what)).

**live-apply vs rebuild.** A merged change to a client's configuration either takes effect on the running seat within about 15 seconds (routine levels, permissions, who may email the Operator, and similar), or needs the seat rebuilt (connections, the AI model, memory and more), which an agent does and reports ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

**fleet release, skills release.** How a change to the Operator itself reaches the seats. A fleet release ships new Operator code ring by ring (see **ring** below) and rolls a seat back on its own if it fails its checks; a skills release ships new or changed skills with no restart ([Deployment & Release](/admin/playbook/deployment-release)).

**Claude-connector grant.** Permission for a person at a client to reach their Operator from their own Claude app. SMD can revoke it at once, and only an admin can issue it again (ADR 0057; [Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

**private companion.** The `admin/` pages in the private engagements repository that hold what cannot be public: accounts, recovery contacts, vendor bills, and each client's commercial detail ([Handoff Companion](/admin/playbook/handoff-companion)).

**Max login.** The Captain's personal Claude subscription login. Agents, `smd-ops` and every ops workspace run Claude on it today, which is one of the single-person dependencies on [Continuity](/admin/playbook/continuity).

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

**sticky stop / HARD_STOP / cost stop.** The seat's automated circuit breaker. It moves from `OK` to `HARD_STOP` on recorded conditions such as cost spend or repeated tool failures, and the stopped seat stays stopped until someone investigates and uses the **Clear stop** form in the admin console. A stop caused by spending is a cost stop, and only the Captain clears it (ADR 0062; `docs/runbooks/operator/sticky-stop-clear.md`).

**routine level: flag-only, prepare-and-route, auto-handle.** How far one routine may go. Flag-only reads and tells someone; prepare-and-route drafts for a person to send; auto-handle acts on its own. Anything touching deadlines or money, or sending to opposing counsel or a court, never auto-handles (ADR 0075).

**draft-for-review and confirm.** Two settings for an outside email. Under draft-for-review the Operator drafts and a person at the client sends it themselves; under confirm the Operator asks a person yes or no in the same conversation and, on a yes, sends it itself (ADR 0071).

**exposure ladder and rung.** The three steps a routine climbs before it may act on its own: rehearsed against a fake firm (rung 1), drafting on the client's seat under review (rung 2), acting on its own with the Captain's signed approval (rung 3). Each rung needs its own written evidence, and after a SEV1 the routine drops to the bottom and climbs again (`docs/runbooks/operator/enable-gate-checklist.md`).

**shadow firm.** A standing rehearsal in which a fake, hostile client is played against a test seat to replay every kind of incident SMD has had; a routine's first rung is a passing shadow-firm run (`docs/runbooks/operator/shadow-firm.md`).

**demotion rule.** The rule, decided in advance, that any SEV1 pauses the seat and drops every routine involved to the bottom rung until the root cause is fixed and the incident has been rehearsed against the fix ([Incident Response](/admin/playbook/incident-response)).

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
