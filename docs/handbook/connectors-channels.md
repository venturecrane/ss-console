---
title: Connectors & Channels
section: product
order: 4
summary: How the Operator reaches a customer's systems (connectors) and how people reach the Operator (channels) - and why both sides treat the channel as a dumb pipe
sources:
  - label: ADR 0020 - Connector Strategy (MCP-first; Composio dropped)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0020-connector-strategy.md
  - label: ADR 0021 - Leverage Hermes Native Primitives
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0021-leverage-hermes-native-primitives.md
  - label: ADR 0045 - Mediated Connector Capability Broker
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0045-mediated-connector-capability-broker.md
  - label: ADR 0053 - Author-built MCP connectors, per-customer installed
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0053-author-built-mcp-connectors-per-customer-installed.md
  - label: ADR 0057 - Operator Claude-connector access model
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0057-operator-claude-connector-access-model.md
  - label: ADR 0078 - Client-custody email channel
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0078-client-custody-email-channel.md
  - label: operator/README.md - connector code location
    href: https://github.com/venturecrane/ss-console/blob/main/operator/README.md
  - label: operator/connectors/README.md - the author-built connector contract
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/README.md
  - label: msgraph-mail manifest
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/msgraph-mail/manifest.toml
  - label: msgraph-mail staff_mailboxes.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/msgraph-mail/msgraph_mail_connector/staff_mailboxes.py
  - label: Smokeball manifest
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/smokeball/manifest.toml
  - label: Smokeball letter_tools.py (the scanned-post pipeline)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/smokeball/smokeball_connector/letter_tools.py
  - label: Smokeball letterhead.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/connectors/smokeball/smokeball_connector/letterhead.py
---

## Two sides of the same boundary

The Operator sits between a customer's systems and the people who work with it. Reaching the customer's systems is the **connector** problem: email, calendar, practice-management, accounting, payments, document storage. Reaching the Operator is the **channel** problem: how a human (or an inbound event) gets a request to the worker.

Both sides follow one rule that is easy to get wrong: **a channel is a dumb pipe; the worker holds the intelligence.** The same worker - same memory, same skills, same entitlements, same governance - answers whether you reach it by email, by text, by voice, or by a conversational MCP connection. There is no per-channel brain. The only way to break a channel is to narrow what the worker can do when a request arrives through it. See `/admin/playbook/operator-platform` for what the worker is and `/admin/playbook/autonomy-governance` for how its actions are bounded.

## Connector strategy: MCP-first

Connectors are governed by [ADR 0020](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0020-connector-strategy.md), locked 2026-05-24. Every system the Operator touches resolves at runtime through one of three backend patterns, distinguished by the `customer.yaml.connectors{}.backend:` prefix:

- **`mcp:<server>`** - a Model Context Protocol server, vendor-official or vetted community. The MCP server boots as a child process of Hermes from the per-profile config; **there is no in-tree code** for an `mcp:` binding. Examples: M365 Mail/Calendar/Teams, QuickBooks (Intuit, 144 tools), Xero, Stripe, HubSpot, Salesforce, Slack, CourtListener, Clio (`oktopeak/clio-mcp`), Twilio.
- **`build:<vendor>`** - a Python adapter we maintain, reserved by ADR 0020 for a vendor with no acceptable MCP. No `build:` adapter exists today: the pre-realignment adapter directories are gone from `operator/connectors/`, and when we did have to write a connector ourselves, [ADR 0053](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0053-author-built-mcp-connectors-per-customer-installed.md) made it an MCP server rather than a `build:` adapter (next section).
- **`synthetic:<name>`** - an in-process substrate backed by per-customer D1 and R2 (for example `no_pm`, for a firm with no real practice-management system).

The decision order for a new binding is: vendor-direct MCP first, then a vetted community MCP (subject to a code-review acceptance checklist in ADR 0020), then one we author ourselves only when neither exists. The per-vendor decision table in ADR 0020 records the reasoned choice for each vendor we expect to wire.

### Author-built connectors: MCP servers we write

