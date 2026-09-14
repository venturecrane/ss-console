# Hosted Agent Substrate Audit — 2026-07-31

Read-only. Repos: `ss-console` worktree `sos-2026-07-31`, `~/dev/hermes-smd-overlay`.
**Hermes core is NOT checked out locally** (`~/dev/hermes-agent` absent; only an editable
install of the overlay itself in `.venv`). Every Hermes-native claim below is inferred
from our contracts/classification tables, not read from Hermes source. Flagged where it matters.

---

## 1. THE CONSOLE-TO-MACHINE SEAM (`/mcp/turn`)

### Topology

Two hops. Claude (or any MCP client) → **console** `smd.services/api/operator/<slug>/mcp`
→ **Machine** `https://hermes-<slug>.fly.dev/mcp/turn` → Hermes gateway loopback
`/webhooks/mcp` → agent turn → result file → gate long-poll → reply.

The Machine's direct public `/mcp` door is **retired**, returns `410 Gone`
(`webhook_gate.py:1308-1317`). ADR 0057 amendment 2026-07-02 — one public Claude door,
because the old Machine door never read the grant table so the kill switch didn't hold.

### Hop 1 — console (`src/lib/operator/mcp/`)

- Transport: JSON-RPC 2.0 over HTTP POST. `dispatchMcpRequest` handles
  `initialize` / `notifications/initialized` / `ping` / `tools/list` / `tools/call`
  (`mcp-handler.ts:65-95`).
- Capabilities advertised: `{ tools: { listChanged: false } }` (`mcp-handler.ts:73`).
  **No `sampling`, no `logging`, no streaming capability.**
- Auth: Clerk per-user OAuth bearer (`token-validation.ts`), then a **per-request**
  grant-table read (`mcp_issued_grants`, `revoked_at`/`expires_at` SQL-filtered).
  Revoke cuts on the next call. Open-policy JIT minting at route egress
  (`mcp-route.ts:5-7`, ADR 0057 §2/§3, slices 2a-2e).
- CORS: `Access-Control-Allow-Origin: *`, methods `POST, OPTIONS`
  (`mcp-route.ts:16-22`). **A browser can call this cross-origin.**
- Response: single `Response` object with a JSON body. No SSE, no chunked writer,
  no `ReadableStream` anywhere in the route.

### Hop 2 — Machine (`webhook_gate.py`)

Request (`webhook-transport.ts:93-99`, `webhook_gate.py:796-820`):

```
POST {base}/mcp/turn
Authorization: Bearer <WEBHOOK_SECRET_MCP>          # = HMAC-SHA256(master, slug)
X-Tenant-Slug: <slug>
{ message: string, thread_id?: string, principal_subject: string,
  from_email: string, from_profile: string }
```

Response:

```
200 { reply: string, thread_id?: string }
400 { error: "message (non-empty string) required" | "principal_subject required" | ... }
401 { error: "unauthorized" }                       # bad bearer
413 { error: "payload too large" }                  # > 1 MiB (webhook_gate.py:73)
503 { error: "mcp turn route not configured" }      # WEBHOOK_SECRET_MCP unset
503 { error: "operator paused", detail: "cost breaker hard stop (sticky_stop)" }
504 { error: "turn_timeout" }
```

The Machine **trusts the console's asserted `principal_subject`** and never re-derives
identity (`webhook_gate.py:1155-1172`). The console is the party that authenticated.

### The turn spine (`_drive_agent_turn`, `webhook_gate.py:675-747`) — the critical mechanics

