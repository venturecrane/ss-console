# Operator Admin Console — Design

**Status:** Draft for Captain review (2026-06-08). Build-ready design for the **SMD side** of Operator management.
Builds on [`00-foundations.md`](00-foundations.md) — read it first. Pairs with [`02-client-portal.md`](02-client-portal.md),
built in parallel by a separate team. Designed clean-slate; the two existing pages (`/admin/operator/costs`,
`/admin/operator/config-history`) are reusable parts, not the design.

Surface root: `admin.smd.services/admin/operator/*` (role-gated; see §2).

---

## 1. What this console is for

SMD runs a **fleet of operators** — one per client, each a per-customer Machine hosting one or more personas. The admin
console is how SMD does three things:

1. **Operate the fleet** — see every operator's health, cost, and posture at a glance; catch problems across all clients.
2. **Operate any single operator** — author its configuration, set its floors, watch its runtime, manage its lifecycle.
3. **Run managed clients** — for clients in a managed posture, SMD _is_ the operator's day-to-day operator, so the
   console must support doing the operational work, not just observing it.

SMD holds **full control of every domain for every client, always** ([ADR 0041](../../adr/0041-operator-authority-posture.md)).
This console is never read-only to SMD. The per-domain authority switches only change whether a _client_ may also operate
a domain — they never restrict SMD.

---

## 2. Who uses it — SMD-internal roles (Layer 0)

Today `/admin/operator/*` is Captain-only. Managed clients change that: if SMD operates a client's operator day-to-day,
non-Captain SMD staff need scoped access. We design two SMD-internal roles now and ship Captain-only until staff exist:

