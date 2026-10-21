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
  - label: ADR 0009 - Cross-machine query prohibition
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0009-cross-machine-query-prohibition.md
  - label: ADR 0044 - R2-authoritative live reconfig
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0044-r2-authoritative-live-reconfig.md
  - label: ADR 0062 - Operator cost plane
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0062-operator-cost-plane.md
  - label: ADR 0083 - Authorship model and output classes (format amendment 2026-08-19)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0083-authorship-model-output-classes.md
---

## What the Operator runs on

The Operator is not a system we wrote from scratch. It runs on the **NousResearch Hermes Agent** runtime (MIT-licensed), and the code SMD owns is a thin **plugin-only overlay** that extends Hermes through its documented extension surface. This is the single most important fact about the platform: Hermes is the substrate, and almost everything an agent does - the LLM loop, skills, memory, the tool registry, approval and guardrail machinery, MCP integration - is native Hermes, not SMD code. The architecture was substantially realigned on 2026-05-24 after six rounds of focused research, and that realignment is what this page describes.

Three principles govern all Operator work. They are not aspirational; they decide what gets built and where the code lives.

1. **Hermes is the substrate. Trust it.** Skills, memory, the tool registry, the plugin hook surface, MCP integration, and approval machinery are native and not reinvented. Teknium's (the Nous Research lead) May 2026 hard rule applies: plugins MUST NOT modify Hermes core files (per ADR 0015, citing upstream `AGENTS.md`).
2. **Build only what Hermes won't.** Sample-driven voice transformation, compliance-grade audit emission, content-class trust ceilings, configurable send-posture routing, curated vertical skill catalogs, and the customer-facing business surface are what we build on top of Hermes. None are on its roadmap.
3. **Mirror, don't gate.** Where Hermes' learning loop creates state, the overlay captures a parallel record with provenance in Machine-local SQLite on the seat's own Fly volume (ADR 0062 records that the per-customer D1 originally named here was never built), so the Captain can inspect and reverse it. No approval queue stands between the agent and its work; safety is enforced by authored entitlement ceilings (see `/admin/playbook/autonomy-governance`), not by an interposed gate.

## The fork posture: pin, don't patch

SMD does not maintain a modified Hermes. Per ADR 0015 (the plugin-only half, which stands; the fork half was superseded by ADR 0024), SMD pins Hermes at a specific upstream tag and SHA and runs it byte-for-byte. The pin lives once, as `ARG HERMES_UPSTREAM_TAG` / `HERMES_UPSTREAM_SHA` in `operator/templates/Dockerfile`, and every seat runs the shared fleet image built from it; the per-seat `hermes_ref` field was retired from `customer.yaml` in #3332 and the validator now rejects it. All SMD-specific code lives in a separate, open-source, MIT-licensed repo: **`venturecrane/hermes-smd-overlay`**.

