---
title: Connector Strategy — MCP-First Where Vendor or Vetted-Community Server Exists, BUILD Only When No Acceptable MCP
date: 2026-05-24
status: accepted
captain: Scott Durgan
supersedes: none
related-spec: docs/specs/operator/customer-yaml-schema.md
related-issue: TBD (filed as follow-on to the locked Hermes-alignment plan dated 2026-05-24)
---

# ADR 0020 — Connector Strategy

**Status:** Accepted (Captain decision, 2026-05-24).

**Source:** The locked Hermes-alignment build plan dated 2026-05-24, §8 (Connector strategy execution). The plan section synthesized a per-vendor audit of MCP availability against our active and planned capability bindings. This ADR records the decision matrix so future connector wiring follows the rule and the rationale is recoverable per vendor.

## Context

The Operator touches the customer's external systems through eleven capability interfaces (ADR 0006 rewrite). Each capability resolves at runtime through one of the backend patterns below, distinguished by the `customer.yaml.connectors{}.backend:` prefix:

- `mcp:<server>` — Model Context Protocol server (vendor-official or vetted community)
- `build:<vendor>` — Python adapter we maintain in `operator/connectors/<vendor>/`
- `synthetic:<name>` — In-process substrate using per-customer D1+R2 (e.g., `no_pm`)
- `native:<provider>` — a Hermes-native provider surfaced without wrapping it in a connector (e.g. `native:brave-free` for web search); materialized by `_materialize_web_search` in `bootstrap/translate.py`. **Added 2026-07-13, forward-linking [ADR 0070](./0070-web-search-shared-connector-divergent-defaults.md)'s native cut** — 0070's first cut wired web search as `mcp:brave`, then replaced it with this `native:` prefix because wrapping a native Hermes feature in an MCP server was a redundant layer. This ADR originally named **three** backend patterns; `native:` is the fourth.

**Composio is dropped.** An earlier revision of this ADR reserved a fourth `composio:<connector>` backend as a long-tail fallback. As of the 2026-05-30 revision it is removed entirely: every vendor we plan to wire has a vendor-direct or vetted-community MCP, or a BUILD adapter, and we connect to MCPs directly. The `composio:` prefix is no longer an accepted backend and the per-connection runtime guard has been retired. New vendors with no first-party MCP are wired with a BUILD adapter.

The MCP ecosystem matured significantly in v0.14.0 of Hermes. The official MCP catalog at `github.com/modelcontextprotocol/servers` plus vendor-maintained servers (Microsoft `microsoft/mcp`, Intuit `intuit/quickbooks-online-mcp-server`, Twilio Labs `twilio-labs/mcp`, ShipStation `shipstation/mcp-shipstation-api`, CourtListener hosted at `mcp.courtlistener.com`) cover a substantial fraction of the vendors we expect to wire. Community MCPs cover others (`oktopeak/clio-mcp` for Clio practice management). For the rest, BUILD adapters are the option.

The decision matrix exists because connector choice for each vendor has real tradeoffs (maintainership, license, capability coverage, auth model) and we want the choice to be reasoned, recorded, and consistent across customers.

## Decision

**Vendor-direct MCP first for any system we want to connect to.** Decision order for new connector bindings:

1. **Vendor-direct MCP** — first-party, vendor-maintained, vendor-supported. Default choice when one exists.
2. **Vetted community MCP** — small, focused, securely reviewable, actively maintained (acceptance criteria below).
3. **BUILD adapter** in `operator/connectors/<vendor>/` — when no acceptable MCP exists, or when trust-ceiling enforcement is safer to own end-to-end (e.g., trust-account writes against LawPay). This is also the fallback for any long-tail vendor that has not shipped a first-party MCP.

### Per-vendor decision table

The following table is the canonical wiring for new customer.yaml authoring. Existing bindings can be migrated when the existing home creates more maintenance friction than the migration cost — no pre-scheduled migration.

