# Smokeball API tool surface — pinned for the law wedge (Smokeball backend)

**Purpose.** Pin the Smokeball practice-management API's actual contract so the wedge's fixtures and skill bodies can run against a real I/O shape, not an imagined one — the Smokeball analogue of `clio-surface.md`. Unlike Clio (a community MCP we adopt), **there is no off-the-shelf Smokeball MCP — we author one** (`mcp:smokeball`, ADR 0020: MCP-first, build a server only where no acceptable MCP exists). So this doc does double duty: it pins **Smokeball's REST contract** AND specifies the **tool surface the `mcp:smokeball` server exposes** to the skills.

**Source.** [Smokeball API docs](https://docs.smokeball.com) (`docs.smokeball.com`, REST/JSON/OAuth 2.0), pinned **read-only from published docs on 2026-06-13** — no app, no live tenant, no OAuth round-trip yet. Smokeball publishes a **staging environment** (`stagingapi.smokeball.com`), which is the connect-step target.

> Re-pin this file whenever the API or the `mcp:smokeball` server version bumps. The connect step replaces "published-doc assumptions" here with a real staging-tenant round-trip; until then, treat every shape below as the **contract of record but unverified against a live tenant**. Items marked ⚠️ or ASSUMED must be confirmed at connect.

## CONFIRMED LIVE — 2026-06-23 (mcp:smokeball v0.1.0, staging round-trip)

The `mcp:smokeball` connector was built against the OpenAPI spec and round-tripped against the real US staging tenant (`stagingapi.smokeball.com`) with the `SMOKEBALL_STAGING_*` credentials in Infisical `/ss`:

- **Auth CONFIRMED.** `client_credentials` grant works: `POST {auth_host}/oauth2/token` with `Authorization: Basic base64(client_id:secret)` + `Content-Type: application/x-www-form-urlencoded`, body `grant_type=client_credentials` → `access_token` (`token_type: Bearer`, `expires_in: 21600` = 6h). AWS Cognito.
- **Request contract CONFIRMED.** Every API call needs **two** headers: `x-api-key` (the `SMOKEBALL_STAGING_API_KEY`) + `Authorization: Bearer`. `GET /matters?Limit=1` returned 200.
- **Pagination envelope CONFIRMED.** List responses are a HATEOAS envelope `{ value: [...], offset, limit, size, first, previous, next, last, href }` — not a bare array. Skills read `value`.
- **Path corrections (OpenAPI vs the guesses above).** `/mattertypes` (not `/matter-types`); files at `/matters/{matterId}/documents/files` (+ `/{fileId}`, `/{fileId}/download`) — `get_file`/`get_download_url` need **matterId + fileId**, not a flat file id; webhooks at `/webhooks` + event types at `/webhooks/types`; query params are **PascalCase** (`Status`, `IsLead`, `MatterTypeId`, `ContactId`, `UpdatedSince`, `Sort`, `Limit`, `Offset`); tasks filter by `IsCompleted` (bool), `MatterId`.
- **Still ASSUMED (verify when the wedge wires writes).** `create_memo` request-body field (the server sends `{text}`; the live memo schema is an inline object the OpenAPI did not expand); the `UpdatedSince` .NET-ticks-vs-ISO format; the stage<->matter-type join (`get_stage_to_matter_mappings` hits `/stages`). **Added 2026-06-25 (write cut — bodies match the OpenAPI DTOs but are UNVERIFIED against a live tenant):** `create_event`/`update_event` (`EventDto`: `subject`, `startTime`, `endTime`, `matterId`, `type=Normal`, ...); `create_event_reminder` (`ReminderDto`: `offset`, `offsetTypeId` — unit encoding unconfirmed); `create_task`/`update_task` (`TaskDto`: required `staffId`, `dueDateOnly`); `create_folder` (`FolderDto`: required `name`, `parentFolderId`). The `add_file`/`delete_file` path 403'd on staging at the time; since then `create_folder` + `add_file` have delivered sixteen chronology packages into the A&P production tenant (August 2026), so the write path is verified on prod. **Task DTO VERIFIED on prod 2026-08-31** (supervised `[SMD-PROBE]` rehearsal, created + completed + torn down; vfy_01M1CWACT2NSB1WFSZXD3KQK5F), three vendor truths: `POST /tasks` answers **202 with `{href, id}` and materializes asynchronously** (an immediate read 404s — read back with patience, not once); the task read **never echoes `staffId`** (ownership is write-only; `assignees` came back empty even with `staffId` set at create); and `PUT /tasks/{id}` is a **full replace** — `StaffId` required on every update, `CompletedByStaffId` required when completing, and every omitted field is cleared on the tenant (a bare completion PUT nulled the subject, due date, and matter link — `update_task` now read-merges before sending). Remaining ASSUMED: the event and reminder DTO details.

## Base URLs and auth (US region — confirm region with the firm)

|           | Production                   | Staging / dev                            |
| --------- | ---------------------------- | ---------------------------------------- |
| Auth host | `https://auth.smokeball.com` | `https://datastaging-auth.smokeball.com` |
| API host  | `https://api.smokeball.com`  | `https://stagingapi.smokeball.com`       |

Do **not** mix region/environment hosts (AU = `.com.au`, UK = `.co.uk`). The `mcp:smokeball` server takes region+environment as launch config and selects the host pair.

**Auth.** OAuth 2.0, two grants:

- **Authorization Code Grant** — user-delegated; the firm authorizes our app and we act as a Smokeball user. This is the pilot path (the firm/trial tenant grants consent).
- **Client Credentials Grant** — server-to-server, outside a user context.

App registration is **self-service** at `https://console.smokeball.com` (create a private app → receive `client_id`/`client_secret`). The public partner-program "registration of interest" form is only for marketplace-distributed apps — not required for a firm-specific pilot. ⚠️ Exact **scope strings** and token endpoint path are ASSUMED-standard and must be confirmed at connect against the authentication pages.

**Token lifetimes — CONFIRMED 2026-09-02** (`vfy_01M1HBKA7DMVT108T6621WRG6P`, fetched from [the authorization-code-grant page](https://docs.smokeball.com/docs/api-docs/2t26gcuuqf1wk-authorization-code-grant)). Access token 60 minutes. Refresh token **180 days** — but keyed to the ISSUE date: _"The 180-day expiry took effect on 24 August 2026 and applies to refresh tokens issued on or after that date. Refresh tokens issued before then remain valid for 30 days."_ Smokeball's documented refresh response carries no `refresh_token` field, so nothing rotates between consents and the expiry cannot be extended by use. `client_credentials` issues no refresh token at all. This value was carried as ASSUMED from 2026-06-13 until a token died on it (pilot-smokeball, 2026-09-01); it is now vendor-read, not inferred.

## `mcp:smokeball` tool surface (Smokeball-native names → REST endpoints)

We author these tools. Names are **Smokeball-native** (the Operator is a Smokeball expert, not a Clio facade); the wedge skills are updated to call them in the fluency pass. Read vs. write split mirrors the fail-closed wedge posture.

| Capability            | Read tools → endpoint                                                                                                                                      | Write tools → endpoint                                                                                                                                                                                                                                                                   |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth                  | `auth_status`                                                                                                                                              | `authenticate`, `logout` (`/logout`)                                                                                                                                                                                                                                                     |
| Matters               | `list_matters(status, isLead, matterTypeId, contactId, updatedSince, sort, limit, offset)` → `GET /matters`; `get_matter(matter_id)` → `GET /matters/{id}` | **`create_matter(description, matter_type_id, client_contact_id, number)`** - IMPLEMENTED 2026-08-21 (commitment, confirm-gated): the Operator's OWN internal matter only, never a client's case; `patch_matter(...)` stays out of scope                                                 |
| Matter types / stages | `list_matter_types()` → `GET /matter-types`; `get_stage_sets()` / `get_stage_to_matter_mappings()` → `/stages/*`                                           | —                                                                                                                                                                                                                                                                                        |
| Contacts              | `get_contacts(query, limit, offset)` → `GET /contacts`; `get_contact(contact_id)`; `get_contact_relations(...)`                                            | `create_contact(...)` ⚠️ — gated, draft-only                                                                                                                                                                                                                                             |
| Tasks                 | `list_tasks(matter_id, is_completed, updated_since, limit, offset)` → `GET /tasks`; `get_task(task_id)`                                                    | **`create_task(staff_id, subject, matter_id, due_date→dueDateOnly, ...)`, `update_task(task_id, ..., staff_id)`** — IMPLEMENTED 2026-06-25; DTO verified on prod 2026-08-31 (POST is 202-async; PUT is full-replace, so `update_task` read-merges and needs `staff_id`) (internal_write) |
| Calendar / events     | `list_events(matter_id, from_, to, updated_since, ...)` → `GET /events`                                                                                    | **`create_event`, `update_event` (non-recurring only), `create_event_reminder`** — IMPLEMENTED 2026-06-25 (internal_write); no delete-event                                                                                                                                              |
| Staff / users         | `search_staff(name, enabled, limit)` → `GET /staff`; `get_staff(staff_id)`                                                                                 | `create_staff` / `create_user` — **out of scope** (not used by the wedge)                                                                                                                                                                                                                |
| Roles / relationships | `get_roles_on_matter(matter_id)`, `get_relationships_on_matter(matter_id)`                                                                                 | —                                                                                                                                                                                                                                                                                        |
| Files / documents     | `get_files_on_matter(matter_id)` → `GET /matters/{id}/documents/files`; `get_file`; `get_download_url`; **`list_folders(matter_id)`**                      | `add_file(...)` (two-stage upload), `delete_file(...)` (destructive); **`create_folder(matter_id, name, parent_folder_id)`** — IMPLEMENTED 2026-06-25 (internal_write)                                                                                                                   |
| Memos                 | `get_memos_on_matter(matter_id)`                                                                                                                           | `create_memo(matter_id, ...)` — the internal-log write (the Clio `create_note` analogue)                                                                                                                                                                                                 |
| Trust / bank accounts | `get_bank_accounts()`; **`get_matter_balances(bank_account_id, matterId)`** → `GET /bankaccounts/{id}/matter-balances`                                     | `create_transaction`, `protect_funds`, `unprotect_funds` — **hard-banned** (zero fund movement, enforced as a `fails` invariant)                                                                                                                                                         |
| Billing (AR)          | `get_matter_billing_config(matter_id)`; `get_fees(...)`; `get_expenses(...)`                                                                               | —                                                                                                                                                                                                                                                                                        |
| Webhooks              | `get_webhook_subscriptions()`, `get_event_types()`                                                                                                         | `create_webhook_subscription(...)` (provisioning-time, drives event skills)                                                                                                                                                                                                              |

Pagination is `limit` (default **500**, max 500) + `offset`; `updatedSince` is **.NET ticks** format (not ISO) on `list_matters`; `sort` takes `asc(Field)`/`desc(LastUpdated)`.

## Smokeball matter shape (real fields — verified vs. published docs, not a live tenant)

`id` (UUID) · `number` (may be blank) · `matterTypeId` (→ practice area) · `description` · `status` (`Open|Pending|Closed|Deleted|Cancelled`) · `clientIds[]` · `otherSideIds[]` · `openedDate` (ISO 8601) · **`isLead` (bool — leads vs. matters, first-class)** · **`personResponsibleStaffId` (responsible attorney — a direct field)** · `personAssistingStaffId` · `versionId` (optimistic concurrency) · `title` (auto-generated). PATCH-only: `externalSystemId`, `clientCode`, `branchId`, `originatingStaffId`, `supervisorStaffId`.

**Trust balances** (`GET /bankaccounts/{id}/matter-balances`, per matter): `balance` · `protectedBalance` · `availableBalance` (= balance − protected) · `unpresentedChequesBalance` · `lastUpdated` · `matter` (link).

## Smokeball is a STRONGER wedge backend than Clio — three Clio findings dissolve

The Clio connect step (clio-surface.md findings 2–3 + the trust note) carried three real weaknesses forward. Smokeball resolves all three natively:

1. **Responsible attorney is readable.** Clio Finding 2: `responsible_attorney` was a create-only input, never returned by reads — any skill needing "who owns this matter" couldn't get it without a field-widen fork. Smokeball returns **`personResponsibleStaffId`** on the matter directly. `matter-status-responder`, `stalled-matter-nudge`, `consult-scheduler` get attorney attribution for free.
2. **Last-activity signal exists.** Clio Finding 3 (the sharpest): no `updated_at` on a matter, so "gone quiet" detection for `stalled-matter-nudge` was thinner than the fixtures assumed. Smokeball offers **`updatedSince` filtering on `list_matters`** and `LastUpdated` on balances/tasks — a real recency signal. `stalled-matter-nudge`'s trigger becomes sound, not a heuristic-with-a-caveat.
3. **Trust is native.** Clio had no IOLTA/trust tool; `trust-balance-nudge` rode a separate `build:lawpay` read (now deleted). Smokeball's **`get_matter_balances`** returns `availableBalance`/`protectedBalance` per matter directly. The low-trust flag = `availableBalance` vs. the firm's authored floor — **live, no second connector.** AR stays separate (fees/expenses/billing-config), so the AR-vs-trust distinction the wedge insists on holds cleanly.

Bonus: **`isLead`** makes `new-matter-intake` map onto Smokeball's _native_ lead→matter conversion — the intake wedge is exactly the shape of Smokeball's own intake model.

## Calendar — Smokeball HAS an Events API (corrected 2026-06-25)

An earlier version of this doc claimed Smokeball had no calendar resource. That
was wrong. The OpenAPI spec exposes an **Events (calendar) resource group**:
`GET /events`, `POST /events` (create), `PUT /events/{id}` (update), and
`POST /events/{id}/reminders` — wired into the connector 2026-06-25 as
`list_events` / `create_event` / `update_event` / `create_event_reminder`. Two
limits: **create/update is non-recurring (`type=Normal`) only** (recurring events
are read-only), and **no delete-event** is documented. E-Sign/signature status is
still inferred from the signed document landing in the matter (no in-flight status
API).

**This opens a calendar-source design decision (OPEN, see RESEARCH-SYNTHESIS):**
the Operator can now consolidate deadlines into the **Smokeball calendar
directly** (no M365 build) — which matches Christa's "single source of truth in
Smokeball" ask — rather than routing every calendar write through an M365 binding.
M365/Graph is still required for the **inbound-email-discovery** watch (the genuine
Smokeball blind spot) and may still be wanted for two-way Outlook sync. Decide
Smokeball-events vs M365 calendar before wiring the deadline skills; do not assume
M365 is the calendar source.

**Task-based deadlines also come from Smokeball** (`list_tasks` `due_date`,
`create_task`/`update_task`). So the deadline engine can write both a tracked
**task** and a calendar **event** (with a reminder cascade) into Smokeball.

## Wedge skill → Smokeball dependency map (this phase: reads + conservative writes only)

| Wedge skill                | Smokeball reads                                                                                                                      | Writes (this phase)                                                                                      |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `new-matter-intake`        | `get_contacts` (dedupe + conflict detect), `get_contact`, `list_matters(isLead)`/`get_matter` (conflict cross-check), `search_staff` | `create_memo` (internal log) only; **matter/lead drafted, not `create_matter` autonomously**             |
| `consult-scheduler`        | calendar via binding (Google/M365), `get_matter`, `search_staff`                                                                     | none autonomous; calendar entry **surfaced for human confirm**                                           |
| `engagement-letter-chaser` | `get_matter`, `get_files_on_matter` (e-sign status via ESign, fixture-supplied this phase)                                           | `create_memo` (log on signature)                                                                         |
| `matter-status-responder`  | `get_matter` (incl. `personResponsibleStaffId`), `list_tasks`, calendar via binding, `get_files_on_matter`                           | none                                                                                                     |
| `trust-balance-nudge`      | **`get_matter_balances`** (`availableBalance`) for the low-trust flag; `get_matter` for context                                      | none — **zero fund-movement, `protect/unprotect/create_transaction` hard-banned as a `fails` invariant** |
| `stalled-matter-nudge`     | `list_matters(updatedSince)`, `get_matter`, `list_tasks`                                                                             | `create_memo` (log the nudge)                                                                            |
| `matter-status-digest`     | `list_matters` bucketed by `status`/stage, `list_tasks`, `get_matter_balances` (low-trust), calendar via binding                     | `create_memo` (internal digest)                                                                          |
| `deadline-and-sol-tracker` | `list_tasks` (`due_date`) for authored deadlines; calendar via binding for appointments                                              | none (internal surface)                                                                                  |

**Conflict detect-and-halt** (in `new-matter-intake`) is read-only: `get_contacts(query)` + `list_matters` name cross-check surfaces a hit with no write.

**`medical-chronology-maintainer` (ss#2616, the chronology-package request path):** reads `list_matters` + `get_contacts`/`get_contact` (the dual-probe matter resolution: number scan ∩ client-name probe, exactly one match or surface the candidates as prose), `get_matter` (incident fields, `personResponsibleStaffId`), `get_files_on_matter`, `list_folders`, `get_memos_on_matter`, `search_staff`/`get_staff`; writes `create_memo` (the running chronology, confirmed by read) and — deliver mode only — `create_task` for the responsible attorney (read back by `list_tasks`). The package itself is built and filed by the on-seat runner; the skill reaches it through the seat-local broker tools `medchron_job_submit` / `medchron_job_status` / `medchron_allowance` (not Smokeball tools; classified INTERNAL_WRITE / READ / READ in the overlay).

## Write posture (unchanged from the wedge — fail-closed)

Every client-/tribunal-bound message follows the firm's authored `external_send` ceiling (ADR 0035; fail-closed when unauthored). Every Smokeball _write_ (`create_matter`, `create_task`, `create_contact`, file/document writes) is **gated / draft-and-surface** until the connect step proves the call succeeds against staging AND the engagement authors it on (ADR 0035, no imposed defaults). Trust-account writes (`protect_funds`/`unprotect_funds`/`create_transaction`) are **never** authored on — a `fails` invariant, not a default. `create_memo` (internal log) is the one write the wedge uses this phase, analogous to Clio's `create_note`.

## ASSUMED — unverified vs. a live Smokeball tenant (scope the connect-step diff)

- **OAuth scope strings, token endpoint** — ASSUMED standard; confirm against the authentication-overview / grant pages and the created app's console config. (Refresh lifetime is no longer on this list: CONFIRMED 2026-09-02, see the auth section above.)
- **`get_tasks` exact shape** — `due_date`/`dueDate` key, status enum values, and whether tasks carry a matter link vs. require `matterId` filter — confirm at connect.
- **`updatedSince` .NET-ticks conversion** — confirm the exact tick epoch/format the API expects; the MCP server converts ISO ↔ ticks.
- **Stage model** — matter "stage" is via `matterTypeId` → stage sets → stage-to-matter mappings (separate endpoints), not a flat field. `matter-status-digest`'s "group by stage" needs the stage-mapping read; confirm the join shape before relying on a stage label.
- **Webhook event types** — the exact event names that fire on new lead/matter, task due, etc. (`GET /event-types`) — confirm at connect; these drive the event-based skills (the AgentMail `message.received` analogue).
- **Rate limits** — "request throttling" is documented but unquantified; the MCP server implements 429 backoff/retry defensively.
- **ESign / e-signature** — engagement-letter signature status is fixture-supplied this phase; confirm whether Smokeball surfaces signature state on files or it rides a separate ESign capability.