1. Mint `correlation_id = uuid4().hex`. It doubles as `X-Request-ID` (Hermes' dedup key),
   so it **must** be unique per call — which is why it cannot also be the conversation key.
2. Sign body with `HMAC-SHA256(body, WEBHOOK_SECRET_MCP)`, POST to the Hermes gateway's
   loopback `/webhooks/mcp` (`http.client.HTTPConnection`, 30s connect timeout).
3. Hermes' webhook adapter is **fire-and-forget**: it spawns the agent turn as a background
   task and returns `202` immediately. There is no built-in request→turn→response path
   (`shared/mcp_result_store.py` docstring, citing `gateway/platforms/webhook.py` L570-583).
4. The overlay bridges the gap: the `hermes-smd-mcp-result-sink` plugin captures the
   completed turn's answer in its **`post_llm_call`** hook and `put()`s it into a file store
   keyed by correlation id (`plugins/hermes-smd-mcp-result-sink/__init__.py:88`).
5. The gate **long-polls** that store: `_MCP_POLL_TIMEOUT_S = 55.0`,
   `_MCP_POLL_INTERVAL_S = 0.25` (`webhook_gate.py:604-605, 735-745`). Blocking
   `time.sleep`. Timeout → `None` → 504.

### Continuity

`mcp_thread_store` (`shared/mcp_thread_store.py`) — the gate renders the recent transcript
into the next turn's prompt. Thread key = `hash(clerk_subject):thread_id`, so one principal
can never read another's thread. **This is a prompt-injection workaround, not native session
memory**: the Hermes webhook adapter sets `chat_id = webhook:{route}:{delivery_id}` where
`delivery_id` is the forced-unique dedup key, so every webhook turn is its own Hermes
session by design. Fixing that is a core change, forbidden by ADR 0015.

### VERDICT: can a browser drive a live conversation through this?

**A turn-based conversation, yes. A streaming one, no — not without new work.**

- It is a **single synchronous request/response**. No token streaming exists at any of the
  three hops. The reply is produced only after `post_llm_call` fires, i.e. after the whole
  turn completes.
- The **55s Machine-side poll budget is the hard wall**. Anything longer 504s. A Cloudflare
  Worker has no wall-clock cap on an HTTP-triggered request (per the ADR), so the console
  will wait — but the Machine gives up at 55s.
- CORS is already `*`, and the console route is a plain JSON POST, so a browser can call it
  today given a Clerk token and a live grant.
- No per-turn cancel. `job_cancel`/`job_status` exist in the JSON-RPC engine but are
  **no longer publicly routed** (ADR 0057 amendment, "Deferred (follow-up)").

---

## 2. WHAT TOOLS DOES A SEAT HAVE

### The authoritative surface is a fail-closed allowlist

`hermes-smd-overlay/shared/action_classes.py` maps **176 tool names** to an `ActionClass`.
`classify_tool` returns `REFUSED` for any name not in the map — "**Unknown / unmapped tool
— fail-closed terminal class, never executes**" (`action_classes.py:73`, and the OP-P0-1
comment at :512-523). So this map IS the ceiling on the tool surface, for both products.
The `hermes-smd-trust` plugin enforces it at `pre_tool_call`
(`plugins/hermes-smd-trust/__init__.py:504`).

### Categories in the map

| Category                                       | Count (approx) | Examples                                                                                                                                                              |
| ---------------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hermes-native orientation reads                | 5              | `read_file`, `search_files`, `skills_list`, `skill_view`, `session_search`                                                                                            |
| Hermes-native high-capability (CODE_EXECUTION) | 7              | `execute_code`, `terminal`, `process`, `delegate_task`, `computer_use`, `cronjob`, `skill_manage`                                                                     |
| Hermes-native misc                             | ~8             | `web_search`, `vision_analyze`, `todo`, `clarify`, `write_file`, `patch`                                                                                              |
| Memory                                         | 3              | `memory_search`, `memory_get_rule`, `memory_list_rules`                                                                                                               |
| Generic capability-adapter tools               | ~25            | `email_*`, `calendar_*`, `sms_*`, `practice_management_*`                                                                                                             |
| AgentMail MCP                                  | 24             | `mcp_agentmail_*`                                                                                                                                                     |
| Smokeball MCP                                  | 40             | `mcp_smokeball_*`                                                                                                                                                     |
| Clio MCP                                       | 22             | `mcp_clio_oktopeak_*`                                                                                                                                                 |
| MS Graph MCP                                   | 6              | `mcp_msgraph_*`                                                                                                                                                       |
| Google Workspace (broker-mediated)             | 18             | `workspace_gmail_*`, `workspace_drive_*`, `workspace_docs_*`, `workspace_sheets_*`, `workspace_calendar_*`                                                            |
| Overlay plugin tools                           | 8              | `start_background_job`, `job_status`, `job_cancel`, `job_record_sideeffect`, `escalation_append`, `escalation_state`, `record_peer_preference`, plus voice-gate reads |
| Reference/self-test                            | 2              | `mcp_reference_echo`, `mcp_reference_record`                                                                                                                          |

**Crucial caveat: the map is a classification table, not an availability manifest.** A tool
is present on a seat only if something registers it. Registration paths:

