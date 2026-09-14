# operator/

The v1 Operator SKU — a single product configured per customer for any vertical we sell into. Initial vertical is law-firm (personal injury); the product itself is vertical-agnostic.

## Multi-vertical doctrine

One product, many configurations. Vertical-specific content lives only in:

- `skills/` — recipes the agent runs (some are vertical-tagged, e.g. `paid-media-anomaly-watcher`; others horizontal, e.g. `inbox-triage`)
- `connectors/` — BUILD wrappers per the ADR 0020 decision table only (see "Where connector code lives" below)
- `fixtures/` — synthetic data per vertical, structured `fixtures/<vertical>/<sub-vertical>/<type>/`
- `customers/<slug>/customer.yaml` — per-customer configuration

Substrate components — agent runtime, voice layer, memory system, trust ceiling, dashboard, identity & access, operations, audit & compliance — are vertical-agnostic.

## Where connector code lives

ADR 0020 (Connector Strategy, locked 2026-05-24) governs this. Three rules:

1. **`mcp:<server>` bindings have no in-tree code.** The MCP server boots as a child process of Hermes from `mcp_servers.*` config in the per-profile `config.yaml`. M365 Mail/Calendar/Teams, QuickBooks, Xero, Stripe, HubSpot, Salesforce, Slack, ShipStation, CourtListener, Clio (`oktopeak/clio-mcp`), Twilio — all hosted or local MCPs, zero adapter code in this tree.
2. **`build:<vendor>` BUILD adapters that existed before 2026-05-24 stay in `operator/connectors/<vendor>/`** per ADR 0020's "no pre-scheduled migration" rule. Currently: `filevine/`, `lawpay/`, `no_pm/`.
3. **New BUILD adapters land in `venturecrane/hermes-smd-overlay`** as a sub-plugin (e.g., `hermes-smd-microsoft-graph` per #1055 for OneDrive/SharePoint), not in this tree. Per ADR 0015, the Hermes fork itself carries no SMD code; all SMD plugin code lives in the overlay.

## 10 product components

1. **Agent** — Hermes runtime per-customer Machine
2. **Skills** — recipes the agent runs
3. **Connectors** — how the agent reaches the customer's tools
4. **Voice** — how the agent communicates in the customer's writing style
5. **Memory** — what the agent knows about the customer's business
6. **Trust Ceiling** — autonomy per skill, plus calibration
7. **Dashboard** — human interface for configure / monitor / interact
8. **Identity & Access** — accounts, OAuth, role-gating, send-as identity
9. **Operations** — Captain-side infra (provisioning, fleet, cost, decommission)
10. **Audit & Compliance** — actions, evidence packets, immutability

## Directory layout

```
operator/
├── README.md                       # this file
├── adapter/                        # Hermes hook surface — Operator.register()
├── safety_substrate/               # citation refusal, fabrication filter, adversarial tests
├── skills/                         # canonical SKILL.md library
├── connectors/                     # BUILD wrappers — only filevine/, lawpay/, no_pm/ per ADR 0020
├── templates/                      # Dockerfile, fly.toml.template, bootstrap.sh
├── bin/                            # provision-customer.sh, pause-customer.sh, rollback-skill.sh
├── fixtures/                       # synthetic data per vertical
├── grading/                        # per-skill / per-customer test result tracking
└── customers/<slug>/
    └── customer.yaml               # per-customer config (skills enabled, connectors, trust ceiling)
```

## Provisioning a customer

```bash
operator/bin/provision-customer.sh <slug>
```

Given a populated `customers/<slug>/customer.yaml`, the script:

1. Creates the Fly app (`hermes-<slug>`)
2. Provisions persistent volume for state
3. Sets per-customer Fly secrets (Anthropic, AgentMail, Composio credentials)
4. Deploys the Hermes image with the customer's enabled skills and connectors
5. Smoke-tests the agent loop

## Customer config

`customer.yaml` is the single source of truth for one customer. Formal schema: `docs/specs/operator/customer-yaml-schema.md`. Never contains literal secret values.

## Trust ceiling

Enforced in code via the `adapter/`'s integration with Hermes' tool dispatch. Per-skill ceiling lives in `SKILL.md` frontmatter; per-customer overrides live in `customer.yaml`. Customer-level overrides win.

Ceiling values: `autonomous` (executes), `draft_for_review` (writes to drafts queue + notifies), `refused` (returns error, logs attempt).

See `docs/specs/operator/dashboard-roles.md` for the role/ceiling matrix.

## Where to look next

- Specs: `docs/specs/operator/` (capability contracts, schemas, OAuth, telemetry, decommission)
- ADRs: `docs/adr/0004-productized-operator-offering.md` and follow-ons