| SMD role              | Can                                                                                                                                                                                    | Cannot                                                                                       |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `smd_admin` (Captain) | everything: provision, decommission, set floors, repin, edit any config, all fleet ops                                                                                                 | —                                                                                            |
| `smd_operator`        | run assigned managed clients: author non-floor config, operate runtime (review/act on the client's behalf where the engagement authored it), manage connectors, answer change-requests | provision/decommission, set or lower floors, repin Hermes, edit clients not assigned to them |

Assignment of `smd_operator` to clients is a Captain action. Every SMD action is audit-logged with the acting staff
identity (Layer-0 actor), distinct from client-side actors.

---

## 3. Information architecture

```
/admin/operator
├── (fleet home) ............... roster of all operators, health/cost/posture/alerts at a glance
├── /alerts .................... fleet-wide alert feed (sticky-stops, auth-expired, anomalies, boot failures)
├── /cost ..................... fleet cost: total COGS, per-client ratios, anomalies   (reuses today's /costs)
├── /requests ................. change-request inbox (client-requested changes in SMD-operated domains)
├── /provision ................ stand up a new operator (author customer.yaml → Machine)
└── /[customer] ............... per-operator drill-in
    ├── (overview) ............ identity, personas roster, posture, health summary, subscription
    ├── /config ............... author customer.yaml: personas, skills, scope, business hours, voice
    ├── /governance ........... entitlement ceilings + SMD-owned floors (action-class model)
    ├── /connectors ........... bindings, health, credential custody, re-consent
    ├── /runtime .............. observe: activity/drafts, matters, audit          (via runtime read path, §8)
    ├── /memory ............... observations + agent-authored skills: review/dismiss/approve
    ├── /people ............... client users + client-internal roles
    ├── /cost ................. this operator's COGS drill-down                    (reuses today's /costs/[slug])
    ├── /authority ............ the per-domain client-self-serve switches for this client
    └── /lifecycle ............ pin/resize/pause/decommission + config-history     (reuses /config-history)
```

Two altitudes: **fleet** (everything above `/[customer]`) and **per-operator** (everything under it).

---

## 4. Fleet surfaces (across many operators)

### 4.1 Fleet home — the roster

The default landing. One row per operator (per client), built for scanning a growing fleet.

| Column                 | Source                                          | Notes                                                               |
| ---------------------- | ----------------------------------------------- | ------------------------------------------------------------------- |
| Client / operator name | `customer_configs`                              | links to per-operator overview                                      |
| Personas               | `customer_configs.personas`                     | count + names (roster-of-one shows the one)                         |
| Posture                | `customer_configs.authority`                    | chip: Managed / Co-Managed / Self-Managed (derived from switch set) |
| Health                 | per-customer `fleet_status` + sticky-stop state | green/yellow/red; sticky-stop banner if SOFT/HARD                   |
| Connectors             | connector health rollup                         | n connected / m degraded                                            |
| Cost ratio             | `cost_attribution` COGS/MRR                     | basis points; flags ≥ kill criterion                                |
| Open alerts            | alert feed                                      | count by severity                                                   |
| Last activity          | runtime read path                               | last audit_log ts                                                   |

Filter/sort by health, posture, cost ratio, vertical, alert count. Search by client. This is the "is anything on fire
across all my operators" view.

### 4.2 Fleet alerts

A single feed of everything that needs SMD attention across all operators, each linking to the relevant per-operator
surface:

- **Sticky-stop** transitions (SOFT/HARD) — runaway/refusal/cost-cap circuit breaker tripped.
- **Connector auth-expired** — a connector needs re-consent (delegated mode → SMD drives it; self-held → SMD nudges client).
- **Cost anomalies** — spend spike vs baseline (reuses today's anomaly model; snooze/ack carried over).
- **Boot-check failures** — an invariant boot check failed (isolation, citation).
- **Audit-integrity drift** — D1-vs-mirror mismatch.
- **Structural-config deferred** — a client/SMD config change needs a Captain re-provision ([ADR 0019](../../adr/0019-customer-yaml-to-profile-config-translation.md)).

Each alert: severity, customer, age, ack/snooze/resolve. The existing cost-anomaly snooze/ack is the pattern; generalize
it to all alert types.

### 4.3 Fleet cost

Reuses today's `/admin/operator/costs`: total fleet COGS, per-client COGS/MRR ratio, anomalies, CSV export. This is the
SMD-only economics wall — it has **no client analog**. Add: fleet totals and a sortable ratio leaderboard (which
operators are margin-negative).

### 4.4 Change-request inbox

The receiving end of the authority model. When a client in an SMD-operated domain clicks "request a change," it lands
here. Each request: customer, domain, requested change (structured where possible — e.g. "enable skill X", "add user Y",
"raise ceiling on Z"), requester, age. SMD actions it (which performs the real change through the normal config path) or
declines with a note. This is what makes "Managed" real: the client asks, SMD does.

### 4.5 Provisioning

Stand up a new operator. Flow:

1. Pick vertical pack (seeds personas, skills, floors, connector capability set, compliance posture).
2. Author the customer.yaml essentials (identity, personas, users, connectors, scope, authority default).
3. Validate (the existing customer.yaml validator + secret-exclusion scan).
4. Commit to the configs repo (git source of truth) → CI materializes → Machine provisions.
5. Connector consent (per credential custody, [ADR 0042](../../adr/0042-operator-credential-custody.md)) — SMD-driven or
   handed to the client.
6. Activate subscription; assign client users + an `smd_operator` if managed.

Provisioning is `smd_admin`-only. The console drives the authoring + validation; the actual Machine stand-up is the
existing provisioning tooling.

---

## 5. Per-operator surfaces (individual)

### 5.1 Overview

Identity (name, vertical, customer_id), the **persona roster** (each persona: name, title, status, skill count, its own
health), posture chip + per-domain switch summary, health summary (heartbeat, sticky-stop, connector rollup, last
activity), subscription status, and quick links to every domain. The persona roster is the multi-operator surface
([foundations §3](00-foundations.md)) — built for N, shows one at v1.

### 5.2 Configuration authoring

SMD's authoring surface for the client's `customer.yaml` (the parts that aren't floors). Per persona: identity/voice
(name, title, tone, signature, send-as), skills (enable/disable, scope, cost estimate, cron schedule, bundles). Plus
customer-scope: connectors (see §5.4), scope envelope (folder visibility, keyword/domain/matter blocks), business hours,
voice library/cohorts, escalation recipients, memory retention.

Writes go through the **config write path**: edit → validate → `config_change_audit` (intent ledger) → git write-back →
CI re-materialize ([ADR 0025](../../adr/0025-autonomy-ceilings-configurable-exposure-vs-initiation.md)/[0026](../../adr/0026-config-surface-is-a-security-boundary.md)).
Non-structural edits hot-reload via the sidecar; structural edits surface a "re-provision required" state. The console
shows which class an edit is before commit.

### 5.3 Trust & governance (floors + ceilings)

The entitlement surface, on the **action-class model** ([ADR 0025](../../adr/0025-autonomy-ceilings-configurable-exposure-vs-initiation.md)):
per skill, per action class (READ / INTERNAL_WRITE / EXTERNAL_SEND / COMMITMENT / DESTRUCTIVE), the authored ceiling.
**SMD authors floors here** — a vertical/engagement floor pins a non-raisable ceiling (e.g. a regulated vertical pins
EXTERNAL_SEND ≤ draft). The surface shows, per skill × action class: the floor (SMD-set, non-raisable by the client) and
the current ceiling (within the floor).

This surface must stay clean of any imposed default: an action class with no authored ceiling shows as **unconfigured →
fail-closed**, not as "drafts for review." Ceilings are whatever the engagement authored — autonomous, drafted, gated,
or refused — and the surface renders that authored value, never a presumed one. (See [ADR 0035](../../adr/0035-no-imposed-entitlement-defaults.md);
this is the cancer the foundations doc §8 calls out — do not reintroduce it here.)

The legacy per-skill scalar `trust_ceiling` in the data layer is treated as a degraded view; this surface drives toward
the action-class map.

### 5.4 Connectors & credentials

Per connector (capability → adapter binding): status (connected / degraded / auth-expired), health probe result,
**credential custody mode** (delegated / self-held, per [ADR 0042](../../adr/0042-operator-credential-custody.md)), token
freshness, scopes. Actions: bind/unbind a connector, run a health probe, drive re-consent (delegated: SMD generates the
one-click link and routes it; self-held: SMD nudges the client and cannot complete it), rotate a delegated static
secret. The auth-expired alert (fleet §4.2) deep-links here.

### 5.5 Runtime (observe)

What the operator is actually doing, read across the isolation boundary via the **runtime read path** (§8). SMD-observe
by default; for managed clients SMD also _acts_ here (operating on the client's behalf where the engagement authored a
review surface). Sub-views:

- **Activity / drafts** — recent operator actions. Where a skill is authored to draft, the draft and its review state
  appear; where a skill is authored autonomous, the completed action appears as an activity/audit entry with no review
  surface. The surface adapts to what the entitlements produced — it never shows an empty "queue" implying a review stage
  that wasn't authored.
- **Matters / workstreams** — the operator's per-engagement aggregates (vertical-shaped).
- **Audit** — the append-only `audit_log` for this operator, filterable by persona, skill, action class, actor, date.
  Immutable; this is the read view of the compliance record.

### 5.6 Memory & agent-skills

The operator's learned state, mirrored with provenance ([ADR 0016](../../adr/0016-honcho-disposition.md)/[0017](../../adr/0017-skill-curator-disposition.md)):

- **Observations** — memory conclusions with evidence status (evidenced / unevidenced / insufficient). SMD can dismiss
  (physical removal from the learning loop) — a fleet-safety capability SMD always retains.
- **Agent-authored skills** — skills the operator wrote for itself, with provenance + Captain-approval state. SMD can
  approve or disable. The kill-switch on agent-authored skills is an SMD platform-safety capability in every posture
  ([foundations §2](00-foundations.md), fork #3 resolution).

Honcho memory inference is Phase-2 ([ADR 0016](../../adr/0016-honcho-disposition.md)); until then this surface shows the
flat-file/owned-memory state and agent-skill inventory.

### 5.7 People & access

The client's own users and their client-internal roles (the re-derived principal/staff/compliance idea). SMD can
add/remove client users and set their roles (SMD always can; the client _also_ can iff the people-access switch is on).
Seeds the first principal at onboarding.

### 5.8 Cost

Per-operator COGS drill-down (reuses today's `/costs/[slug]`): per-driver breakdown, daily timeline, COGS/MRR. SMD-only.

### 5.9 Authority

The per-domain switch panel for this client ([ADR 0041](../../adr/0041-operator-authority-posture.md)). SMD sets the
global default and flips individual domains to `client`. This is where SMD decides, when a client has settled, to hand
them self-serve control of (say) people-access or connectors. Every flip is audit-logged. Launch state: all off.

### 5.10 Lifecycle

Provision status, the fleet release (Hermes pin) the seat runs, VM size, **pause/resume**, and **decommission** (the existing
9-step idempotent pipeline). Plus config-history (reuses `/config-history`): the customer.yaml sync log with git SHAs.
`smd_admin`-only.

---

## 6. Cross-cutting

- **Audit everything.** Every SMD action (config edit, floor change, ceiling change, connector action, authority flip,
  lifecycle action, request resolution) writes an audit row with the Layer-0 staff actor. SMD actions on a client's
  operator are as audited as the client's own.
- **Empty states are honest.** Pre-runtime-read-path, runtime surfaces show real empty states, not fabricated data.
  A roster-of-one is a real state. ([foundations §7](00-foundations.md)).
- **Isolation holds.** Every per-operator runtime read is scoped to one customer; no surface joins across customers
  except the fleet roster/cost/alerts, which read only the console-side projections (`customer_configs`, fleet_status,
  cost rollups), never two Machines' runtime D1 at once.

---

## 7. The runtime read path (decided: A+B)

Each operator's runtime state (audit, activity, matters, observations) lives on that operator's own isolated Machine,
walled off from every other client's — the console cannot query a Machine's D1 directly
([ADR 0009](../../adr/0009-cross-machine-query-prohibition.md)). Both portals need a controlled way to pull that data out
for display. Two designable options:

| Option                                             | Mechanism                                                                                                      | Pros                                                                     | Cons                                                                      |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| **A. Read API on the Machine**                     | the Machine exposes a thin, authenticated read-only HTTP endpoint; the console calls it per-customer on demand | always-fresh; no duplication; isolation preserved (one Machine per call) | per-request latency; the Machine must be up; auth surface on each Machine |
| **B. Mirror to a console-side per-customer store** | the Machine pushes read-relevant rows to a console-side store keyed by customer                                | fast portal reads; survives Machine downtime; natural for fleet rollups  | duplication; staleness window; more storage                               |

**Decision (Captain, 2026-06-08): A+B.** Use **B** for fleet-rollup + summary data (health, cost, last-activity, alert
signals — already partly the `fleet_status` pattern) so the fleet view is always answerable even when a Machine is
briefly down, and **A** for deep per-operator drill-ins (full audit log, a specific draft, matter detail) so detail is
fresh on demand. Either way the read is per-customer and audited; it must never become a cross-customer join. This is the
highest-leverage shared component for both portals and is **specced and built first** — every other surface displays on
top of it. The A+B split and its isolation invariants are fixed in [ADR 0043](../../adr/0043-operator-runtime-read-path.md);
the endpoint/auth shape is an implementation detail specced with the build.

---

## 8. Build notes

**Reusable as-is (bonus):** the cost dashboard (`/costs` + `/costs/[slug]`) and its anomaly snooze/ack; the
config-history view; the `customer_configs` projection + the customer.yaml validator/secret-detector; the per-customer
`fleet_status` heartbeat.

**Net-new:** fleet roster, fleet alert feed (generalized from cost anomalies), change-request inbox, provisioning UI,
per-operator config-authoring UI, governance/floors UI (action-class), connectors+custody UI, runtime observe surfaces,
memory/agent-skills review, authority switch panel, SMD-internal roles, and the runtime read path (§7).

**Net-new ADRs (this package):** [0041](../../adr/0041-operator-authority-posture.md) (authority), [0042](../../adr/0042-operator-credential-custody.md)
(credential custody), [0043](../../adr/0043-operator-runtime-read-path.md) (runtime read path). The SMD-internal role
model (§2) likely warrants a short ADR once the approach is chosen.

**Sequence suggestion:** runtime read path (§7) → fleet roster + overview (read-only, lights up the fleet) → config
authoring + governance (the daily SMD job) → connectors/custody → memory/lifecycle → authority panel (last, since
launch keeps all switches off).

---

## 9. Resolved decisions (Captain, 2026-06-08)

1. **SMD-internal roles (§2):** **build the `smd_admin` / `smd_operator` seam now**, populated Captain-only at launch.
   No later refactor of every action's auth check.
2. **Runtime read path (§7):** **A+B** — mirror summaries for the fleet view, live per-customer reads for deep drill-ins.
   Specced and built first.
3. **Managed-client operation:** determined by the authority model — in a managed posture SMD operates the client's
   runtime under an audited SMD-staff identity; a client needing its own identity on those actions self-holds that domain
   (a per-client choice, never a default).
