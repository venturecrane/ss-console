# Operator context (auto-loads when working under operator/)

Moved from the root CLAUDE.md 2026-08-09 so non-Operator sessions do not pay
for it every turn. For Operator strategy discussions that touch no file under
operator/, load this file explicitly.

## The Operator Thesis (load first — [ADR 0037](../docs/adr/0037-operator-thesis.md))

The canonical frame for what the Operator _is_. Load this before any Operator strategy, marketing, competitive, or vertical-selection work, so it is built upon, not re-derived.

1. **Competes with a hire, not with software. (mission-critical)** Every system does a subset; the human is the connective tissue between them, and the Operator is that human. Incumbent systems (Clio, the AMS, the PSA) are **connection targets, not competitors**; more disconnected systems = more value; price against a **salary**, not a software seat.
2. **A configurable substrate, not a tool with a use case.** No fixed function; authored per engagement across skills, entitlements (initiation × exposure), voice, connectors, memory. The only hard limit is connectability — if we can connect, we can work with it.
3. **No imposed defaults.** Unconfigured is fail-closed (a safety state), not an identity. Ask "what did the engagement author?", never "what does the system assume?"
4. **The moat is the harness + the guide + the memory — never a single feature.** Not voice, not audit, not draft-for-review. Calling one feature "the moat" is a category error.
5. **Packs turn the universal into the recognizable.** "All things to all people" is the capability; "exactly your thing" is the package. Packs compose and cluster into families that compound. The magnitude is the strategy; the pack is the entry.
6. **Targeting is market-driven, on reachability × willingness-to-pay.** Pick verticals where the coordinator role is most acute/expensive, most cheaply reachable, highest-paying (vs a salary). The guide is a resource we supply, not a constraint on which market to pick.

## Operator Architecture (locked 2026-05-24)

The Phase 1 Operator SKU (productized retainer offering, per ADR 0004) runs as a per-customer Fly.io Machine hosting the Nous Research Hermes Agent runtime (`NousResearch/hermes-agent`, MIT). The architectural posture was substantially realigned on 2026-05-24 after six rounds of focused research. Three principles govern all Operator work:

1. **Hermes is the substrate. Trust it.** Skills, the flat-file memory core (`MEMORY.md`/`USER.md`), the Curator, profiles, the tool registry, the plugin hook surface, MCP integration, and approval/guardrail machinery are all native and not reinvented. Teknium's May 2026 hard rule applies: plugins MUST NOT modify Hermes core files. Our overlay is plugin code, hosted in a separate repo (`venturecrane/hermes-smd-overlay`). **Honcho is NOT deployed** — the 2026-05-30 revision of ADR 0016 deferred it to Phase 2 (demand-gated) after the first real boot exposed the in-container integration as fictional; Phase-1 seats run in-session flat-file memory only.
2. **Build only what Hermes won't.** Sample-driven voice transformation, compliance-grade audit emission, content-class trust ceilings, configurable send-posture routing (draft-for-review among the authored options), curated vertical skill catalogs, and the customer-facing business surface are what we build on top of Hermes — none are on its roadmap. (These are capabilities, not the moat: the moat is the harness + the guide + the memory, per [ADR 0037](../docs/adr/0037-operator-thesis.md) Tenet 4. No single feature is the moat.)
3. **Mirror, don't gate.** Where Hermes' learning loop creates state (today: agent-authored skills; Honcho conclusions if/when Phase 2 activates it), our overlay captures a parallel record in per-customer D1 with provenance. Captain dismissal physically removes the state from Hermes. No approval queue stands between the agent and its work; safety is enforced by the authored entitlement ceilings (fail-closed when unauthored), not by an interposed gate.

Load these ADRs before any Operator architectural work:

- **ADR 0037** — The Operator Thesis (what it is / competes with a hire / configurable substrate / no defaults / moat = harness+guide+memory / packs / market-driven targeting) — load first
- **ADR 0004** — Productized Operator offering (the SKU itself)
- **ADR 0006** — Capability-adapter pattern (typed contracts as TS-side ergonomic; runtime via plugin + MCP)
- **ADR 0007** — Per-customer Machine isolation
- **ADR 0010** — Per-customer OAuth token storage on Fly volume
- **ADR 0011** — Multi-persona per customer (persona = Hermes profile)
- **ADR 0012** — customer.yaml storage (Git source of truth → D1+R2 materialized)
- **ADR 0015** — Hermes fork posture (pin-only fork, plugin-only overlay)
- **ADR 0016** — Honcho disposition (revised 2026-05-30: **deferred to Phase 2, demand-gated** — Phase 1 = flat-file memory core, no Honcho on any seat; the mirror/dismiss/TTL machinery is the Phase-2 shape)
- **ADR 0017** — Skill Curator disposition (disable autonomous curator per-customer; keep in-conversation `skill_manage`; mirror to D1 inventory; supervised `--dry-run` consolidation only)
- **ADR 0019** — customer.yaml → per-profile config translation
- **ADR 0020** — Connector strategy (MCP-first; BUILD only where no acceptable MCP)
- **ADR 0021** — Leverage Hermes native primitives (`execute_code`, `delegate_task`, no-agent cron, skill bundles, webhook gateway via `pre_gateway_dispatch`, MCP-first connector retirement)