- Hermes-native tools: always (assumed — cannot verify without Hermes source).
- MCP connector tools: only when the connector is authored in `customer.yaml.connectors{}`
  with an `mcp:` backend and the credential is staged.
- Overlay plugin tools: `register_wrapped_tool(..., requires_env=[...])`
  (`shared/tool_registration.py:27-60`) — Hermes gates on the env var.

### What the Hosted Agent actually gets

Traced from `operator/customers/scott/customer.yaml` — the **real** founding Hosted Agent
seat (`seat.product: hosted-agent`, ADR 0067), not just the template:

- `connectors.Email: { backend: 'mcp:agentmail' }` → the 24 `mcp_agentmail_*` tools.
- The `_hosted-template` also authors `connectors.WebSearch: { backend: 'native:brave-free' }`
  → `web_search`. **`scott/customer.yaml` has no WebSearch connector** (lines 95-104) —
  the template is ahead of the live seat.
- Skills enabled: `inbox-triage`, `workspace`, `status-report-assembler`.
- Hermes natives + the overlay's job/escalation/peer-memory tools.

**Rough order: ~45-60 tools on a Hosted Agent seat**, dominated by AgentMail.
The 62 legal-vertical MCP tools (Smokeball/Clio) are Operator-only, connector-gated.

---

## 3. FILE AND DOCUMENT HANDLING

### Generic files

`read_file` (READ), `write_file` (INTERNAL_WRITE), `patch` (INTERNAL_WRITE),
`search_files` (READ) — `action_classes.py:469-470, 536-537`. These are Hermes-native
and operate on the Machine's filesystem (the Fly volume). A seat **can** hold a document as
a file on `/opt/data` and re-read it across turns.

### Google Docs / Drive — the workspace broker

`operator/workspace_broker/` runs as a **separate uid** (`workspace-broker`) launched
root-side by `operator/templates/entrypoint.sh:232-250` via `setpriv --no-new-privs` with
`env -i`. The agent talks to it over a unix socket (`SMD_WORKSPACE_BROKER_SOCKET`). The
agent process **never holds a Google credential** — that is the whole design (OP-P0-2).

18 operations (`operator/workspace_broker/operations.py:52-71`), with trust classes from
`shared/action_classes.py:483-506`:

| Operation                                                               | ActionClass     |
| ----------------------------------------------------------------------- | --------------- |
| `workspace_gmail_search`, `workspace_gmail_get`                         | READ            |
| `workspace_gmail_create_draft`                                          | INTERNAL_WRITE  |
| `workspace_gmail_modify`, `workspace_gmail_archive`                     | **DESTRUCTIVE** |
| `workspace_calendar_list`, `workspace_calendar_get`                     | READ            |
| `workspace_calendar_create_draft`, `workspace_calendar_update_draft`    | INTERNAL_WRITE  |
| `workspace_drive_list`, `workspace_drive_get`, `workspace_drive_export` | READ            |
| `workspace_docs_get`                                                    | READ            |
| `workspace_docs_create`, `workspace_docs_append`                        | INTERNAL_WRITE  |
| `workspace_sheets_get_values`                                           | READ            |
| `workspace_sheets_create`, `workspace_sheets_update_values`             | INTERNAL_WRITE  |

Notes: no `docs_replace`/`docs_delete`, no `drive_delete`, no upload — append-only Docs.
Calendar draft tools force tentative status, no attendees, no notifications
(`operator/skills/workspace/SKILL.md:41-42`).

### **CONFIG-DECLARES-BUT-NOT-WIRED, #1 (Hosted Agent)**

`scott/customer.yaml` enables the `workspace` skill (lines 75-81) but authors **no
`google_auth` block**. Only `smd`, `smd-staging`, `pilot-law` author one.

The broker independently re-reads `customer.yaml.google_auth` and fail-closes:
`authored_identities({})` returns `("", set(), {})`
(`operator/workspace_broker/google_auth.py:37-65`), and `credentials()` then raises
`RuntimeError("DWD requires authored subject and scopes")`
(`google_auth.py:86-87`).

**So: on the live Hosted Agent seat, all 18 `workspace_*` tools are registered
and every one of them raises at call time.** The skill is enabled, the tools advertise
to the model, and the capability does not exist. This is exactly the class the lead flagged.