Why this matters: Hermes ships at high velocity (ADR 0015 cites 459+ PRs per week and 13 minor releases in 10 weeks). A hard fork would impose prohibitive maintenance cost on a single-Captain venture. By keeping all our code in the overlay and never touching core, a Hermes upgrade becomes a tag-bump exercise rather than a code-merge exercise, and our rebase cost approaches zero. The overlay ships as a set of narrow Hermes plugins (28 under `plugins/` on the overlay's main branch, among them audit, trust, voice, memory-mirror, peer-memory, corrections, inbound, drafting, and record-store) plus a `shared/` support package, a `bootstrap/` CLI, and the root-owned `config_applier/`. Each plugin registers against Hermes' documented lifecycle hooks (`pre_tool_call`, `pre_llm_call`, `transform_tool_result`, `on_session_start`, `on_session_end`, `pre_gateway_dispatch`, and others).

The proprietary value is deliberately not in the hook code. It is in the SMD backend: the audit database, voice training, the admin console, and customer onboarding. Open-sourcing the overlay costs us nothing and signals craft (ADR 0015).

## One Machine per customer

Per ADR 0007, each customer gets a dedicated **Fly.io Machine** named `hermes-{customer-slug}`. There is no shared runtime across customers. Multi-tenancy is achieved through deployment isolation, not runtime tenancy.

This is a deliberate trade against the standard SaaS pattern. For an Operator working inside a regulated practice (the first vertical is personal-injury law, but the principle holds for every regulated vertical the platform will ship into), the cost of cross-customer data leakage is unbounded: privilege breach, bar discipline, an existential lawsuit. So the isolation guarantee is placed in the deployment topology rather than in application code. Customer A's Machine has no network or storage path to Customer B's data. Cross-customer access is not "denied by code" - it is architecturally impossible (ADR 0007).

Each customer's Machine carries:

- Its own Fly volume at `/opt/data`, which holds the seat's state as Machine-local SQLite (the broker-owned audit ledger at `/opt/data/audit/audit.db`, the agent-state database) plus OAuth tokens and profile homes. The per-customer D1 and Vectorize bindings (`hermes-{slug}-d1` and so on) in the original design were never built: no provisioning code creates them and none exist in the account (`operator/migrations/README.md`; ADR 0062).
- A per-customer R2 bucket for agent-authored skill bodies, `ss-operator-{slug}-skills`, created by `operator/bin/provision-customer.sh`. Its `customer.yaml` lives in the shared `smd-customer-config` bucket under a per-slug key, and the nightly audit export goes to the shared, object-locked `smd-audit-archive` bucket under `audit/<slug>/` (`operator/bin/audit-chain-watch.py`).
- The shared fleet image, so a Hermes upgrade reaches every seat through one Dockerfile pin rather than a per-seat re-pin.

A **boot-time invariant** enforces this: as the last root gate before the privilege drop, `operator/templates/entrypoint.sh` runs `operator/safety_substrate/invariants/invariant_7.py`, which checks that the skill-bodies bucket, the config bucket, and the on-volume SQLite paths all resolve to this seat's own slug and volume, and exits 3 (refusing to boot) on a violation or if the module is missing (safety invariant #7; ADR 0009, wired 2026-07-13 under SEC-22). The accepted cost is that operational complexity scales linearly with customer count - the control plane must provision, monitor, update, and decommission one Machine per customer, and each Machine carries a baseline cost even when idle (ADR 0007).

## OAuth tokens live on the customer's volume

A customer's own OAuth tokens (Gmail, Microsoft Graph, QuickBooks, Clio, and so on) are stored on that customer's per-Machine Fly volume at `/opt/data/oauth/<provider>.json`, not in Infisical and not in any shared store (ADR 0010). The on-disk shape follows each provider's client-library format - for Google, the google-auth authorized-user JSON (ADR 0010, as amended by ADR 0036).

The distinction is data ownership: SMD owns the Anthropic API key and other shared infrastructure secrets, which stay in Infisical and are pushed to each Machine's Fly secrets at provision time. The customer owns their Gmail OAuth token, which never leaves their Machine. Token files are `0600`, owned by the `hermes` user, never logged, and every read emits an `oauth.token_read` audit event carrying provider and scopes but never the token value. Decommissioning a customer deletes the volume and therefore the tokens, satisfying right-to-be-forgotten without a separate cleanup step (ADR 0010).

## A persona is a Hermes profile

When a customer wants more than one AI identity - say an inbox-triage operator and a separate intake handler running against the same firm's connectors and memory but with distinct signature blocks and skill assignments - that is modeled as **multiple Hermes profiles**, not multiple Machines and not an in-process dispatcher (ADR 0011). A persona maps cleanly onto a Hermes profile: each profile has its own `HERMES_HOME`, `SOUL.md` identity file, `config.yaml`, skill catalog, and memory peer card. Profiles are Hermes' native unit of identity isolation.

v1 ships at `personas[]` length 1; the validator enforces this until a v2 unlock, which is a validator change rather than an architecture pivot. Persona switching mid-session uses Hermes' native `/handoff` command. The decision explicitly does not spawn a Machine per persona (that would multiply cost and fracture the shared-memory story) and does not modify Hermes core to run personas concurrently in one session (Hermes does not support this and is not building it) (ADR 0011).

## How customer.yaml becomes a running profile

The bridge between the SMD product surface and the Hermes runtime is a translation step. SMD authors a `customer.yaml` (storage and authoring covered in `/admin/playbook/knowledge-memory`), which speaks the product's vocabulary - customer, persona, connector, entitlement. Hermes speaks a different vocabulary - profile, model, MCP server, personality file. The `hermes-smd bootstrap` CLI in the overlay performs that translation at Machine startup (ADR 0019).

For each persona, bootstrap creates the profile directory, writes `SOUL.md` (identity, tone, escalation rules) and `config.yaml` (model pin, memory config, MCP server bindings from `connectors{}`, the overlay plugins enabled with their per-plugin config), and symlinks the enabled skills into the profile. The translation is deterministic (same input produces byte-identical output) and idempotent (re-running it changes nothing). After boot, changes reach the running seat through the overlay's root-owned `config_applier`: a merged `customer.yaml` is published to R2 by the deploy workflow (`.github/workflows/deploy.yml`), and the applier polls that object every 15 seconds by default, validates it, and applies only fields on its live-writable allow-list (entitlement ceilings and similar), with no reboot (ADR 0044; overlay `config_applier/__main__.py`). Rebuild-class fields are rejected on the live path and take effect at the next boot or re-provision, so OAuth tokens on the volume are never disturbed (ADR 0019). The `customer-sync` sidecar the original design named is a Phase-2 stub (`operator/bin/sync-customer-yaml.sh`).

## Documents come back in the firm's format, and the format lives in Word

A drafting skill files work product as a real Word document through the Smokeball connector (`mcp_smokeball_render_docx_draft` with a `document_class`), and the connector renders the content into the firm's own Word template for that class when the firm's Document Library holds one (resolved deterministically from `self_initiation.document_library` in `customer.yaml`: the library matter by number, the folder by name, the file by the class's name), else onto a Times New Roman starter with the named styles defined. Letterhead, page setup, fonts, spacing, item labels, headings, caption tables, and page numbers are the renderer's; the model writes content only, including the labels' own numbers, the caption, the signature block, and the proof of service, and the renderer adds no text. Typography is not config: the firm edits a style in Word and the next draft follows, with no publish and no reboot. The establishment turn (`operator/skills/document-library-establishment/SKILL.md`) hunts for a firm-authored exemplar of each of the six classes, reports every class as found, not found, or not applicable on the admin's word, and files a class template only from exemplars of that class, onto the starter for the firm to edit, or the firm drops its own letterhead into the folder under the class's file name. The letterhead on a `letter` or `demand_letter` is never model text: the firm's own template file for the class supplies it when one resolves (a class template the Operator filed from the SMD starter, recognized by the marker the starter writes into its document properties or by its named styles, is not the firm's file for this purpose and takes the authored letterhead while its header is empty), else the renderer prints the firm's identity authored once in `customer.yaml` (`firm_identity`: name, street, city/state/zip, telephone, fax, website; only the name is required and an absent field is an omitted line) into the starter's first-page header, where it survives into every later draft rendered into that template, and else the report says there is no letterhead; nothing is ever markered in its place. Memos carry none, and a pleading's firm block stays content beneath the per-document attorney line (`operator/connectors/smokeball/smokeball_connector/letterhead.py`). A firm document that is none of the six (a retainer, an intake form) can be filed as an additional reference template under its own name; it is never a format base, and the render tool refuses a classless template under a class's name. The template gate also refuses a `{{FILL}}` marker that does not name its source (ADR 0083, 2026-08-19 amendment; `operator/connectors/smokeball/smokeball_connector/docx_format.py`, `library.py`).

## Spreadsheets are read as cells and built in code

The Operator reads an Excel workbook (.xlsx) the way it reads any other document: `read_document` on a matter and `read_attachment_text` on an emailed attachment return each sheet as a `## Sheet:` block with one row per line. A formula the file never computed reads as `[formula, no saved value]`, never as a blank that could pass for zero. Legacy .xls files, encrypted Office files, and PowerPoint decks are refused by name. It builds a workbook with `mcp_smokeball_add_workbook`: the model supplies typed columns and rows as data, and the connector writes the file, sums any `totals` columns itself, reads every cell back against the input before uploading, and files it on the matter. A text cell is always text, so a value beginning `=` is never a formula. Every workbook it builds is stamped as the Operator's, and the drafting record check skips those, so the Operator's own figures never count as the firm's record. A spreadsheet is readable but is never staged as a vendor invoice (`operator/connectors/smokeball/smokeball_connector/xlsx_io.py`, `workbook_tools.py`).

## The system, not a feature

The platform is best understood as three compounding layers, and the moat is the combination, never any one piece (ADR 0037, Tenet 4):

- **The harness** - configurable trust enforced in code: what the operator may do, who it may contact, what requires escalation.
- **The guide** - the human who authors `customer.yaml` well for a specific business, plus the authored knowledge itself.
- **The memory** - the per-customer operating memory that deepens over time and raises switching cost.

Calling any single feature "the moat" - voice fidelity, audit, draft-for-review send - is a category error. Competitors will have configurable agents; they will not easily have the guide or the accumulated memory. The autonomy and trust machinery is detailed in `/admin/playbook/autonomy-governance`; the three knowledge lanes in `/admin/playbook/knowledge-memory`; the connector and channel surface in `/admin/playbook/connectors-channels`.