| Capability                       | Vendor                                   | Decision                                                                                      | Rationale                                                                                                                                                                                                                                                                                                      |
| -------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Email                            | Microsoft 365 (Outlook)                  | `mcp:m365-mail` (hosted at `agent365.svc.cloud.microsoft`, per-tenant)                        | First-party Microsoft. Per-tenant URL: `/agents/tenants/{tenant_id}/servers/mcp_MailTools`. Auth via Microsoft Entra tenant ID — each customer firm is its own tenant.                                                                                                                                         |
| Calendar                         | Microsoft 365                            | `mcp:m365-calendar` (hosted, per-tenant)                                                      | Same hosted pattern: `/servers/mcp_CalendarTools`. Per-tenant Entra auth.                                                                                                                                                                                                                                      |
| DocumentStorage                  | OneDrive / SharePoint                    | `mcp:softeria/ms-365-mcp-server` (community, MIT)                                             | Microsoft has shipped M365 Mail/Calendar/Teams/User/Copilot Chat as first-party MCPs but NOT OneDrive/SharePoint. Migrated to the community MIT server in ADR 0021 Stream F (PR #1081). The prior `build:microsoft_graph` adapter was deleted in #1065. Revisit binding when MS ships a first-party Drive MCP. |
| Email                            | Google Workspace (Gmail)                 | `build:google-gmail` (`operator/connectors/google/crane_gmail.py`)                            | BUILD, not the Preview Google MCP. Customer-owned domain-wide delegation impersonates the Operator's Workspace user. Scopes: `gmail.modify` + `gmail.send`; trust ceilings and content floors govern use. See §"Google Workspace: BUILD, not MCP".                                                             |
| Calendar                         | Google Workspace                         | `build:google-calendar` (`operator/connectors/google/crane_calendar.py`)                      | BUILD. Scope `calendar.events`; read/write events, attendees, and explicit `sendUpdates` control. Default notification behavior is `none`; authored workflows may notify. Same Preview / no-OAuth-MCP reasons; one shared Google credential.                                                                   |
| DocumentStorage                  | Google Drive + Docs + Sheets             | `build:google-drive` (`operator/connectors/google/crane_drive.py`)                            | BUILD. Scopes `drive` + `documents` + `spreadsheets`; read/write Drive files, edit native Docs/Sheets, and share when allowed by the customer's action ceilings.                                                                                                                                               |
| PracticeManagement               | Clio                                     | `mcp:clio-oktopeak` (community, MIT)                                                          | Community MCP at `oktopeak/clio-mcp`, v2.0.0 (2026-05-23) added HTTP transport + 6 tools. Code-review per acceptance criteria before first Clio customer.                                                                                                                                                      |
| PracticeManagement               | Filevine                                 | `build:filevine`                                                                              | No acceptable MCP exists. REST + GraphQL with OAuth client-credentials; clean BUILD shape.                                                                                                                                                                                                                     |
| PracticeManagement               | CASEpeer / SmartAdvocate / Neos / MyCase | `build:<vendor>`                                                                              | Per the PI vertical adapter build priority (ADR 0014).                                                                                                                                                                                                                                                         |
| PracticeManagement               | (none, synthetic)                        | `synthetic:no_pm`                                                                             | For firms without a real PM system. D1+R2 substrate.                                                                                                                                                                                                                                                           |
| ESign                            | DocuSign                                 | `build:docusign` (prod) + `mcp:docusign-official` (**Beta**, pilot in parallel)               | DocuSign official MCP shipped Beta 2026-04-28. Hold build adapter as prod path; pilot the MCP; cut over at GA. Beta endpoint + auth model still unverified — read `developers.docusign.com/platform/mcp-server/` before binding.                                                                               |
| Accounting                       | QuickBooks Online                        | `mcp:quickbooks-intuit` (official Intuit, Apache-2.0)                                         | 144 tools, 29 entities, comprehensive coverage. Gold-standard MCP profile.                                                                                                                                                                                                                                     |
| Accounting                       | Xero                                     | `mcp:xero-official` (`@xeroapi/xero-mcp-server`, MIT, local npx)                              | First-party Xero. Per-organisation OAuth2 Custom Connections, or per-account Bearer Token mode for multi-account runtime.                                                                                                                                                                                      |
| Payments                         | LawPay / 8am.com                         | `build:lawpay`                                                                                | No acceptable MCP. Trust-ceiling enforcement (no refund tool, no trust-ledger modification) baked into our wrapper rather than relying on a community maintainer.                                                                                                                                              |
| Payments                         | Stripe                                   | `mcp:stripe-official` (`mcp.stripe.com` remote, or `@stripe/mcp` local)                       | First-party Stripe. Per-customer OAuth (remote) or per-customer Restricted API Keys (recommended for autonomous agents). Trust-ceiling enforcement continues via plugin layer.                                                                                                                                 |
| Fulfillment                      | ShipStation                              | `mcp:shipstation-official` _when needed_                                                      | LICENSE clarification required before binding any customer; defer until a customer needs ShipStation.                                                                                                                                                                                                          |
| CallTracking                     | Twilio Voice/SMS                         | `mcp:twilio-labs` when needed (Apache-2.0)                                                    | Twilio Labs official; 1,800 endpoints.                                                                                                                                                                                                                                                                         |
| CallTracking                     | CallRail                                 | `build:callrail` if needed                                                                    | No acceptable MCP; defer until a customer needs CallRail.                                                                                                                                                                                                                                                      |
| CourtAccess                      | CourtListener / PACER                    | `mcp:courtlistener` (hosted)                                                                  | Official Free Law Project; hosted; OAuth with Dynamic Client Registration.                                                                                                                                                                                                                                     |
| IntakeCRM                        | HubSpot                                  | `mcp:hubspot-official` (`mcp.hubspot.com`)                                                    | First-party HubSpot. GA 2026-04-13. OAuth 2.1 + PKCE per customer. Read + write across CRM objects; activity objects blocked when sensitive-data flag enabled.                                                                                                                                                 |
| IntakeCRM                        | Salesforce                               | `mcp:salesforce-hosted` (Salesforce-managed endpoint)                                         | First-party Salesforce. GA April 2026. Per-org OAuth via External Client App (`mcp_api` + `refresh_token` scopes + PKCE). **Enterprise Edition+ only.** Exact endpoint URL not in blog — verify before binding.                                                                                                |
| InternalComms                    | Slack                                    | `mcp:slack-official` (`mcp.slack.com/mcp`, plugin config in `slackapi/slack-mcp-plugin`, MIT) | First-party Slack hosted MCP. Per-workspace OAuth. **Workspace admin must approve MCP integration first** — add to customer onboarding checklist.                                                                                                                                                              |
| InternalComms                    | Teams                                    | `mcp:m365-teams` (hosted at `agent365.svc.cloud.microsoft`, per-tenant)                       | First-party Microsoft. URL pattern: `/servers/mcp_TeamsServer`. Per-tenant Entra auth.                                                                                                                                                                                                                         |
| PracticeManagement (real-estate) | Dotloop                                  | `build:dotloop`                                                                               | No acceptable MCP.                                                                                                                                                                                                                                                                                             |

### Google Workspace: BUILD, not MCP (amended 2026-06-02)

> **SUPERSEDED 2026-06-17 by [ADR 0045](0045-mediated-connector-capability-broker.md).** The
> `build:google-*` connector CLIs described in this section (and the three `build:google-*` table
> rows above) were retired. Google Workspace (Gmail / Calendar / Drive / Docs / Sheets) now runs
> through the ADR 0045 **Workspace broker**: the broker process holds the DWD service-account
> credential and exposes governed `workspace_*` tools, so the agent never holds the Google
> credential and there is no connector CLI to `execute_code` against. Google is **not** modeled as
> a `connectors[]` entry — it is declared by `customer.yaml.google_auth:` and served by the broker.
> The `/opt/data/oauth/google.json` path and `operator/connectors/google/` CLIs referenced below no
> longer exist. The reasoning below is retained as the historical record of the BUILD decision that
> the broker replaced.

The three Google rows above were `mcp:google-*` in the original table. They are now `build:` adapters (`operator/connectors/google/crane_{gmail,calendar,drive}.py`), decided during the SMD Services connector session. Three reasons, in order of weight:

1. **Customer-owned Workspace authority.** The hard security boundary is the customer-owned Google Workspace delegation, not the fact that these are wrapper CLIs. For standard Workspace customers, `customer.yaml.google_auth.mode=dwd` points the Machine at a customer service account authorized through domain-wide delegation and impersonating the Operator's Workspace user. The Fly Machine is that user's computer; `execute_code` can call Google at the granted scopes. The BUILD adapters provide audited, ergonomic operations while trust ceilings and content floors govern use.
2. **Preview, not GA.** Google's hosted Gmail/Calendar/Drive MCPs are Workspace **Developer Preview**, and the overlay's `_materialize_mcp_servers` wires only static header-key MCPs — there is **no OAuth-MCP wiring path**. Declaring `mcp:google-*` was inert (silently skipped at boot).
3. **One owned credential path.** Gmail, Calendar, Drive, Docs, and Sheets ride the same credential file at `/opt/data/oauth/google.json` through shared `_google_auth.py`. User-OAuth remains a legacy path for narrow tests and non-DWD tenants; the standard Workspace path is DWD with `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_IMPERSONATE_SUBJECT`, and `GOOGLE_OAUTH_SCOPES`.

These are CLIs the agent shells to via `execute_code` (ADR 0021), not `ctx.register_tool()`-registered tools — so no `build:` runtime materialization is required in the overlay (the binaries ship in the Machine image at `/app/connectors/google/`). Revisit the MCP option if/when Google's Workspace MCPs reach GA _and_ the overlay grows an OAuth-MCP wiring path.

### Acceptance criteria for community MCPs

Before binding a paying customer to a community-maintained MCP server, the overlay maintainer (Captain or designated reviewer):

1. Reads the README and confirms a documented OAuth or API-key auth flow.
2. Reviews the server's source code for: (a) absence of credential-logging patterns, (b) absence of arbitrary-shell-execution in tool handlers, (c) reasonable error handling, (d) license clarity.
3. Confirms maturity signals: star count, recent commit activity, open-issue triage signs.
4. Runs the server's test suite if it has one.

For `oktopeak/clio-mcp` (already chosen for Clio), the review is light — 26 tools, ABA Opinion 512 alignment in the README, OAuth 2.0 + AES-256-GCM token store. Captain confirmed during the audit; review captured in connector docs.

### Customer.yaml backend resolution at boot

Per ADR 0019 (customer.yaml → per-profile config translation), the bootstrap CLI resolves backend prefixes at startup. **Only the `mcp:` path is wired today** (`translate._materialize_mcp_servers`, per `operator/contracts/customer-yaml-blocks.yaml`); the `build:`/`synthetic:` paths below are PLANNED, built demand-pull through vertical-one per [ADR 0038](./0038-operator-vertical-delivery-method.md):

- `mcp:<server>` → writes `mcp_servers.<server>` entry in the per-profile Hermes config; the MCP server boots as a child process of Hermes per Hermes' MCP integration. **(Implemented.)**
- `build:<vendor>` → **PLANNED.** The intended path is for the `hermes-smd-trust` plugin (or a dedicated per-vendor sub-plugin in the overlay) to instantiate the Python adapter at plugin init and register its tools via `ctx.register_tool()`. No materializer registers BUILD tools yet; the adapters live in `hermes-smd-overlay` and do not exist yet (ADR 0038 §Context).
- `synthetic:<name>` → **PLANNED.** The intended path wires the synthetic substrate's Hermes-registered tools with per-customer D1+R2 bindings via env vars. No materializer registers synthetic tools yet — a `synthetic:no_pm` binding surfaces no PM tools at runtime.

## Alternatives Considered

### Pattern 1: BUILD-everything

Build Python adapters for every vendor we wire.

**Rejected.** Maintenance scales with vendors; vendor API changes hit us; we'd duplicate work the official MCP vendors are already doing better.

### Pattern 2: MCP-only

Refuse to BUILD anything; require an MCP for every vendor.

**Rejected.** Several vendors (Filevine, LawPay, Dotloop) have no acceptable MCP. Refusing to wire them blocks customers. We wire them with BUILD adapters instead.

### Pattern 3: Decision matrix per vendor (this decision)

Selected. Each vendor's wiring is a reasoned choice from the three backend options. The default is vendor-direct MCP-first; deviations are documented.

### Pattern 4: Broker vendor connections through Composio

An earlier revision reserved a `composio:` backend to broker vendor connections through Composio's tenant-wide API.

**Rejected.** Brokering through Composio adds three costs without commensurate benefit: (a) shared-key tenancy risk that requires a per-connection runtime guard, (b) an additional API surface to maintain, (c) a third-party in the trust chain visible to compliance-audited customers. Vendor-direct MCPs use per-customer OAuth or per-customer API keys as the isolation primitive — its own boundary, matching ADR 0010's per-customer secret storage. Vendors with no first-party MCP are wired with BUILD adapters, which avoid the shared-key risk entirely. Composio is dropped.

## Consequences

**Positive.**

- New customer onboarding is mostly a `customer.yaml` edit, not a code change. MCP-bound capabilities require no SMD code at all.
- Maintenance burden is bounded: only BUILD adapters are ours to maintain end-to-end. MCP server maintenance is the vendor's (or upstream community's) problem.
- Per-customer isolation is preserved across all backend patterns. MCP servers run as per-Machine subprocesses; BUILD adapters live in the per-Machine container; synthetic substrates use per-customer D1+R2.
- Capability-disclosure metadata (the "what the Operator used to write this" sourcing block per ADR 0006 rewrite) works uniformly across backends because the capability-conformance metadata is per-tool, not per-backend.

