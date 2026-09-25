# customer.yaml — Formal Schema

**Spec for issue [#790](https://github.com/venturecrane/ss-console/issues/790).** Source of truth for one customer's configuration. Git is the authoritative store ([ADR 0012](../../adr/0012-customer-yaml-storage.md)); the portal D1 `customer_configs` table and per-customer R2 prefix are materialized projections, not competing sources. Never contains literal secret values.

## Source

- [ADR 0006](../../adr/0006-capability-adapter-pattern.md) — capability-interface + adapter pattern
- [ADR 0011](../../adr/0011-multi-persona-per-customer.md) — `personas:` is an array (length ≥ 1 at v1)
- [ADR 0012](../../adr/0012-customer-yaml-storage.md) — git source of truth, CI-validated on merge

## Schema version

Files declare `schema_version: 1`. CI rejects merges where the file's `schema_version` is not in the validator's accepted-version set. Schema evolution rules: see [ADR 0012](../../adr/0012-customer-yaml-storage.md) §8.

## Contract

```yaml
schema_version: 1 # REQUIRED. Currently only "1" is accepted.

# ---- IDENTITY (REQUIRED) ----

customer_id: <slug> # ^[a-z0-9][a-z0-9-]{0,31}$ — matches the configs-repo file name (sans .yaml)
customer_name: <string> # display only; max 100 chars
vertical: <enum> # marketing-agency | law-firm | real-estate | manufacturing | insurance | mixed
practice_areas: <list<string>> # required when vertical=law-firm; otherwise OPTIONAL
fly_region: <string> # Fly.io region slug (e.g. iad, lax, ord)

# ---- RUNTIME (REQUIRED) ----

model: <string> # Anthropic model ID (e.g. claude-opus-4-7)
hermes_ref: <string> # Upstream Hermes pin per ADR 0024: v{YYYY}.{M}.{D}@{40-hex-sha} (e.g. v2026.5.7@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0)

machine:
  size: <string> # Fly VM class (performance-1x, etc.)
  memory_mb: <int> # 256-8192

# ---- GOOGLE WORKSPACE AUTHORITY (OPTIONAL) ----
# Present when Google Workspace uses customer-owned domain-wide delegation.
# GOOGLE_SERVICE_ACCOUNT_JSON is stored as an environment secret, never here.

google_auth:
  mode: <enum> # user_oauth | dwd
  subject: <email> # REQUIRED when mode=dwd; Workspace user to impersonate
  scopes: <list<string>> # REQUIRED non-empty when mode=dwd

# ---- HUMANS WITH PORTAL ACCESS (REQUIRED) ----
# See dashboard-roles.md.

users:
  - email: <string>
    role: <enum> # principal | operator | compliance
    full_name: <string>
    voice_profile_id: <slug> # OPTIONAL; ^[a-z0-9][a-z0-9-]{0,31}$; unique within users[]
    # When set, Layer 2 voice transform selects this user's profile
    # for drafts attributed to this reviewer. When omitted, the user
    # inherits the customer-level general voice. See ADR 0011 for the
    # multi-user-vs-multi-persona distinction.

# ---- PERSONAS (REQUIRED; ARRAY; length ≥ 1) ----
# Per ADR 0011: array at v1, length=1 in practice; Phase 2 may grow.

personas:
  - slug: <slug> # ^[a-z0-9][a-z0-9-]{0,31}$; unique within this file
    status: <enum> # active | archived (length-≥-1-active enforced at v1)
    name: <string> # first name; max 50 chars
    title: <string> # OPTIONAL; e.g. "AI Associate"; max 50 chars
    signature_html: <string> # OPTIONAL; generated at provision if absent
    avatar_url: <string> # OPTIONAL; https URL
    tone: <list<string>> # 3-5 adjectives; e.g. ["warm-but-professional", "concise"]
    pronouns: <enum> # OPTIONAL; "they/them" (default) | "he/him" | "she/her"
    send_as: # OPTIONAL; AgentMail identity
      agentmail_identity: <string> # e.g. marcus@<customer_id>.agents.smd.services
    skills:
      - name: <skill-slug>
        version: <string> # OPTIONAL; 6-char content hash or "pending" (CI defaults to "pending")
        trust_ceiling: <enum> # autonomous | draft_for_review | refused
        enabled: <boolean> # OPTIONAL; default true
        cost_estimate: # OPTIONAL but required for cost telemetry rollup
          tokens_in_per_run: <int>
          tokens_out_per_run: <int>
          tool_calls_per_run: <int>
          runs_per_day_typical: <int>
        scope: <list<string>> # OPTIONAL skill-specific scope tags
    voice_overrides: <object|null> # OPTIONAL; inherits voice_library when null
    escalation_overrides: <object|null> # OPTIONAL; inherits top-level escalation when null
    channel_bindings: # OPTIONAL; integrations this persona drives
      - integration: <string> # e.g. ms-graph, gmail, slack
        channels: <list<string>> # e.g. ["primary-inbox"]

# ---- CUSTOMER-SCOPE CONFIG (REQUIRED) ----

connectors:
  # Map of capability name → adapter binding. Capability names are the closed
  # union from src/lib/operator/capabilities/types.ts CapabilityName:
  # PracticeManagement | Email | Calendar | DocumentStorage | ESign |
  # CourtAccess | Payments | Accounting | IntakeCRM | CallTracking | InternalComms |
  # WebSearch
  <CapabilityName>:
    adapter: <slug> # e.g. filevine, microsoft-graph, docusign
    backend: <string> # mcp:<url> | build:<wrapper> | synthetic:<fixture> | native:<provider>
    enabled: <boolean> # OPTIONAL; default true
    scopes: <list<string>> # OPTIONAL; oauth scopes this connector needs
    token_ref: <string> # OPTIONAL; Infisical reference; see Secret Exclusion

scope: # email / folder visibility envelope
  email_folders_visible: <list<string>>
  email_folders_blind: <list<string>>
  email_keyword_blocks: <list<string>>
  domain_blocks: <list<string>>
  matter_blocks: <list<string>> # OPTIONAL; external PM matter refs
  inbound_allow_from: <list<string>> # OPTIONAL; the ADR 0055 organization roster —
  # exact addresses or `@domain` grants whose mail the Operator may RESPOND to
  # (not merely draft). Empty/absent is fail-closed: drafts only.
  admins: <list<email>> # OPTIONAL; the Operator-admin allow list (ADR 0085 §2) — the
  # people who may establish the firm's voice and output shape by instructing the
  # Operator, and who may promote a captured correction. The role the signed
  # agreements call Named Administrator.
  #   - PERSON addresses only (`local@domain`). An `@domain` grant is REJECTED:
  #     an admin is a person, and a domain grant would hand establishment
  #     authority to every future hire. Duplicates are REJECTED.
  #   - Empty/absent is fail-closed — no instruction on any channel resolves
  #     admin-classed, so nothing widens.
  #   - Changed through a PR (who speaks for the firm is commitment-shaped).
  #     NOT portal-editable; the portal shows the list read-only.

escalation: # default; per-persona override allowed via personas[].escalation_overrides
  red_flag_recipients: <list<email>> # at least one
  failure_recipients: <list<email>> # at least one
  acknowledgement_window_minutes: <int> # OPTIONAL; default 60

voice_library: # OPTIONAL; shared across personas unless overridden
  samples_path: <string> # OPTIONAL; r2:// path

voice_cohorts: # OPTIONAL; recipient-cohort taxonomy (issue #857)
  cohorts: <list<slug>> # ≥1 entry; each entry matches ^[a-z0-9][a-z0-9-]{0,31}$
  # Omission of `voice_cohorts:` accepts the base set:
  # ["client", "opposing-counsel", "court", "internal"]
  min_samples_per_cohort: <int> # OPTIONAL; positive integer; overrides
  # the Layer 2 module default for the per-(user,cohort) fallback floor

business_hours: # OPTIONAL; defaults to M-F 08:00-18:00 in fly_region tz
  timezone: <string> # IANA tz
  days: <list<string>> # ["mon", "tue", ...]
  start: <HH:MM>
  end: <HH:MM>

memory: # MUST satisfy isolation invariant (see Failure modes)
  d1_namespace: <slug> # MUST equal customer_id
  r2_vault_path: <string> # MUST equal "vaults/{customer_id}/"; see r2-vectorize-naming.md
  vectorize_index: <slug> # MUST equal "hermes-{customer_id}-vault"

logging: # OPTIONAL
  level: <enum> # debug | info | warn | error
  ship_to: <list<enum>> # cloudflare-d1 | fly-logs

pause: # OPTIONAL
  active: <boolean> # default false
  reason: <string> # required if active=true

observability: # OPTIONAL — added by ADR 0023 Wave 1
  sentry: # OPTIONAL
    enabled: <boolean> # default true; shared SMD project, tenant-tagged at SDK init
  health: # OPTIONAL
    period_seconds: <int> # default 60; heartbeat push cadence to healthchecks.io
    grace_minutes: <int> # default 5; late before healthchecks.io fires a webhook

# ---- COMPLIANCE DASHBOARD VIEW (OPTIONAL; added by #895) ----
# See dashboard-roles.md §"Dedicated Compliance dashboard view (#895)".
# Defaults to false when omitted. RBAC on the existing audit surface is
# unaffected by this field — it gates only the dedicated Compliance view.
compliance_enabled: <boolean> # OPTIONAL; default false
```

## Memory retention

**Added by [#863](https://github.com/venturecrane/ss-console/issues/863); runner removed by #1355.** The ADR-0008 periodic cleanup runner this block once configured is gone (never scheduled, swept a store that was never provisioned). The block remains OPTIONAL and `audit_log_days` remains LIVE — the decommission pipeline's `resolve_audit_log_days` reads it for the audit-retention carve-out ([`audit-retention.md`](./audit-retention.md)). The other windows are inert pending the ADR-0016 Machine-side retention story.

```yaml
memory:
  # ...existing memory.* fields...
  retention: # OPTIONAL
    matters_days: <int> # OPTIONAL; default 730 (2 years)
    documents_days: <int> # OPTIONAL; default 365 (1 year)
    recipients_days: <int> # OPTIONAL; default 730 (2 years)
    voice_samples_days: <int> # OPTIONAL; default 365 (1 year)
    audit_log_days: <int> # OPTIONAL; default 2555 (7 years; legal industry norm)
    drafts_days: <int> # OPTIONAL; default 90 (90 days)
```

Field rules:

- Every `*_days` value MUST be a positive integer. Non-int values trigger a logged fallback to the module default in `MemoryRetentionPolicy.from_customer_yaml`; zero or negative values raise `ValueError` at policy-construction time.
- Unknown keys under `memory.retention:` are ignored (forward-compat with future schema versions that add more knobs).
- The runner only sweeps memory + voice today. `audit_log_days` and `drafts_days` declare the policy now; their sweep code lands in follow-ons against `memory-retention.md` §"Per-pipeline scope".
- The default scheduled cron uses `access_scope = firm-wide`; partner-only and attorney-list rows require a narrower Captain-invoked sweep (see `memory-retention.md` §"Access-scope discipline").

## Capability binding

The `connectors:` map keys MUST be drawn from the canonical capability union published by [`src/lib/operator/capabilities/types.ts`](../../../src/lib/operator/capabilities/types.ts):

```
PracticeManagement | Email | Calendar | DocumentStorage | ESign |
CourtAccess | Payments | Accounting | IntakeCRM | CallTracking | InternalComms |
WebSearch
```

This union is the wire contract from [ADR 0006](../../adr/0006-capability-adapter-pattern.md). Adding a key outside this set is a validation error — new capabilities require an ADR that extends the type union, then a follow-on schema version bump per [ADR 0012](../../adr/0012-customer-yaml-storage.md) §8.

The `adapter:` value is the SMD-internal adapter slug (e.g. `filevine`, `microsoft-graph`, `docusign`). It is treated as opaque by the schema; the per-adapter conformance harness at boot ([ADR 0006](../../adr/0006-capability-adapter-pattern.md), `src/lib/operator/capabilities/conformance.ts`) verifies the adapter actually satisfies the interface's required-method set. The schema does NOT enumerate accepted slugs — that registry lives with the adapter implementations.

### `WebSearch` — the shared web-search connector ([ADR 0070](../../adr/0070-web-search-shared-connector-divergent-defaults.md))

`WebSearch` is a connector-only capability: it has **no** skill-facing adapter interface (no `web-search.ts`, no conformance methods — its `BANNED_METHOD_NAMES` entry is empty). It exists so the `connectors:` map can bind a web-search backend under the same trust-ceiling and secret machinery every other connector uses.

```yaml
connectors:
  WebSearch:
    adapter: brave
    backend: 'native:brave-free' # Hermes' native web provider (bundled), NOT an MCP server.
    #                              A sensitive/legal seat uses a paid/customer-owned Brave tier;
    #                              other native providers (tavily/exa/firecrawl) are drop-in.
    enabled: true
```

- **Altitude is search only.** The driven/cloud browser is explicitly out of scope (ADR 0070) — heaviest resource cost and the largest prompt-injection surface; reserved for a future Operator-tier authored capability. (`native:brave-free` is search-only; extract would be a separate native provider.)
- **The one deliberate default divergence (ADR 0035 — no imposed defaults):**
  - **Hosted Agent:** `enabled: true` in `_hosted-template` — research is the marketed product.
  - **Operator:** left **unauthored** in `_template` — the web is incidental and lower-trust in regulated verticals, so it is authored per engagement.
- **Cost** is SMD-absorbed on Brave's **free** tier for the Hosted Agent ($0, no runaway spend — it rate-limits at quota, never bills) + a per-seat fair-use cap (`safety.sticky_stop.web_search_daily_cap`, below). Not BYO — it must not add a second signup for the unwilling-to-operate buyer. Sensitive Operator tiers use a paid or customer-owned Brave key (one party in the query path).
- **Runtime.** Web search is **native**: the overlay's `translate.py::_materialize_web_search` resolves `native:<provider>` to config `web.search_backend`, and Hermes' bundled provider registers the native `web_search` tool (classified READ) — no MCP server. The provider reads its key (e.g. `BRAVE_SEARCH_API_KEY`) directly. Confirm Brave's data-processing/retention terms before authoring `WebSearch` for an Operator legal seat. (The first ADR 0070 cut wrapped Brave in `mcp:brave`; that redundant layer was retired 2026-07-08.)

## Secret-exclusion enforcement

`customer.yaml` is git-committed. A secret committed here lands in git history permanently. For a law-firm tenant, that is a privilege-breach with bar-discipline consequences.

The validator runs a secret-scan pass over the raw file text BEFORE structural parsing, so a malformed YAML containing a secret still fails closed.

### Banned at the value level (anywhere in the document)

1. **Provider-shaped API keys** matching `^(sk|pk|rk)_(live|test)_[A-Za-z0-9]{20,}$` (Stripe / Anthropic / Resend / similar)
2. **JWT-shaped tokens** matching `\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b` (three base64url segments separated by `.`)
3. **AWS-shaped access key IDs** matching `^AKIA[0-9A-Z]{16}$`
4. **GitHub-shaped tokens** matching `^gh[pousr]_[A-Za-z0-9]{36,255}$`
5. **OpenAI-shaped keys** matching `^sk-[A-Za-z0-9]{32,}$` or `^sk-proj-[A-Za-z0-9_-]{32,}$`
6. **Slack-shaped tokens** matching `^xox[abprs]-[A-Za-z0-9-]{10,}$`
7. **Google OAuth client secrets** matching `^GOCSPX-[A-Za-z0-9_-]{20,}$`
8. **Hex-encoded secrets** — any standalone string of 40+ hex characters (`^[a-f0-9]{40,}$`)
9. **Base64 secret-shaped** — any string matching `^[A-Za-z0-9+/]{40,}={0,2}$` with length > 80, outside known-safe fields (`signature_html`, `avatar_url`)
10. **High-entropy long strings** — any string of 32+ characters whose Shannon entropy ≥ 4.5 bits per character, outside the same allowlist

### Banned at the field-name level

The validator flags any field name containing (case-insensitive) one of:
`password`, `passwd`, `secret`, `client_secret`, `api_key`, `apikey`, `access_token`, `refresh_token`, `private_key`, `bearer`, `auth_token`

The presence of one of these field names is by itself a rejection — even if the value is `~` / empty / a placeholder. Such fields belong in Infisical, not the YAML.

### The only permitted secret-reference pattern

```yaml
connectors:
  PracticeManagement:
    adapter: filevine
    backend: build:filevine-mcp
    token_ref: 'infisical:/operator/{customer_id}/practice-management/oauth-refresh'
```

`token_ref` strings:

1. MUST begin with the literal prefix `infisical:`
2. MUST have at least three slash-separated path segments after the prefix (`/<scope>/<customer>/<purpose>`)
3. MUST NOT contain any value that itself triggers a secret heuristic (you cannot smuggle a secret as a "ref")

References are resolved by `bin/provision-customer.sh` at deploy time and injected as Fly secrets. They never appear in Hermes container env vars at rest.

### Allowlisted fields (NOT scanned for high-entropy / base64 patterns)

- `personas[].signature_html` — HTML signature bodies legitimately contain long encoded strings (e.g. embedded image data URIs)
- `personas[].avatar_url` — https URLs may have long query strings
- `personas[].send_as.agentmail_identity` — long subdomain identities
- `users[].email`, `escalation.red_flag_recipients[]`, `escalation.failure_recipients[]` — email addresses
- `customer_name` — display names

Allowlisted fields are still scanned for **provider-shaped keys** (the patterns in §1-7 above); an OpenAI key smuggled into `signature_html` is still rejected.

## Multi-user voice profiles

**Added by [#858](https://github.com/venturecrane/ss-console/issues/858).** A customer may have multiple humans on portal access — for example, a principal partner who personally writes the firm's most consequential email and an associate attorney whose drafts go out under the associate's identity. The draft-for-review posture ([ADR 0035](../../adr/0035-no-imposed-entitlement-defaults.md)) attributes every shipped message to the user who approved it; Layer 2 voice transform shapes the draft to match _that reviewer's_ writing voice, not a single firm-wide composite.

The `users[].voice_profile_id` field is the seam:

- When set, voice samples ingested while this user's identity was the sent-folder author are tagged with the user's profile slug. Layer 2 looks up the per-user profile and reshapes the draft to match.
- When omitted, the user inherits the customer-level **general voice profile** — the aggregate across every sample regardless of authorship. This is the default and what every existing customer uses until per-user calibration runs.
- When the per-user profile has fewer than `MIN_PROFILE_SAMPLE_COUNT` samples ([`adapter/voice/transform.py`](../../../operator/adapter/voice/transform.py)), Layer 2 falls back to the general profile rather than reshape against a noisy target.

**Distinct from multi-persona.** Per [ADR 0011](../../adr/0011-multi-persona-per-customer.md), a customer's deployment runs one or more **personas** — AI agent identities (the Operator the paralegal-substrate, Casey the intake-handler). v1 ships with one persona. Multi-user voice is orthogonal: a single persona may draft on behalf of several human reviewers, each with their own voice profile. The architecture is:

- **Persona** (the Operator) — the AI agent's identity (signature, send-as inbox, skills, trust ceilings)
- **User** (Partner Sarah) — the human who reviews and approves drafts; carries an optional `voice_profile_id`
- **Voice profile** — the writing-style signature aggregated from samples tagged with one user's slug

One persona, one customer Machine, N users with distinct voice profiles. Multi-persona runtime support is Phase 2; multi-user voice is v1 (this PR).

## Per-recipient cohort voice variation

**Added by [#857](https://github.com/venturecrane/ss-console/issues/857).** Reviewers write differently to different audiences — a partner's voice to an anxious client is not their voice to opposing counsel. PRD §9.3 Layer 3 declared per-recipient cohorts as v1 (not deferred); this PR lifts the cohort vocabulary out of informal sample tagging and into the schema.

**Base cohort taxonomy.** Customers ship by default with the four base cohorts:

- `client` — communications to the customer's own clients
- `opposing-counsel` — communications to lawyers on the other side
- `court` — communications to courts, clerks, judges, administrative bodies
- `internal` — communications among the firm's own staff

Slug names align with the voice-gate harness ([`operator/voice-gate/types.ts`](../../../operator/voice-gate/types.ts) :: `RecipientCohort`). The harness historically shipped three cohorts (`client`, `opposing-counsel`, `internal-team`); this PR adds `court` and `internal` to the harness's `RECIPIENT_COHORTS` array so the schema's four-cohort base set is acceptable to the blind-test gate. The legacy `internal-team` slug is kept in the harness union so archived blind-test runs scored against it continue to render — customers who shipped on the old vocabulary do not have to re-migrate. Schema-side, `BASE_VOICE_COHORTS` uses `internal`; customers may opt into either slug via their own `voice_cohorts.cohorts[]` declaration.

**Customer extensions.** A customer's `voice_cohorts:` block names the cohort vocabulary that customer's voice samples are partitioned into. A transactional firm with no court practice may drop the `court` cohort:

```yaml
voice_cohorts:
  cohorts:
    - client
    - opposing-counsel
    - internal
```

A firm with a unique audience (mediation panels, expert witnesses) may add custom cohorts:

```yaml
voice_cohorts:
  cohorts:
    - client
    - opposing-counsel
    - court
    - internal
    - mediator
    - expert-witness
```

When `voice_cohorts:` is omitted, the customer accepts `BASE_VOICE_COHORTS` (the four base names). The Layer 2 transform reads the resolved vocabulary via `resolveCohortVocabulary(customer.voice_cohorts)` so the absence-vs-present branch lives in one place.

**Sample tagging.** Voice samples are already written to R2 at `{customer-slug}/voice/cohort/{cohort-id}/{ulid}.json` (see [`adapter/voice/pipeline.py`](../../../operator/adapter/voice/pipeline.py) :: `_ingest_one`). The cohort vocabulary declared here is what `CohortResolver` is allowed to assign; cohorts not in the customer's declared list are coerced to the `unassigned` sentinel by the resolver.

## Skill bundles (ADR 0021 Stream D)

**Added by [ADR 0021](../../adr/0021-leverage-hermes-native-primitives.md).** Hermes ships skill bundles natively — `~/.hermes/skill-bundles/<slug>.yaml` files that load multiple skills under a single slash command. The customer.yaml `personas[].bundles[]` block declares the bundles this persona ships with; `hermes-smd bootstrap` translates each entry into the per-profile bundle YAML at Machine startup.

```yaml
personas:
  - slug: marcus
    # ... other persona fields ...
    bundles:
      - slug: inbox-sweep
        description: 'Triage inbox + flag scope creep for a draft pass'
        skills:
          - inbox-triage
          - scope-creep-flagger
        instruction: 'Optional shared context prepended to every bundled skill invocation'
```

Validation rules:

- `bundles` is OPTIONAL; default is `[]`.
- `slug` matches `SLUG_PATTERN` (`^[a-z0-9][a-z0-9-]{0,31}$`) and is unique within this persona's `bundles[]`.
- `description` REQUIRED, max 200 chars.
- `skills` REQUIRED, non-empty list of strings. Each entry MUST reference an enabled skill on the same persona — bundles cannot reference skills the persona doesn't ship with.
- `instruction` OPTIONAL — shared context prepended to every bundled skill invocation (Hermes' bundle `instruction:` field).

## Per-skill cron schedules with no-agent pre-run (ADR 0021 Stream B)

**Added by [ADR 0021](../../adr/0021-leverage-hermes-native-primitives.md).** Hermes' cron-skill attachment supports a pre-run script that emits `{"wakeAgent": false}` to skip LLM inference when nothing changed. The customer.yaml `personas[].cron[]` block declares the schedule for each cron-attached skill plus whether a pre-run script gates wake-up.

```yaml
personas:
  - slug: marcus
    cron:
      - skill: paid-media-anomaly-watcher
        schedule: '0 7 * * *' # daily 0700 in Hermes' configured timezone
        pre_run: pre_run.py # path relative to the skill directory
        wake_policy: pre_run_decides
      - skill: status-report-assembler
        schedule: 'every 1d'
        wake_policy: always
```

Validation rules:

- `cron` is OPTIONAL; default is `[]`.
- `skill` REQUIRED; MUST reference an enabled skill on this persona.
- `schedule` REQUIRED; one of:
  - cron expression (5 space-separated fields, e.g. `0 9 * * *`)
  - interval (`every 30m`, `every 2h`, `every 1d`)
  - relative delay (`30m`, `2h`, `1d`)
  - ISO 8601 timestamp (`2026-03-15T09:00:00`)
- `wake_policy` REQUIRED; one of `always` or `pre_run_decides`.
- `pre_run` REQUIRED iff `wake_policy: pre_run_decides`; MUST be absent or null iff `wake_policy: always`.

**Audit-trail requirement (ADR 0021 §"Two safety constraints").** `pre_run.py` MUST emit an `audit_action="suppressed_wake"` row before printing `{"wakeAgent": false}` to stdout. The row captures `skill_name`, `pre_run_inputs_digest`, `decision_basis`, and `next_scheduled_at`. Audit-write failure forces the script to fall back to `{"wakeAgent": true}` so the agent wakes and the failure becomes visible. The mirror-don't-gate principle (ADR 0016) applies here: a silent suppression is structurally indistinguishable from a silently-broken pre_run.py, and the dashboard's watcher-health view alarms on a scheduled tick with no audit row.

## Webhook gateway (ADR 0021 Stream E)

**Added by [ADR 0021](../../adr/0021-leverage-hermes-native-primitives.md).** Inbound vendor webhook events (Filevine matter-created, Clio activity-logged, etc.) route to skill invocations via the overlay's `hermes-smd-webhook-router` plugin (`pre_gateway_dispatch` hook). Two schema additions wire this up:

1. **`connectors[].webhook_url`** — the URL the vendor pushes events to. Pattern is `https://hermes-{customer_id}.fly.dev/webhooks/{capability_slug}`; the validator enforces that `{customer_id}` matches the document's `customer_id` (cross-customer leakage vector if it ever doesn't, ADR 0009).

2. **`webhook_triggers[]`** — top-level array mapping inbound payloads to (persona, skill). Each entry: `{ source, event_type, skill, persona }`. `source` MUST match an adapter on a connector that carries inbound — either `webhook_url` configured (vendor push, Stream E) or a poll-driven inbound adapter (`msgraph`, whose delta poller stamps events into the same gate→router path per [ADR 0078](../../adr/0078-client-custody-email-channel.md) / [email-channel-seam](email-channel-seam.md) D1, so there is deliberately no webhook endpoint to pair against). `persona`/`skill` MUST reference real declarations.

```yaml
connectors:
  PracticeManagement:
    adapter: filevine
    backend: build:filevine
    webhook_url: 'https://hermes-smith-pi-firm.fly.dev/webhooks/practice_management'

webhook_triggers:
  - source: filevine
    event_type: matter.created
    skill: inbox-triage
    persona: marcus
  - source: filevine
    event_type: document.added
    skill: scope-creep-flagger
    persona: marcus
```

Validation rules:

- `connectors[].webhook_url` is OPTIONAL; default is null (pull-only connector).
- `webhook_triggers` is OPTIONAL; default is `[]`.
- A trigger whose `source` has no connector with `webhook_url` configured is rejected (`UnknownWebhookSource`) — either add the URL or drop the trigger.
- `event_type` is opaque to the validator; the source vendor defines the value (e.g. `matter.created`, `document.added`, `payment.received`). Must be a non-empty string.
- `webhook_triggers[].throttle` is OPTIONAL (#1781): `{ cooldown_minutes: <non-negative integer> }`. The overlay gate parks any delivery for a (source, event_type, matter) already inside an open cooldown window (202 + `WEBHOOK_SUPPRESSED`, reason `trigger-cooldown:<matter>`) — the deterministic break for write-then-echo loops (the seat's own `create_memo` echoing back as `matter.updated`). Unauthored = the gate's platform default (30 min, an integrity control); `cooldown_minutes: 0` disables for that trigger. Malformed blocks are rejected at authoring/provision time in BOTH validators (parity-pinned): the runtime resolver falls back to the platform default on malformed input, so a silently-accepted typo would silently replace the authored intent.

**Selection rule (Layer 2 fallback ladder).** Given a draft, a reviewer, and a recipient cohort, the transform picks profiles in this priority order:

1. **Per-(user, cohort)** — `users[i].voice_profile_id` × cohort id. Picked when samples ≥ `min_samples_per_cohort` (default `MIN_PROFILE_SAMPLE_COUNT`).
2. **Per-user general** — `users[i].voice_profile_id` aggregated across that user's samples regardless of cohort.
3. **Customer general** — the firm-wide composite (every sample, every user, every cohort).

The fallback is enforced in `VoiceProfileBundle.select(reviewer_user_id, recipient_cohort)`. Each step's outcome is recorded in `TransformResult.selected_voice_user_id` and `TransformResult.selected_voice_cohort` so the audit row and dashboard surface know which profile actually shaped the draft.

**Blind-test gate scoping.** The voice-gate harness (`runVoiceGate({ ..., cohort })`) already scopes per cohort; the blind-test gate (#823) is exercised per cohort separately. The schema additions in this PR feed the harness's cohort vocabulary; the harness's three-state contract (pass / near-pass / fail) is unchanged.

## Observability (ADR 0023)

**Added by [ADR 0023](../../adr/0023-operator-per-customer-observability.md) Wave 1.** Optional block parallel to `logging:` and `pause:`. Covers vendor wiring for the per-customer observability stack (Sentry on Machine, healthchecks.io push heartbeat).

```yaml
observability: # OPTIONAL
  sentry: # OPTIONAL
    enabled: <boolean> # default true
  health: # OPTIONAL
    period_seconds: <int> # default 60
    grace_minutes: <int> # default 5
```

Field rules:

- The entire `observability:` block is optional. Missing fields fall back to documented defaults. Setting `observability:` to an empty object is equivalent to omitting it.
- `sentry.enabled` defaults to `true`. The Machine initializes the Sentry SDK with a `tenant=<customer_id>` scope tag at boot (Python `sentry-sdk` package in the overlay). Setting to `false` disables Sentry init at boot — used for synthetic-customer fixtures and CI smoke runs that should not emit to the shared `smd-operator` project.
- `sentry.send_default_pii` and `sentry.before_send` scrub list are NOT in `customer.yaml` — they are locked in the overlay's Sentry init module per ADR 0023 §"Cross-cutting calls" #11 and gated by a pytest regression suite. Per-customer scrub overrides are a deliberate follow-on; no compliance posture allows weakening the default scrub.
- `health.period_seconds` is the cadence of the Machine's outbound POST to its assigned healthchecks.io URL. Defaults to 60.
- `health.grace_minutes` is the late-window before healthchecks.io fires its grace-expired webhook. Defaults to 5.

**Sentry error-spike thresholds are NOT in `customer.yaml`.** They are owned by Sentry's native alert rules, configured per-customer in Sentry UI by SMD ops. Auto-provisioning from `customer.yaml` is a follow-on triggered by customer-count scale (~20+ customers).

**No `alert_webhook` field in Wave 1.** Customer-configurable external destinations are deferred per ADR 0023 §"Cross-cutting calls" #9. The admin dashboard at `/admin/operator/costs/` is the always-on monitoring surface across all customers; Captain ops escalation uses the existing Resend path on `workers/cost-anomaly`. Reintroduction is a follow-on driven by real customer demand with per-destination adapters, not a single shape that doesn't fit any real webhook target.

## Safety — cost breaker (ADR 0062)

**Added by [ADR 0062](../../adr/0062-operator-cost-plane.md) (ss-console #1661).** Optional block; these are integrity controls protecting SMD's own spend (ADR 0035 posture: unauthored means the platform default applies — never fail-open). Live-read from the volume per use (ADR 0044 read-fresh posture), so an authored change applies without a restart.

```yaml
safety: # OPTIONAL
  sticky_stop: # OPTIONAL
    cost_cap_daily_cents: <int> # default 5000 ($50/day)
    inbound_daily_cap: <int> # default 200
    web_search_daily_cap: <int> # default 200; per-seat WebSearch fair-use ceiling (ADR 0070)
```

Field rules:

- `sticky_stop.cost_cap_daily_cents` is the base of the Machine-wide daily spend ladder enforced on the durable-job path (real provider-reported cents): warn at 80%, soft-stop at 100% (exposure pinned to draft-for-review), hard-stop at 200% (segments refuse; jobs dead-letter to `needs_review`; the webhook gate parks inbound). Must be a positive integer; a malformed value falls back to the platform default with a logged warning.
- `sticky_stop.inbound_daily_cap` is the maximum verified vendor-webhook deliveries routed to the agent per UTC day. Overflow is acknowledged (202), audited (`INVARIANT_VIOLATION` with `gate_inbound_park` metadata), and NOT routed — never a silent drop. Same positive-integer/fallback rule.
- `sticky_stop.web_search_daily_cap` ([ADR 0070](../../adr/0070-web-search-shared-connector-divergent-defaults.md)) is the per-seat fair-use ceiling on `WebSearch` (`native:brave-free`) calls per UTC day. On the Hosted Agent's free Brave tier this is a courtesy bound (Brave's own free quota is the hard stop — there is no spend to run away, so "your only bill is Anthropic" holds by construction). Same positive-integer/fallback rule (default 200). Authored where `WebSearch` is enabled (`_hosted-template`); irrelevant on a seat with `WebSearch` unauthored. **Enforcement status:** the field is authored and read into config today; a dedicated per-call counter at the native `web_search` call site is a follow-on. The interim backstop is the Machine-wide cost breaker (`cost_cap_daily_cents`) — every search rides an LLM turn, so a runaway search loop trips the cost ladder regardless.
- The ladder percentages (80/100/200) are platform semantics, not customer-authorable. Recovery from a hard stop is Captain `clear()` (audited `AGENT_RESUMED`), never automatic.
- Materialization is runtime live-read (`CustomerConfig.sticky_stop` in the overlay), not a `translate.py` step — see `operator/contracts/customer-yaml-blocks.yaml` (`safety`).

## Send policy — reply-channel rate caps (#2070)

**Added by ss-console #2070** (the sustained-dialogue program; closes the caps half of #2069). Optional block. Governs **only** the `hermes-smd-reply` relay — the autonomous/confirm send lane has its own controls and never consults this limiter, so every field here is a _reply_ bound, never a seat-wide one. Live-read from the volume per reply (ADR 0044), so authoring it applies without a restart.

```yaml
send_policy: # OPTIONAL
  reply: # OPTIONAL
    internal_exempt: <bool> # default false
    per_sender_max: <int> # default 3
    per_sender_window_seconds: <number> # default 600
    global_max: <int> # default 20
    global_window_seconds: <number> # default 3600
    backstop_max: <int> # default 0 (disabled)
    backstop_window_seconds: <number> # default 3600
  held_release: # OPTIONAL
    enabled: <bool> # default false
    ttl_seconds: <int> # default 86400
```

Why it exists: the caps were hardcoded platform constants that treated a rostered colleague identically to a stranger, so a sustained email dialogue (an attorney iterating on a draft) went silent at the fourth exchange — held, never released, nobody notified (observed in the 2026-07-30 burst rehearsal).

Field rules:

- `reply.internal_exempt` exempts senders the recipient classifier resolves as **INTERNAL** (`scope.inbound_allow_from`) from the per-sender and global windows. Exempt sends are still counted against — and bounded by — the reply backstop. Classification failures are never internal (fail-closed), so an unclassifiable sender keeps the caps.
- `reply.per_sender_max` / `per_sender_window_seconds` and `reply.global_max` / `global_window_seconds` are the rolling windows applied to CLIENT / VENDOR / OUTSIDE senders (and to everyone when `internal_exempt` is false). Counts must be non-negative integers; windows must be positive numbers.
- `reply.backstop_max` / `backstop_window_seconds` bound the reply channel across **all** sender classes; `0` disables. This is the runaway bound that survives the internal exemption.
- `held_release.enabled` persists rate-held replies and auto-releases them in per-sender FIFO order once the window clears (O2); `ttl_seconds` expires a reply that never gets its window.
- **Whole-block fail-closed:** the on-box resolver (`shared/send_policy.resolve_send_policy`) resolves the ENTIRE block to platform defaults on any fault — including dropping an authored `internal_exempt`. A typo can only ever tighten a seat, never loosen it. This validator surfaces the same faults at authoring time so the silent tightening never happens unnoticed.
- Materialization is runtime live-read (`CustomerConfig.send_policy` → `RateLimiter.check` in the overlay), not a `translate.py` step — see `operator/contracts/customer-yaml-blocks.yaml` (`send_policy`).

## Case manager (the deadline jobs)

**Added 2026-09-25** for the case-manager spec ([case-manager-deadline-work.md](./case-manager-deadline-work.md)). Optional block. It turns on the deadline jobs a case manager owns, each at the level the firm chose, in the portal's tier language: `surfaces` ("Surfaces it": says so, does nothing), `prepares` ("Prepares it for you": a draft or a proposal a person approves), `handles` ("Handles it": does it).

```yaml
case_manager: # OPTIONAL; absent = every job off
  own_tasks: # OPTIONAL; the Operator's own tasks (Job 1a)
    level: <surfaces|prepares|handles> # required
    legacy_task_ids: [<Smokeball task id>, ...] # optional; distinct, non-empty, at most 200
  task_cleanup: # OPTIONAL; the weekly proposal per attorney (Job 1)
    level: <surfaces|prepares|handles> # required
    keep_quiet_days: <int 1..365> # optional; the routine's own when absent
    max_lines: <int 1..30> # optional; the routine's own (30) when absent
  date_prep: # OPTIONAL; prepare for a date entering its window (Job 2)
    level: <surfaces|prepares|handles> # required
    window_days: <int 1..90> # required; no pack default
    steps: # optional; an unlisted step is never offered
      <step>: <surfaces|prepares|handles> # never above date_prep.level
  quiet: # OPTIONAL; routine work done and mentioned in one line (Job 3)
    level: <surfaces|prepares|handles> # required
```

`<step>` is one of `binder_assemble`, `witness_list_finalize`, `exhibit_list_finalize`, `records_refresh`, `motion_calendar_refresh`, `discovery_status_refresh` (the closed catalog, `operator/skills/date-prep-brief/references/decision-catalog.md`).

Field rules:

- **Absent is a state, not an error.** No block: every job is off, and `deadline-miss-escalator` renders exactly as it did before the block existed. An absent sub-block turns off that job alone (ADR 0035, no imposed default).
- **Strict keys.** An unknown key at any level is refused (`InvalidCaseManager`), because a misspelled job (`date_perp:`) would otherwise read as "that job is off" and nobody would learn why the prep never arrived.
- **`legacy_task_ids`** names tasks the Operator created before its `[Operator]` subject stamp existed (2026-08-01). A Smokeball task read carries no creator, so this authored list is the only other signal that a task is the Operator's own.
- **A step never exceeds its job.** A step authored above `date_prep.level` is refused: a firm that chose "Prepares it for you" for prep has not chosen "Handles it" for any part of it.
- Materialization is config-as-data: the `task-list-keeper`, `date-prep-brief` and `deadline-miss-escalator` pre_runs read the block off the seat's own `/var/lib/smd-config/customer.yaml` at each tick (`operator/contracts/customer-yaml-blocks.yaml`, `case_manager`). Not portal-editable yet: the levels change by PR (`src/lib/portal/operator/customer-yaml-editor.ts` keeps them locked).

## Failure modes

| Condition                                                             | Validator behavior                                                                                                                                                                              |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing required field                                                | Reject with `MissingField` error naming the JSONPath                                                                                                                                            |
| Required string is empty                                              | Reject with `EmptyField` error                                                                                                                                                                  |
| Enum field value not in accepted set                                  | Reject with `EnumViolation` error listing accepted values                                                                                                                                       |
| `customer_id` does not match `^[a-z0-9][a-z0-9-]{0,31}$`              | Reject with `InvalidSlug` error                                                                                                                                                                 |
| `personas` array is empty OR has no `status: active` entry            | Reject with `MissingActivePersona` error                                                                                                                                                        |
| Persona slug duplicated within `personas[]`                           | Reject with `DuplicatePersonaSlug` error                                                                                                                                                        |
| `connectors` key not in `CapabilityName` union                        | Reject with `UnknownCapability` error                                                                                                                                                           |
| `trust_ceiling` raises above SKILL.md authored ceiling                | Reject with `TrustCeilingExceeded` error (validator surfaces both values; ceiling-floor lookup happens at provision time, not in this validator at v1)                                          |
| Secret pattern matched in any value                                   | Reject with `SecretDetected` error naming the JSONPath + pattern category; the matched **substring is NOT echoed** in the error (avoid log/transcript leak)                                     |
| Banned field name encountered                                         | Reject with `BannedFieldName` error naming the JSONPath                                                                                                                                         |
| `token_ref` does not begin with `infisical:`                          | Reject with `InvalidTokenRef` error                                                                                                                                                             |
| `memory.d1_namespace` ≠ `customer_id`                                 | Reject with `IsolationViolation` error (cross-Machine query prevention; see [r2-vectorize-naming.md](./r2-vectorize-naming.md) + [ADR 0009](../../adr/0009-cross-machine-query-prohibition.md)) |
| `memory.r2_vault_path` ≠ `vaults/{customer_id}/`                      | Reject with `IsolationViolation` error                                                                                                                                                          |
| `memory.vectorize_index` ≠ `hermes-{customer_id}-vault`               | Reject with `IsolationViolation` error                                                                                                                                                          |
| `vertical: law-firm` without `practice_areas`                         | Reject with `MissingField` error citing `practice_areas`                                                                                                                                        |
| `pause.active: true` without `pause.reason`                           | Reject with `MissingField` error citing `pause.reason`                                                                                                                                          |
| `users[].voice_profile_id` malformed slug                             | Reject with `InvalidSlug` error                                                                                                                                                                 |
| Duplicate `users[].voice_profile_id` across users                     | Reject with `DuplicateVoiceProfileId` error (per-user attribution model — two users cannot share a profile)                                                                                     |
| `voice_cohorts:` present but `voice_cohorts.cohorts` empty            | Reject with `EmptyList` error (the field's purpose is to declare cohorts; an empty list is an authoring mistake)                                                                                |
| `voice_cohorts.cohorts[]` entry malformed slug                        | Reject with `InvalidSlug` error                                                                                                                                                                 |
| Duplicate `voice_cohorts.cohorts[]` entry                             | Reject with `DuplicateVoiceCohort` error                                                                                                                                                        |
| `voice_cohorts.min_samples_per_cohort` ≤ 0 or non-integer             | Reject with `TypeMismatch` error                                                                                                                                                                |
| Duplicate `bundles[].slug` within a persona                           | Reject with `DuplicateBundleSlug` error (ADR 0021 Stream D)                                                                                                                                     |
| `bundles[].skills[]` references skill not on the persona              | Reject with `UnknownBundleSkill` error (ADR 0021 Stream D)                                                                                                                                      |
| `cron[].schedule` not parseable (cron expr/interval/delay/ISO)        | Reject with `InvalidCronSchedule` error (ADR 0021 Stream B)                                                                                                                                     |
| `cron[].skill` references skill not on the persona                    | Reject with `UnknownCronSkill` error (ADR 0021 Stream B)                                                                                                                                        |
| `cron[].wake_policy` not in {`always`, `pre_run_decides`}             | Reject with `InvalidCronWakePolicy` error (ADR 0021 Stream B)                                                                                                                                   |
| `cron[].pre_run` set with `wake_policy: always` (or vice versa)       | Reject with `InvalidCronWakePolicy` error (ADR 0021 Stream B)                                                                                                                                   |
| `connectors[].webhook_url` not matching customer-bound pattern        | Reject with `InvalidWebhookUrl` error (ADR 0021 Stream E)                                                                                                                                       |
| `connectors[].webhook_url` embeds slug ≠ `customer_id`                | Reject with `IsolationViolation` error (ADR 0021 Stream E + ADR 0009)                                                                                                                           |
| `webhook_triggers[].source` names no inbound-carrying connector       | Reject with `UnknownWebhookSource` error (ADR 0021 Stream E). Inbound-carrying = `webhook_url` set, OR a poll-driven adapter (`msgraph`, ADR 0078 D1)                                           |
| `webhook_triggers[].persona` does not match any declared persona      | Reject with `UnknownWebhookPersona` error (ADR 0021 Stream E)                                                                                                                                   |
| `webhook_triggers[].skill` not an enabled skill on the target persona | Reject with `UnknownWebhookSkill` error (ADR 0021 Stream E)                                                                                                                                     |

| `case_manager` unknown key, bad level, bad bound, or a step above its job | Reject with `InvalidCaseManager` error naming the JSONPath |

All errors are returned as a list; the validator does not short-circuit on the first error. Authors get the full picture in one round-trip.

## Verification

### Runtime validator

[`src/lib/operator/customer-yaml/validator.ts`](../../../src/lib/operator/customer-yaml/validator.ts) — TypeScript validator. Consumes the parsed YAML as an `unknown` (the consumer chooses its YAML parser — portal uses one, Hermes uses another per [ADR 0012](../../adr/0012-customer-yaml-storage.md) §4) and returns a `ValidationResult` with a typed `CustomerYaml` on success or a list of `ValidationError` entries on failure. Hand-rolled (no schema-library dependency) — the contract is narrow enough that a 200-line validator with explicit checks reads better than a 50-line schema declaration whose rules you have to translate back to docs.

### Secret detector

[`src/lib/operator/customer-yaml/secret-detector.ts`](../../../src/lib/operator/customer-yaml/secret-detector.ts) — accepts raw file text plus an `allowlist` of field paths and returns a list of `SecretFinding` entries. Pre-commit hooks and CI both call this directly; the validator also invokes it as the first pass on the structural parse. Error messages name the line and pattern category but **never echo the matched substring** — a precaution that keeps secret values out of CI logs, terminal history, transcripts, and the gitleaks-of-the-validator-output failure mode.

### Test surfaces

- [`tests/customer-yaml-validator.test.ts`](../../../tests/customer-yaml-validator.test.ts) — round-trips valid YAML to typed shape; rejects every category in _Failure modes_ above; verifies aggregate error list (validator does not short-circuit); verifies error messages never echo matched secret substrings.
- [`tests/customer-yaml-secret-detector.test.ts`](../../../tests/customer-yaml-secret-detector.test.ts) — covers each pattern category, the field-name ban, the allowlist, and the no-echo rule.

### CI wiring (deferred to ADR 0012 follow-on PR)

Pre-commit hook + CI workflow live with the canonical configs repo per [ADR 0012](../../adr/0012-customer-yaml-storage.md) §5. The validator + secret detector exported from `src/lib/operator/customer-yaml/` are the modules that workflow imports. The repo + workflow themselves are out of scope for [#790](https://github.com/venturecrane/ss-console/issues/790) — they land in the follow-on PR specified by ADR 0012 _Implementation_ phase 4.

## Implementation notes

- Schema is consumer-agnostic. Both the portal (TypeScript, [`src/lib/portal/customer-config.ts`](../../../src/lib/portal/customer-config.ts) — reads the D1 projection) and Hermes (Python pydantic — reads the YAML directly from R2) build typed representations against this spec. The TypeScript validator in this PR is the portal-side and the CI-side check; a Hermes-side pydantic validator is the Hermes-side check. Both validators target the same contract.
- The `synthetic:` backend prefix is supported so dev/test fixtures can wire fake adapters; CI rejects `synthetic:` in production-targeted YAML via a separate `mode: 'production' | 'development'` flag on the validator, defaulting to production.
- Per-customer schema migration is a PR in the configs repo, not a database migration. Schema version bump triggers a CI sync re-projection for every customer ([ADR 0012](../../adr/0012-customer-yaml-storage.md) §8).

## Cross-references

- [`d1-schema.md`](./d1-schema.md) — per-customer Hermes D1 contract (`persona_slug` nullable columns)
- [`dashboard-roles.md`](./dashboard-roles.md) — `users[].role` vocabulary
- [`r2-vectorize-naming.md`](./r2-vectorize-naming.md) — memory.\* isolation invariants
- [`oauth-lifecycle.md`](./oauth-lifecycle.md) — how `token_ref` resolves at provision time
- [ADR 0006](../../adr/0006-capability-adapter-pattern.md) — capability-interface + adapter pattern (the `connectors:` shape)
- [ADR 0007](../../adr/0007-per-customer-machine-isolation.md) — per-customer Machine isolation
- [ADR 0009](../../adr/0009-cross-machine-query-prohibition.md) — cross-Machine query prohibition (the `memory:` invariants)
- [ADR 0011](../../adr/0011-multi-persona-per-customer.md) — multi-persona per customer (the `personas:` array shape)
- [ADR 0012](../../adr/0012-customer-yaml-storage.md) — git as source of truth (validator runs at PR time and in pre-commit)
