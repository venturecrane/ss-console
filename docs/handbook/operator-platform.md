---
title: Operator Platform Architecture
section: product
order: 1
summary: How the Operator is built - the Hermes substrate, the plugin-only overlay, and the per-customer Machine that isolates one customer's runtime from every other
sources:
  - label: Operator Architecture (CLAUDE.md)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: ADR 0015 - Hermes fork posture
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md
  - label: ADR 0007 - Per-customer Machine isolation
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0007-per-customer-machine-isolation.md
  - label: ADR 0010 - Per-customer OAuth token storage
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0010-per-customer-oauth-token-storage.md
  - label: ADR 0011 - Multi-persona per customer
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0011-multi-persona-per-customer.md
  - label: ADR 0019 - customer.yaml to profile translation
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0019-customer-yaml-to-profile-config-translation.md
  - label: ADR 0083 - Authorship model and output classes (format amendment 2026-08-19)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0083-authorship-model-output-classes.md
---

## What the Operator runs on

The Operator is not a system we wrote from scratch. It runs on the **NousResearch Hermes Agent** runtime (MIT-licensed), and the code SMD owns is a thin **plugin-only overlay** that extends Hermes through its documented extension surface. This is the single most important fact about the platform: Hermes is the substrate, and almost everything an agent does - the LLM loop, skills, memory, the tool registry, approval and guardrail machinery, MCP integration - is native Hermes, not SMD code. The architecture was substantially realigned on 2026-05-24 after six rounds of focused research, and that realignment is what this page describes.

Three principles govern all Operator work. They are not aspirational; they decide what gets built and where the code lives.

1. **Hermes is the substrate. Trust it.** Skills, memory, the tool registry, the plugin hook surface, MCP integration, and approval machinery are native and not reinvented. Teknium's (the Nous Research lead) May 2026 hard rule applies: plugins MUST NOT modify Hermes core files (per ADR 0015, citing upstream `AGENTS.md`).
2. **Build only what Hermes won't.** Sample-driven voice transformation, compliance-grade audit emission, content-class trust ceilings, configurable send-posture routing, curated vertical skill catalogs, and the customer-facing business surface are what we build on top of Hermes. None are on its roadmap.
3. **Mirror, don't gate.** Where Hermes' learning loop creates state, the overlay captures a parallel record in per-customer D1 with provenance, so the Captain can inspect and reverse it. No approval queue stands between the agent and its work; safety is enforced by authored entitlement ceilings (see `/admin/playbook/autonomy-governance`), not by an interposed gate.

## The fork posture: pin, don't patch

SMD does not maintain a modified Hermes. Per ADR 0015 (the plugin-only half, which stands; the fork half was superseded by ADR 0024), SMD pins Hermes at a specific upstream tag and SHA and runs it byte-for-byte. All SMD-specific code lives in a separate, open-source, MIT-licensed repo: **`venturecrane/hermes-smd-overlay`**.

Why this matters: Hermes ships at high velocity (ADR 0015 cites 459+ PRs per week and 13 minor releases in 10 weeks). A hard fork would impose prohibitive maintenance cost on a single-Captain venture. By keeping all our code in the overlay and never touching core, a Hermes upgrade becomes a tag-bump exercise rather than a code-merge exercise, and our rebase cost approaches zero. The overlay ships as a small set of narrow Hermes plugins (audit, trust, voice, memory-mirror) plus a `shared/` support package and a `bootstrap/` CLI, each registered against Hermes' documented lifecycle hooks (`pre_tool_call`, `pre_llm_call`, `transform_tool_result`, `on_session_start`, `on_session_end`, `pre_gateway_dispatch`, and others).

The proprietary value is deliberately not in the hook code. It is in the SMD backend: the audit database, voice training, the admin console, and customer onboarding. Open-sourcing the overlay costs us nothing and signals craft (ADR 0015).

## One Machine per customer

Per ADR 0007, each customer gets a dedicated **Fly.io Machine** named `hermes-{customer-slug}`. There is no shared runtime across customers. Multi-tenancy is achieved through deployment isolation, not runtime tenancy.

This is a deliberate trade against the standard SaaS pattern. For an Operator working inside a regulated practice (the first vertical is personal-injury law, but the principle holds for every regulated vertical the platform will ship into), the cost of cross-customer data leakage is unbounded: privilege breach, bar discipline, an existential lawsuit. So the isolation guarantee is placed in the deployment topology rather than in application code. Customer A's Machine has no network or storage path to Customer B's data. Cross-customer access is not "denied by code" - it is architecturally impossible (ADR 0007).

Each customer's Machine carries:

- Dedicated D1, R2, and Vectorize bindings, all namespaced to the customer (`hermes-{slug}-d1`, `hermes-{slug}-r2`, etc.).
- A pinned content-hash SHA of the Hermes runtime - updates do not propagate without an explicit Captain re-pin.
- Its own audit log, memory artifact, and dashboard.