**Negative / accepted.**

- Community MCPs (Clio for now) require human review before binding paying customers. The review is small (a few hours per MCP) but it's real per-vendor work.
- BUILD adapters carry vendor-API-drift risk that doesn't apply to MCP servers (where the maintainer handles drift). We accept this cost where no MCP exists.

## Verification

1. **The `customer.yaml` validator accepts and resolves the three backend prefixes** (`mcp:`, `build:`, `synthetic:`). Unknown prefixes — including the retired `composio:` — fail validation. **[Amended 2026-11-09: four prefixes are accepted. `native:` is the fourth, as the Decision section above records; the list is `ACCEPTED_BACKEND_PREFIXES` in `src/lib/operator/customer-yaml/types.ts`.]**
2. **The bootstrap CLI generates correct per-profile config** for each backend type. Smoke tests against `_template` customers with one of each backend produce working Hermes configs.
3. **MCP server bindings produce working tool registrations** at Hermes startup. The first agent turn after boot can call a tool from each configured MCP server. `mcp:` is the only backend with a runtime materializer today — `connectors{}` is materialized solely by `translate._materialize_mcp_servers` (`operator/contracts/customer-yaml-blocks.yaml`).
4. **PLANNED — not yet wired.** BUILD adapter tool registration via `ctx.register_tool()` in `hermes-smd-trust` or per-vendor sub-plugins, dispatching tool calls to the Python adapter and back, is the target contract. No `build:` (or `synthetic:`) runtime materializer exists yet: the bootstrap translator wires `mcp_servers` only, and the §"Customer.yaml backend resolution at boot" mapping for `build:`/`synthetic:` above describes intended, not yet-implemented, behavior. Per [ADR 0038](./0038-operator-vertical-delivery-method.md) §Context, "There are no per-vertical skill bodies and no `build:` adapters … [a prospect] cannot yet be served by one"; the `build:` adapters live in `hermes-smd-overlay` (ADR 0038 §Consequences) and are built demand-pull through vertical-one.