Connectors are wired by `customer.yaml.connectors{}` backend prefix: `mcp:` (vendor or vetted-community MCP server), `build:` (Python adapter we maintain), `synthetic:` (no_pm substrate). Composio is dropped (ADR 0020, 2026-05-30 revision) — we connect to MCPs directly, and long-tail vendors with no first-party MCP get a `build:` adapter.

The 2026-05-24 realignment burial is complete. Removed: `smd.hooks.*` dual-surface scaffolding, Honcho interceptor, Curator interceptor, GEPA boot-check (ADR 0018 superseded), in-tree YAML validator, the pre-realignment MS Graph adapter, and the `clio/` / `dotloop/` / `shipstation/` connector dirs whose MCP-first decisions superseded them. Author-built connectors we must write ourselves (no vendor/community MCP exists) are MCP servers living in `operator/connectors/` in this tree, per ADR 0053 — Smokeball is the first. The overlay repo (`venturecrane/hermes-smd-overlay`) stays substrate-only.

## Never run a second hermes runtime beside a live gateway (the one-shot rule)

**Do not run `hermes -p <profile> ...` one-shots, `hermes chat`, or any other
full hermes runtime on a seat whose gateway is up.** A seat is 1 vCPU / 1GB
(`machine:` in its `customer.yaml`); a second runtime loads the same plugin set
and competes for the same single core with the process that is answering clients.

Use instead:

- **broker-socket calls** for anything the workspace broker already exposes, or
- **a scheduled in-gateway turn** (Hermes' own cron) for anything that needs the
  agent, so the work runs on the runtime that is already loaded.

**Why this is a rule and not a preference.** On 2026-09-01 rehearsal one-shots on
`pilot-smokeball` tipped the gateway into a crash loop that then **sustained
itself for two and a half hours with no external load at all**. The gateway's
startup resolves every platform plugin with synchronous imports on the event
loop — about four minutes at 1GB — while hermes' own in-process watchdog
hard-exits 75 after roughly two minutes of missed probes. Once startup crosses
that budget the loop is: exit 75, container replaced, colder caches, slower
startup, repeat. `oom_killed` was false throughout; the extra load only had to
push startup past the line once. Full write-up, including the captured thread
dump and the upstream ask:
`docs/runbooks/operator/incidents/2026-09-01-gateway-startup-watchdog-collision.md`.

The seat cannot defend itself here. The entrypoint supervisor deliberately will
not restart a slow-starting gateway (killing one is what sustained the loop), and
the watchdog budget is not configurable at the pin we run. The only control is
not starting the second runtime.

## Probe artifacts in a tenant (ss #2403 — the 28745d01 lesson)

Any rehearsal, self-test, or kill-test that writes an artifact into a customer
or pilot tenant (a Smokeball task, memo, event, or file) follows this contract.
It exists because a rehearsal probe task outlived its test on the pilot: the
2026-08-14 digest flagged it as "a machine-authored probe task; its own note
instructs deletion after witnessing", and 37 minutes later the verification
chase cited that same task as its live tracking anchor.

1. **Stamp at creation.** The subject starts with `[SMD-PROBE <ISO-8601 UTC
creation stamp>]`, e.g. `[SMD-PROBE 2026-08-18T17:00Z] drafting prove-out`.
   The connector's `[Operator]` provenance stamp may precede it. The stamp is
   deliberately subject-visible: firm staff reading the task list are the other
   consumer that can mistake a probe for real work. (Before first use on a new
   tenant, create-and-read-back one stamped task — this vendor has form for
   munging text, e.g. names truncate at the first period.)
2. **Tear down in the same session.** A probe artifact is deleted — for tasks,
   completed via `update_task(is_completed=True, staff_id=<owner>)`; the
   connector has no task delete, and Smokeball's task PUT is a full replace
   that requires `StaffId` + `CompletedByStaffId` (proven live 2026-08-31,
   vfy_01M1CWACT2NSB1WFSZXD3KQK5F) — before the session reports its test done,
   with a negative probe (gone-means-gone rule 2). "Its note says to delete it
   later" is the anti-pattern this contract replaces.
3. **Ingestion is fenced either way.** `list_tasks` drops probe-marked rows by
   default and counts the drop (`probeArtifactsExcluded`); the tracker/chaser
   pre_run pulls exclude them too. The match is position-anchored — only a
   subject that STARTS with the marker is a probe — so real work cannot be
   hidden by quoting it. Never widen the match.