Secondary observation: `entrypoint.sh` **FATALs the whole boot** if the Google credential
file is not materialized (`entrypoint.sh:220-223`, confirmed present on `origin/main` at
line 221 — this worktree deletes 110 lines from that file as unrelated WIP).
`materialize_credential` returns silently when `GOOGLE_SERVICE_ACCOUNT_JSON` /
`GOOGLE_TOKEN_JSON` are absent (`google_auth.py:14-20`). The seat boots only because
`provision-customer.sh:598` stages `GOOGLE_SERVICE_ACCOUNT_JSON` **unconditionally for every
seat** from the operator's shell env. So every Hosted Agent Machine carries the fleet
Google service-account key even though the seat cannot use it. Not verified against a live
Machine — I did not touch Fly.

---

## 4. STORAGE REALITY

### CONFIRMED — no per-customer D1, no Vectorize

- **`wrangler.toml` has exactly one D1 binding**: `DB` → `ss-console-db`
  (`wrangler.toml:55-59`). That is the **console's** database (clients, quotes,
  `customer_configs`, `mcp_issued_grants`). There is no per-customer D1.
- **There is no `[[vectorize]]` block anywhere in `wrangler.toml`.** No Vectorize
  binding exists. `provision-customer.sh` never creates one — the only match in
  `operator/bin/` is a comment in `decommission-customer.sh:5` listing it as a
  hypothetical cleanup step.
- **`memory.vectorize_index` in `customer.yaml` is validator-only.** The validator
  requires it to equal `hermes-{customer_id}-vault`
  (`docs/specs/operator/customer-yaml-schema.md:136, 522`) and nothing reads it at runtime.
  Zero non-doc, non-test hits for "vectorize" in the overlay's Python.
- **"D1" on the Machine is a lie of naming.** `shared/d1_client.py` is
  `import sqlite3` / `sqlite3.connect(path, check_same_thread=False)`
  (`d1_client.py:52, 288`). `SMD_D1_AUDIT_BINDING` resolves to a **path**, default
  `/opt/data/audit.db`. Same for `SMD_D1_AGENT_STATE_BINDING`.
- **Memory is Hermes' flat-file core, in-session only.** `operator/templates/README.md`
  ("Memory semantics (Phase 1)"): `MEMORY.md`/`USER.md` per profile; customer-owned
  explicit memory is "**not on the runtime read path**"; Honcho **deferred to Phase 2**,
  and the earlier in-container `honcho-ai` install was **fictional** (that package is the
  client SDK, not a server). `SMD_D1_OBSERVATIONS_BINDING` / `HONCHO_BASE_URL` /
  `HONCHO_API_KEY` are declared `inert in Phase 1` in `contracts/consumes.yaml`.
  Postgres and Redis are in the image but **not started**.

### What storage a seat DOES have

**Fly volume `hermes_state`, 10 GB, mounted at `/opt/data`** (`templates/README.md`):