## References

- Locked Hermes-alignment build plan dated 2026-05-24, §8 (Connector strategy execution)
- [`microsoft/mcp`](https://github.com/microsoft/mcp) — Microsoft's catalog of first-party MCP servers (Azure + Fabric implementations live here; M365 servers are hosted at `agent365.svc.cloud.microsoft` and listed in this catalog's "Microsoft 365" section)
- [Microsoft 365 first-party MCP servers](https://github.com/microsoft/mcp#-which-mcp-servers-are-available-from-microsoft) — Mail, Calendar, Teams, User, Copilot Chat available; OneDrive/SharePoint NOT yet shipped
- [Google Cloud — Google-managed MCP servers GA announcement](https://cloud.google.com/blog/products/ai-machine-learning/google-managed-mcp-servers-are-available-for-everyone)
- [Gmail MCP server configuration guide](https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server) — endpoint, OAuth scopes, Preview status
- [HubSpot Remote MCP GA changelog](https://developers.hubspot.com/changelog/remote-hubspot-mcp-server-is-now-generally-available) — GA 2026-04-13
- [Stripe MCP docs](https://docs.stripe.com/mcp) — remote `mcp.stripe.com` + local `@stripe/mcp`
- [Salesforce Hosted MCP GA blog](https://developer.salesforce.com/blogs/2026/04/salesforce-hosted-mcp-servers-are-now-generally-available) — Enterprise Edition+
- [`XeroAPI/xero-mcp-server`](https://github.com/XeroAPI/xero-mcp-server) — first-party Xero MCP (MIT)
- [`slackapi/slack-mcp-plugin`](https://github.com/slackapi/slack-mcp-plugin) — first-party Slack MCP plugin config; hosted server at `mcp.slack.com/mcp`
- [DocuSign MCP Server (Beta)](https://developers.docusign.com/platform/mcp-server/) — endpoint + auth details require manual read
- [`intuit/quickbooks-online-mcp-server`](https://github.com/intuit/quickbooks-online-mcp-server) — official Intuit MCP
- [`oktopeak/clio-mcp`](https://github.com/oktopeak/clio-mcp) — community Clio MCP
- [`twilio-labs/mcp`](https://github.com/twilio-labs/mcp) — Twilio Labs MCP
- [`shipstation/mcp-shipstation-api`](https://github.com/shipstation/mcp-shipstation-api) — ShipStation MCP (license unconfirmed)
- [CourtListener MCP](https://mcp.courtlistener.com)
- [ADR 0006 (rewrite)](./0006-capability-adapter-pattern.md) — backend prefix model
- [ADR 0015 (rewrite)](./0015-hermes-fork-vs-upstream.md) — plugin-only overlay; the runtime registration happens in plugins
- [ADR 0019](./0019-customer-yaml-to-profile-config-translation.md) — backend resolution at boot