A **boot-time invariant** enforces this: at Machine boot, the runtime verifies its storage bindings include only its own customer's namespaces and refuses to start if it detects a binding outside its namespace (safety invariant #7; spec in `safety-invariants.md`). The accepted cost is that operational complexity scales linearly with customer count - the control plane must provision, monitor, update, and decommission one Machine per customer, and each Machine carries a baseline cost even when idle (ADR 0007).

## OAuth tokens live on the customer's volume

A customer's own OAuth tokens (Gmail, Microsoft Graph, QuickBooks, Clio, and so on) are stored on that customer's per-Machine Fly volume at `/opt/data/oauth/<provider>.json`, not in Infisical and not in any shared store (ADR 0010). The on-disk shape follows each provider's client-library format - for Google, the google-auth authorized-user JSON (ADR 0010, as amended by ADR 0036).

The distinction is data ownership: SMD owns the Anthropic API key and other shared infrastructure secrets, which stay in Infisical and are pushed to each Machine's Fly secrets at provision time. The customer owns their Gmail OAuth token, which never leaves their Machine. Token files are `0600`, owned by the `hermes` user, never logged, and every read emits an `oauth.token_read` audit event carrying provider and scopes but never the token value. Decommissioning a customer deletes the volume and therefore the tokens, satisfying right-to-be-forgotten without a separate cleanup step (ADR 0010).

## A persona is a Hermes profile

When a customer wants more than one AI identity - say an inbox-triage operator and a separate intake handler running against the same firm's connectors and memory but with distinct signature blocks and skill assignments - that is modeled as **multiple Hermes profiles**, not multiple Machines and not an in-process dispatcher (ADR 0011). A persona maps cleanly onto a Hermes profile: each profile has its own `HERMES_HOME`, `SOUL.md` identity file, `config.yaml`, skill catalog, and memory peer card. Profiles are Hermes' native unit of identity isolation.

v1 ships at `personas[]` length 1; the validator enforces this until a v2 unlock, which is a validator change rather than an architecture pivot. Persona switching mid-session uses Hermes' native `/handoff` command. The decision explicitly does not spawn a Machine per persona (that would multiply cost and fracture the shared-memory story) and does not modify Hermes core to run personas concurrently in one session (Hermes does not support this and is not building it) (ADR 0011).

## How customer.yaml becomes a running profile

The bridge between the SMD product surface and the Hermes runtime is a translation step. SMD authors a `customer.yaml` (storage and authoring covered in `/admin/playbook/knowledge-memory`), which speaks the product's vocabulary - customer, persona, connector, entitlement. Hermes speaks a different vocabulary - profile, model, MCP server, personality file. The `hermes-smd bootstrap` CLI in the overlay performs that translation at Machine startup (ADR 0019).

For each persona, bootstrap creates the profile directory, writes `SOUL.md` (identity, tone, escalation rules) and `config.yaml` (model pin, memory config, MCP server bindings from `connectors{}`, the four overlay plugins enabled with their per-plugin config), and symlinks the enabled skills into the profile. The translation is deterministic (same input produces byte-identical output) and idempotent (re-running it changes nothing). A `customer-sync` sidecar polls R2 for config changes; non-structural changes (escalation contacts, entitlement edits such as persona exposure and skill initiation, scope edits) reload without a restart, while structural changes (adding a persona, swapping a connector backend) are logged for a Captain re-provision so that OAuth tokens on the volume are never disturbed (ADR 0019).

## Documents come back in the firm's format, and the format lives in Word

A drafting skill files work product as a real Word document through the Smokeball connector (`mcp_smokeball_render_docx_draft` with a `document_class`), and the connector renders the content into the firm's own Word template for that class when the firm's Document Library holds one (resolved deterministically from `self_initiation.document_library` in `customer.yaml`: the library matter by number, the folder by name, the file by the class's name), else onto a Times New Roman starter with the named styles defined. Letterhead, page setup, fonts, spacing, item labels, headings, caption tables, and page numbers are the renderer's; the model writes content only, including the labels' own numbers, the caption, the signature block, and the proof of service, and the renderer adds no text. Typography is not config: the firm edits a style in Word and the next draft follows, with no publish and no reboot. The establishment turn files a starter template per class for the firm to edit, or the firm drops its own letterhead into the folder under the class's file name (ADR 0083, 2026-08-19 amendment; `operator/connectors/smokeball/smokeball_connector/docx_format.py`, `library.py`).

## The system, not a feature

The platform is best understood as three compounding layers, and the moat is the combination, never any one piece (ADR 0037, Tenet 4):

- **The harness** - configurable trust enforced in code: what the operator may do, who it may contact, what requires escalation.
- **The guide** - the human who authors `customer.yaml` well for a specific business, plus the authored knowledge itself.
- **The memory** - the per-customer operating memory that deepens over time and raises switching cost.

Calling any single feature "the moat" - voice fidelity, audit, draft-for-review send - is a category error. Competitors will have configurable agents; they will not easily have the guide or the accumulated memory. The autonomy and trust machinery is detailed in `/admin/playbook/autonomy-governance`; the three knowledge lanes in `/admin/playbook/knowledge-memory`; the connector and channel surface in `/admin/playbook/connectors-channels`.