| Path                                 | Contents                                            |
| ------------------------------------ | --------------------------------------------------- |
| `/opt/data/customer.yaml`            | live config, R2-mirrored                            |
| `/opt/data/audit.db`                 | per-customer audit SQLite (`hermes-smd-audit`)      |
| `/opt/data/profiles/<slug>/`         | Hermes per-persona profiles + `MEMORY.md`/`USER.md` |
| `/opt/data/oauth/`                   | per-provider OAuth token files (ADR 0010)           |
| `/opt/data/voice/`                   | voice-sample warm cache                             |
| `/opt/data/smd/sticky_stop.db`       | cost-breaker state                                  |
| `/opt/data/smd/exposure_override.db` | entitlement dial                                    |
| `/opt/data/held_replies.db`          | held-reply queue (#2070)                            |
| `/opt/data/msgraph/delta-state.json` | Graph delta cursor                                  |
| `/opt/data/observations.db`          | Phase 2, unused                                     |
| `/opt/data/honcho/pg/`, `/redis/`    | Phase 2, unused                                     |

**tmpfs (`/run`, wiped on restart by design):** `/run/smd-mcp/` (MCP result + thread
stores), `/run/smd-connector-health/ledger.json`.

**Cloudflare R2 (console-side, 3 buckets, `wrangler.toml:61-85`):**
`ss-console-storage` (STORAGE), `smd-consultant-photos` (public), `smd-customer-config`
(CUSTOMER_CONFIG — `vaults/<slug>/customer.yaml` live config + voice vaults + skill bodies).
R2 is the source of truth for live reconfig; the Machine pulls, root-side.

**Console D1 (`ss-console-db`):** `customer_configs` projection, `mcp_issued_grants`,
`operator_mcp_grant_audit`, plus the whole admin/portal schema.

### Where could a new application's structured data live TODAY?

1. **`/opt/data/*.db` — SQLite on the Fly volume.** The established pattern (six such
   files already). Survives restart and reprovision by design. Zero new infrastructure.
   Reachable from the agent only via a tool; there is no generic `sql_query` tool in the
   allowlist, so it needs either a broker verb or a new overlay plugin tool.
2. **The console's `ss-console-db` D1.** Already bound, already has an authenticated
   route layer. Right home for anything the _browser_ reads.
3. **R2 `smd-customer-config`.** Root-side pull only; not agent-writable by design.
4. **Files on `/opt/data` via `write_file`/`read_file`.** Agent-writable today, no new
   code at all. Crude but real.

**Not available without new infra:** any vector store, any per-customer relational DB
reachable from the edge, any KV the Machine can write.

---

## 5. RETRIEVAL

**Plainly: there is no semantic retrieval on a seat. None. Not built, not bound, not stubbed.**

What exists:

- `search_files` (READ, `action_classes.py:470`) — Hermes-native local file search.
  Lexical. I cannot read its implementation (Hermes not checked out), so I cannot say
  whether it is glob, substring, or regex.
- `read_file` (READ, :469).
- `session_search` (READ, :473) — Hermes-native recall over prior sessions.
- `web_search` (READ, :281) — external, not corpus retrieval.
- `memory_search` / `memory_get_rule` / `memory_list_rules` (READ, :462-464).
  **These appear ONLY in the classification map.** Grep across the overlay finds no
  implementation and no registration. They are either Hermes-native (plausible — the map's
  own comment groups the natives right below them) or aspirational entries classified in
  advance. **Unverified. Do not assume they work.**
- No `grep`-class tool is in the allowlist, so a `grep` tool would classify `REFUSED`.
- No embedding model, no index, no reranker, nothing in either repo.

Retrieval today is: the model's context window, the quoted email body, whatever the skill
explicitly reads via `read_file`/`search_files`, and connector-side search
(`mcp_agentmail_search_messages`, `mcp_smokeball_*`) which is the vendor's index, not ours.

---

## 6. SKILLS AS A UNIT OF CAPABILITY

**51 skills** in `operator/skills/`. Overwhelmingly legal-vertical (PI/litigation:
`demand-letter-drafter`, `separate-statement-assembler`, `medical-chronology-maintainer`,
`deadline-and-sol-tracker`, ...). Three are generic and are what the Hosted Agent uses:
`inbox-triage`, `workspace`, `status-report-assembler`.

### Shape

A skill is a directory: `SKILL.md` + `references/` + `tests/`. `SKILL.md` is markdown
with YAML frontmatter:

```yaml
name, description, version, author, license, platforms, prerequisites: { skills, commands }
metadata: { hermes: { tags: [...] }, smd: { customer: <slug> } }
```

Body sections: **When to Use / Mode / Prerequisites / How to Run / Procedure**.

It is a **prompt-and-procedure document, not code.** It names the tools it is allowed to
use and forbids alternatives — `operator/skills/workspace/SKILL.md:22-27`: "This is the only
Google Workspace path. Do not use the Hermes-native `google-workspace` or `himalaya` skills.
Do not use `execute_code`, `terminal`, or connector CLIs. The gateway has no Google
credential." Enforcement is the trust gate + the absence of credentials, not the prose.

### Invocation — all three modes, authored per skill per persona

`customer.yaml.personas[].skills[].initiation` is a three-boolean map
(`scott/customer.yaml:67-88`):

```yaml
- name: inbox-triage
  initiation: { manual: true, scheduled: true, webhook: true }
```

- **manual** — `hermes run <skill>` (`inbox-triage/SKILL.md`, "How to Run"), and args are
  supported (`--window`, `--max`, `--mailbox`).
- **scheduled** — `personas[].cron[]` entries: `{ skill, schedule, wake_policy }`.
  Hermes-native cron, no agent in the loop (ADR 0021).
- **webhook** — `webhook_triggers[]` maps `{source, event_type} → {skill, persona}`,
  dispatched by `hermes-smd-webhook-router` on the `pre_gateway_dispatch` hook.

### Could a skill be "a capability the router dispatches to"?

**Yes — that is literally what `webhook_triggers[]` already is.** An inbound event is
matched on `(source, event_type)` and routed to a named skill on a named persona. The
gaps for a UI-driven router:

- The dispatch key is `(source, event_type)` — a webhook envelope, not an intent label.
- **`/mcp/turn` does not accept a skill name.** Its body is `{message, thread_id,
principal_subject}` (`webhook_gate.py:796-812`); the agent picks its own skill from
  `skills_list` / `skill_view`. Routing to a specific skill from a UI would need either a
  new field on the turn contract or a new webhook trigger source.
- Skills carry no machine-readable capability declaration beyond `description` and
  `metadata.hermes.tags` — a router would be matching on free text.

---

## 7. THE PLUGIN HOOK SURFACE

`operator/contracts/overlay-hook-surface.json` declares the **closed set of 7 hooks** the
overlay must register for governance to be real, plus 5 `functionalPlugins` whose
activation IS the guarantee. Parity is CI-enforced against the runtime activation gate
(`operator/safety_substrate/tests/test_guard_hook_parity.py`).

| Hook                   | Plugin                    | Safety-critical | Purpose                                                          |
| ---------------------- | ------------------------- | --------------- | ---------------------------------------------------------------- |
| `pre_tool_call`        | hermes-smd-trust          | **yes**         | trust-ceiling gate; refuses a disallowed tool before it executes |
| `post_tool_call`       | hermes-smd-audit          | **yes**         | per-tool audit row + trust accounting                            |
| `post_llm_call`        | hermes-smd-audit          | no              | audit + voice gate on generated output                           |
| `pre_llm_call`         | hermes-smd-voice          | no              | voice transform / sample-driven rewrite                          |
| `subagent_stop`        | hermes-smd-audit          | no              | one row per child subagent completion                            |
| `on_session_end`       | hermes-smd-memory-mirror  | no              | mirror to D1 (inert in Phase 1)                                  |
| `pre_gateway_dispatch` | hermes-smd-webhook-router | **yes**         | route inbound to skills + inbound trust boundary                 |

15 plugin dirs exist in the overlay; hooks actually registered across all of them
(`grep register_hook`): the seven above plus **`transform_tool_result`**
(hermes-smd-inbound:362, hermes-smd-hook-probe:160), **`transform_llm_output`**
(hermes-smd-voice:402), and **`post_api_request`** (hermes-smd-usage:132).

### Can a hook intercept or shape a response destined for a UI? — **Yes, two of them, today.**

1. **`transform_llm_output`** — `plugins/hermes-smd-voice/__init__.py:275-298`.
   "Structurally reshape the model's response to match the customer's voice." Returns the
   reshaped string, or `None` to leave it unchanged. It runs on every generated response.
   This is a general-purpose response-rewriting seam that already ships.
2. **`post_llm_call`** — this is precisely how `/mcp/turn` works at all. The
   `hermes-smd-mcp-result-sink` plugin captures the completed turn's answer in
   `post_llm_call` and writes it to the result store the gate polls
   (`plugins/hermes-smd-mcp-result-sink/__init__.py:81-88`). **The existing
   console→Machine reply path is already a hook shaping a response for an out-of-band
   consumer.** A UI would be a second consumer of the same seam.

Also useful: `transform_tool_result` (used by hermes-smd-inbound for the taint boundary)
could annotate tool output before the model sees it.

**Where the hook surface does NOT help:** all four are **post-hoc, whole-response** hooks.
`post_llm_call` fires when the turn is done. None of them is a token-level callback, so
none of them enables streaming.

---

## 8. STREAMING AND LATENCY

### Streaming

**None, anywhere.** No SSE, no chunked transfer, no `ReadableStream`, no
`text/event-stream` in `src/lib/operator/mcp/*` or `webhook_gate.py`. The MCP
`initialize` response advertises only `{ tools: { listChanged: false } }`. The Machine
long-polls a **file** for a completed answer. Every layer is whole-response.

### Measured latency — real numbers exist

From `~/dev/engagements/operator/customers/ashton-price/SUSTAINED-DIALOGUE-CHANNEL-2026-07-30.md`
(07-30 burst rehearsal, both carrying verify-ledger IDs):

- **14 seconds end-to-end, email path**: send → webhook → agent turn → reply in inbox.
  Uncapped path. `vfy_01KYRXS0Q1K0Z47GMG2YVECB2G`.
- **12 simultaneous messages completed in 33 seconds** — because stock Hermes keys webhook
  sessions per-delivery, so concurrent inbound runs in parallel. `vfy_01KYRYV3MA`.
  That same design is exactly why there is no cross-turn session continuity.

### Realistic wall-clock for a simple turn through `/mcp/turn`

The email figure includes mail-provider hops the MCP path does not have, but adds nothing
the MCP path does. **Expect roughly 10-20 s for a simple turn**, dominated by the model.
Bounds:

- Poll granularity adds up to **250 ms** of dead time (`_MCP_POLL_INTERVAL_S`).
- Hard ceiling **55 s** (`_MCP_POLL_TIMEOUT_S`) → 504 `turn_timeout`.
- Loopback connect timeout 30 s.
- Plus one Cloudflare Worker round trip.

For a chat UI: a first token would arrive in ~0.5-1 s if streaming existed; today the user
waits ~10-20 s staring at nothing. That is the single biggest UX gap.

---

## 9. CROSS-CUTTING: what config declares that is NOT wired

1. **`memory.vectorize_index`** — validator-enforced, zero runtime readers, no binding,
   never provisioned. (§4)
2. **`memory.d1_namespace`** — there is no per-customer D1; "D1" on the Machine is
   `sqlite3`. (§4)
3. **Honcho** (`SMD_D1_OBSERVATIONS_BINDING`, `HONCHO_BASE_URL`, `HONCHO_API_KEY`) —
   declared, explicitly `inert in Phase 1`. Postgres/Redis in the image, not started.
4. **The `workspace` skill on the Hosted Agent seat** — enabled, tools registered,
   fail-closed at the credential because no `google_auth` is authored. (§3)
5. **`connectors.WebSearch`** — in `_hosted-template`, **absent from the live
   `scott` seat**. The template is ahead of the seat.
6. **`memory_search` / `memory_get_rule` / `memory_list_rules`** — classified,
   no implementation found in the overlay. (§5)
7. **The `confirm` ceiling — the sharpest one.** Both hosted templates author
   `external_send: confirm` (`_hosted-template:93`, `scott:66`) and ADR 0071 makes
   confirm-on-send the product's whole "it acts, safely" pitch. But
   `operator/adapter/trust_ceiling.py:292-310`:

   > "The approval-capture round-trip is **#1806**; until it lands, confirm resolves to
   > `await_approval` (fail-safe: nothing sends)."

   The ceiling **value** and its enforcement are built. The mechanism that captures an
   owner's "yes" over channel is **not landed**. So today a Hosted Agent seat authored at
   `confirm` **withholds every external send** — `allowed=False`,
   `audit_action="await_approval"`. Fail-safe, correct, and not the shipped product.

8. **Send sub-classes are not inherited.** Exposure is a _sparse_ map read with
   `exposure.get(action)`; unauthored non-READ classes resolve to `REFUSED`
   (`trust_ceiling.py:95-119`; overlay `enforce.py:12-23, 263`). `scott` authors only
   `internal_write` and `external_send` — so `external_send_internal`,
   `external_send_client`, `external_send_vendor`, `commitment`, `destructive`, and
   `code_execution` are all **REFUSED** on that seat. Whether that blocks replies to the
   allowlisted sender depends on how the recipient classifier types
   `smdurgan@icloud.com` at runtime — **not verified; needs a live probe.**

## 10. GOVERNANCE: shared substrate vs authored per-product

**Shared substrate, identical on both products** (do NOT report these as Operator-only):
`trust_ceiling.py` / `enforce.py`, the 176-name fail-closed allowlist, the taint-gate,
the 7 governance hooks, the audit ledger, the cost breaker + inbound wake cap, the
workspace broker's uid separation, the console-sole MCP door + grant kill switch.

**Authored per-product, in `customer.yaml`** — this is where the Hosted Agent is lighter:
`personas[].entitlements.exposure` (hosted authors only two knobs), `connectors{}`
(hosted: AgentMail + optional native Brave; Operator: Smokeball/Clio/Graph),
`scope.inbound_allow_from` (hosted: exact addresses only, no domain wildcards, ADR 0067),
`safety.sticky_stop` (hosted: `cost_cap_daily_cents: 1000` on the _customer's own_
Anthropic key), `vertical` (hosted: `mixed` → no pack floors; `VERTICAL_FLOORS` is
currently `{}` anyway — the law-firm external-send floor was removed 2026-07).

The Hosted Agent's governance is lighter **by authoring**, not by a different substrate.
Every fail-closed default in §9 item 8 applies to it identically.