When no acceptable MCP exists, we write one. [ADR 0053](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0053-author-built-mcp-connectors-per-customer-installed.md) (accepted 2026-06-22, extends ADR 0020) settles the shape: the connector is a Python stdio **MCP server** in `operator/connectors/<name>/`, baked into the image in its own hash-locked venv, and **inert until a customer binds it** with `backend: mcp:<name>`. An unbound connector is never launched, surfaces no tools, and receives no secrets. The overlay stays substrate-only, so adding a connector one firm needs never forces an overlay release. The declarative contract (package, `manifest.toml`, conformance test, overlay registration, binding) is in [operator/connectors/README.md](https://github.com/venturecrane/ss-console/blob/main/operator/connectors/README.md).

Every tool is classified twice on purpose: the manifest's `tool_classes` table is the conformance oracle, and the overlay's `shared/action_classes.py` is the enforced authority. A tool that is in one and not the other fails conformance or fails closed at boot, so a new tool is a two-repo change reviewed on both sides.

Two vendor connectors live here today, plus a synthetic self-test (`_reference`) that proves every rail, including refusal of an unclassified tool:

- **`mcp:smokeball`** - the law vertical's practice-management system of record, and the first author-built connector. Matters, contacts, tasks, events, documents, memos, and billing reads; task, event, folder, and document writes; `create_matter` as a commitment and `delete_file` as destructive. The manifest's default auth is `client_credentials`; a firm-delegated seat selects `authorization_code` in its `customer.yaml`, and that consent lands on the customer's Machine ([ADR 0054](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0054-machine-hosted-oauth-callback.md)), never on shared infrastructure.
- **`mcp:msgraph-mail`** - Microsoft Graph mail, app-only, and the first provider adapter behind the client-custody email seam of [ADR 0078](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0078-client-custody-email-channel.md): the Operator's mailbox lives on the firm's own mail system. Every write and send is pinned in code to the one Operator mailbox named in `MSGRAPH_MAILBOX` and takes no mailbox argument; tenant-side, an Exchange `ApplicationAccessPolicy` restricts the app to that mailbox as well. There is deliberately no delete tool.

#### Reading a staff member's mailbox

A firm can let the Operator **read** a named staff member's mailbox, so it can find a letter that person received without their forwarding it. The two tools, `list_staff_messages` and `read_staff_message`, are the only msgraph-mail tools that take a mailbox, and they refuse, before any Graph call, every address not listed in the seat's `customer.yaml` `staff_mailbox_reads` block (`staff_mailboxes.py`). A missing, empty, or malformed block refuses everything. The firm must also add the mailbox to the read app's `ApplicationAccessPolicy` scope group; either one missing and the read fails. Nothing can send, draft, move, or delete in a staff mailbox. The permission is the firm's to give, so each address is authored in a reviewed change, never inferred.

A rostered sender can also ask the Operator to **file an email on a matter**: it spools the message as an `.eml`, resolves the matter from the sender's own words only (never from words inside the email), files it with `file_attachment_to_matter`, and replies once saying where it went. Smokeball's Emails tab cannot be written from outside its desktop app, so the filed `.eml` in Documents is the filed copy.

Sending *as* a staff member is a different act with its own decision: [ADR 0089](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0089-staff-send-as-on-approval.md) routes it through the workspace broker and allows it only on that staff member's emailed approval of the exact draft. See `/admin/playbook/autonomy-governance`.

#### The scanned daily post

Firms scan the day's post as **one PDF holding several letters for several matters** and email it in. Filing it whole would put one client's letter on another client's matter, which the filing turn cannot undo (`delete_file` is destructive and taint-gated). So the Smokeball connector reads and files it by page (`letter_tools.py`, `letter_pages.py`):

- `read_attachment_pages` returns page-marked text, deciding per page whether the page needs transcription, so a letter is a checkable **range of page numbers** rather than a span of prose.
- `file_attachment_pages_to_matter` cuts one range out and files it as its own document. It refuses without a live resolution token for that exact matter, and refuses a page the process already filed from the same bundle.
- The `combined-post-intake` skill holds the fail-closed hinge: if the pages cannot be partitioned into letters contiguously and completely, **nothing** from the bundle is filed. A letter it cannot place comes back named with a candidate matter, and the sender's reply naming the matter is what files it.

#### The firm's letterhead

Letters and demand letters rendered on the starter base carry the firm's letterhead, printed by tool code from the `firm_identity` block the firm authors in `customer.yaml` (`letterhead.py`). The model never types it, so it never meets the content gate that would refuse its street number and phone digits. The firm's own Word template wins untouched; with no template and no authored identity, the document carries no letterhead and says so, rather than inventing one.

### The connector inventory

Generated from `operator/connectors/*/manifest.toml` and each package's `*_tools.py` modules. A change that adds, removes, or reclassifies a tool, or adds a tools module, fails `tests/handbook-integrity.test.ts` until this block is regenerated, which puts the author on this page in the same PR.

<!-- BEGIN GENERATED: connector inventory. Regenerate with `npm run handbook:connectors -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->

**`mcp:reference`** (`operator/connectors/_reference/`): capability `Reference`, manifest auth default `static`, 2 tools.

- internal_write: `record`
- read: `echo`
- tool modules: none (tools register in server.py)

**`mcp:msgraph-mail`** (`operator/connectors/msgraph-mail/`): capability `Email`, manifest auth default `client_credentials`, 8 tools.

- external_send: `send_message`, `reply_message`
- internal_write: `create_draft`
- read: `list_messages`, `read_message`, `poll_delta`, `list_staff_messages`, `read_staff_message`
- tool modules: none (tools register in server.py)

**`mcp:smokeball`** (`operator/connectors/smokeball/`): capability `PracticeManagement`, manifest auth default `client_credentials`, 48 tools.

- commitment: `create_matter`
- destructive: `delete_file`
- internal_write: `create_task`, `update_task`, `create_event`, `update_event`, `create_event_reminder`, `create_folder`, `add_file`, `file_attachment_to_matter`, `render_docx_template`, `render_docx_draft`, `stage_vendor_invoice`, `file_attachment_pages_to_matter`, `create_webhook_subscription`, `create_memo`
- read: `auth_status`, `list_matters`, `get_matter`, `list_matter_types`, `get_stage_sets`, `get_stage_to_matter_mappings`, `get_contacts`, `get_contact`, `get_contact_relations`, `list_tasks`, `get_task`, `list_events`, `search_staff`, `get_staff`, `get_roles_on_matter`, `get_relationships_on_matter`, `get_files_on_matter`, `get_file`, `get_download_url`, `read_document`, `list_folders`, `get_memos_on_matter`, `get_bank_accounts`, `get_matter_balances`, `get_matter_billing_config`, `get_fees`, `get_expenses`, `read_attachment_text`, `resolve_invoice_matter`, `read_attachment_pages`, `get_webhook_subscriptions`, `get_event_types`
- tool modules: `smokeball_connector/attachment_tools.py`, `smokeball_connector/letter_tools.py`, `smokeball_connector/vendor_invoice_tools.py`

<!-- END GENERATED: connector inventory -->

### Composio is dropped

An earlier revision of ADR 0020 reserved a fourth `composio:<connector>` backend as a long-tail fallback. As of the 2026-05-30 revision it is **removed entirely**. Brokering connections through a shared-key third party added tenancy risk, an extra API surface, and a party in the trust chain visible to compliance-audited customers, all without commensurate benefit. We connect to MCPs directly, and any long-tail vendor with no first-party MCP gets a `build:` adapter. The `composio:` prefix now fails validation.

### What is wired today vs. planned

Only the `mcp:` path has a runtime materializer today. At boot, `translate._materialize_mcp_servers` writes the `mcp_servers.*` entries into the per-profile Hermes config (`operator/contracts/customer-yaml-blocks.yaml`). The `build:` and `synthetic:` paths are **planned** - their runtime tool-registration bridge does not exist yet, so a `synthetic:no_pm` binding surfaces zero PM tools at runtime. These are built demand-pull through the first vertical per ADR 0038. A binding in `customer.yaml` is aspirational until the runtime actually materializes it.

> TODO(why): ADR 0020's verification section says BUILD-adapter tool registration via `ctx.register_tool()` in the overlay is the target contract, but the adapters "do not exist yet." The handbook should be re-checked once the first `build:` adapter ships in `hermes-smd-overlay`, because the "only mcp: is wired" statement here will then be stale. Checked: ADR 0020 §"Customer.yaml backend resolution at boot" and §Verification; operator/README.md.

### Google is a special case: the broker, not a connector

Google Workspace (Gmail, Calendar, Drive, Docs, Sheets) was originally wired as `build:google-*` CLI adapters. Those were **superseded 2026-06-17 by [ADR 0045](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0045-mediated-connector-capability-broker.md)**, the Mediated Connector Capability Broker. Google now runs through a **Workspace broker**: a separate process holds the domain-wide-delegation service-account credential and exposes governed `workspace_*` tools. The agent never holds the Google credential, and there is no connector CLI for it to shell to. Google is therefore **not** a `connectors[]` entry - it is declared by `customer.yaml.google_auth:` and served by the broker. The broker is the authorization boundary for every Google operation; see `/admin/playbook/autonomy-governance` for how that boundary enforces entitlements.

## Leverage Hermes native primitives

[ADR 0021](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0021-leverage-hermes-native-primitives.md) records the decision to use what Hermes already ships rather than reinvent it. Five native primitives matter to connectors and channels:

- **`execute_code`** - a Python child process with RPC tool access. Batch-loop skills (inbox triage, hours reconciliation, status assembly) that would otherwise make 10 to 100 tool calls in the conversation context collapse into a single inference call. The CLI connectors the agent shells to (where they still exist) are reached this way.
- **No-agent cron (`{"wakeAgent": false}`)** - a cron pre-run script can do arithmetic-only polling and skip waking the LLM when nothing changed, costing zero model tokens on quiet days. The pre-run script must emit an audit row on every run (including the silent path), or the optimization is indistinguishable from "the script silently broke."
- **`delegate_task`** - spawns up to three concurrent isolated subagents with restricted toolsets, returning only summaries to the parent. Compound research workflows parallelize their sub-tasks this way.
- **Skill bundles** - compose multiple skills under one slash command, declared in `customer.yaml`.
- **Webhook gateway (`pre_gateway_dispatch`)** - routes inbound system events (a matter created, a mailbox change) to skill invocations, so known event sources do not need polling.

The point of ADR 0021 is restraint: no new architectural primitives, no modifications to Hermes core. Every change maps to a documented Hermes capability.

## Channels: how people reach the Operator

A channel carries a message in and a message out. It holds none of the worker's intelligence. The channels the Operator supports:

- **Inbound email** - `crane@smd.services`, allow-list gated. Only senders on the authored allow-list can reach the worker by email; everything else is dropped. The inbound path routes the message body to the worker through the gateway.
- **Outbound Gmail push** - event-driven outbound, so the Operator can send (under whatever send-posture the engagement authored) rather than only reply when polled.
- **Voice** - a voice-synthesis backend plus a transform hook, so the Operator can speak in the customer's authored voice. Voice is a separate concern from the worker's personality; the channel renders, it does not decide.
- **Conversational MCP channel** - an MCP connection (the Claude custom connector) supporting multi-turn conversation. This is the "just talk to it" front door: one verb, the worker on the other end. Access follows the ADR 0057 model: Clerk per-user OAuth proves who is talking, but authorization is a row in the `mcp_issued_grants` table, read live on every request - the instant kill switch. An explicit revoke cuts access on the next call, and every grant carries a bounded `expires_at` (there is no forever grant). Who may connect is authored per firm in `customer.yaml` (`allowlist` by default, or verified firm-domain JIT under `open`). The §4 screening-attestation gate was ripped out by the 2026-06-29 amendment: access fails closed on authorization, never on paperwork. Per the 2026-07-02 console-sole amendment, the Machine's direct public `/mcp` door is closed (410 Gone); every Claude request flows through the console's `ask_operator` sync-proxy, which checks the grant per request and forwards the turn to the Machine's authenticated `/mcp/turn` endpoint. The authored entitlements still govern what the worker will do once a caller is through.

### The managed-mailbox capability

Beyond reaching the Operator's own mailbox, the Operator can **manage a principal's mailbox** - read, triage, and draft against a human's inbox - through a per-operation delegation subject. The broker is the authorization boundary: it validates the requested subject and sender against the mailboxes the engagement authored in `customer.yaml.google_auth.managed_mailboxes` before any operation runs. This is the same broker that serves Google generally (ADR 0045); the managed-mailbox path is one set of governed `workspace_*` operations on top of it.

> TODO(why): the managed-mailbox runtime materialization (whether the broker's `workspace_*` tools are live against a real managed mailbox end to end) is verified in source/ADR but not re-confirmed against a running Machine here. Checked: ADR 0045 header and overview; operator/README.md. A live-Machine check belongs to whoever next reprovisions.

## Why the dumb-pipe rule is load-bearing

If channels held intelligence, every new front door would mean re-implementing memory, entitlements, and governance, and the four doors would drift apart. Keeping the worker as the single seat of intelligence means a capability authored once is reachable through every pipe, and a governance ceiling set once binds every pipe. The connector side mirrors this: the boundary that matters is per-customer credential isolation (per-customer OAuth or API keys, the broker for Google), not which backend pattern a vendor happens to use. Both sides push all the judgment into the worker and keep the edges dumb on purpose.
