# Cost Telemetry — Event Emission Specification

**Spec for issue #804.** How cost data is emitted and collected per the §15.1 cost drivers. Without emission spec, Phase 1's "Cost telemetry instrumented" milestone closes with empty tables and the §17.1 COGS/MRR ≤40% kill criterion is unfalsifiable.

Per PM R7, telemetry instrumentation is a **Phase 2 scope item**, not Phase 1 — it must be producing per-customer-per-day reports before beta-1 generates usage.

## Contract

### Storage

> **Amended 2026-07-03 (ADR 0062, #1660).** The per-customer-D1 placement below is superseded: those databases were never provisioned (see ADR 0009's wiring note), so this spec's storage clause described a store that did not exist. `cost_telemetry` and `captain_time_events` live in the **central ss-console D1** (`ss-console-db`, migration `0083_central_cost_telemetry.sql`) with a `customer_slug` tenant column, under ADR 0009's billing-reconciliation carve-out and the `fleet_status` precedent (ADR 0023). Reserved slugs: `_org` (org-level reconciliation rows under drivers `anthropic.org_total.input_tokens` / `anthropic.org_total.output_tokens`) and `_unmapped` (usage from Anthropic workspaces no seat claims). Per-seat attribution comes from per-customer Anthropic **workspaces** mapped via `customer_configs.anthropic_workspace_id` (see `docs/runbooks/operator/cost-telemetry-enable.md`). The nightly worker's writes are idempotent day totals (replace-on-conflict), because the usage-report API returns authoritative daily figures; the additive accumulate-on-conflict contract below still applies to per-event emitters such as the captain-time CLI rollup. The original text is retained below as historical record.

All events land in the per-customer `cost_telemetry` D1 table per d1-schema.md:

```sql
cost_telemetry (
  date          TEXT NOT NULL,          -- YYYY-MM-DD (UTC)
  driver        TEXT NOT NULL,          -- enum below
  amount_cents  INTEGER NOT NULL,       -- integer cents
  units         REAL,                   -- e.g. tokens, calls, GB-hours
  unit_type     TEXT,                   -- enum below
  PRIMARY KEY (date, driver)
)
```

Rollup is one row per `(date, driver)` per customer. Insertion is UPSERT — multiple events for the same date+driver accumulate into a single row.

### Drivers + emission sources

| `driver`                        | Source                                                                                      | Emission cadence                  | `unit_type`       |
| ------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------------- | ----------------- |
| `claude_api_input_tokens`       | Captured from Anthropic API response headers (`anthropic-input-tokens`) on every model call | Per-call, batched to D1 every 60s | `input_tokens`    |
| `claude_api_output_tokens`      | Same response headers (`anthropic-output-tokens`)                                           | Per-call, batched 60s             | `output_tokens`   |
| `fly_machine_minutes`           | Fly.io billing API; pulled by nightly Captain job at 02:00 UTC                              | Daily                             | `machine_minutes` |
| `d1_reads`                      | D1 metering query (`PRAGMA d1_metrics` or Cloudflare GraphQL Analytics)                     | Daily nightly                     | `api_calls`       |
| `d1_writes`                     | Same source                                                                                 | Daily nightly                     | `api_calls`       |
| `r2_storage_gb_hours`           | Cloudflare R2 metering API                                                                  | Daily nightly                     | `gb_hours`        |
| `r2_class_a_ops`                | Same source (writes, list operations)                                                       | Daily nightly                     | `api_calls`       |
| `r2_class_b_ops`                | Same source (reads)                                                                         | Daily nightly                     | `api_calls`       |
| `vectorize_queries`             | Cloudflare Vectorize metering API                                                           | Daily nightly                     | `api_calls`       |
| `vectorize_dimensions_stored`   | Same source                                                                                 | Daily nightly                     | `dimensions`      |
| `agentmail_messages`            | AgentMail billing API                                                                       | Daily nightly                     | `messages`        |
| `agentmail_mailbox_days`        | AgentMail subscription pull                                                                 | Daily nightly                     | `mailbox_days`    |
| `third_party_api_lawpay`        | LawPay billing API (if cost model includes per-call fees)                                   | Daily nightly                     | `api_calls`       |
| `third_party_api_docusign`      | DocuSign billing                                                                            | Daily nightly                     | `envelopes`       |
| `third_party_api_courtlistener` | CourtListener (free tier; logged as units only)                                             | Daily nightly                     | `api_calls`       |
| `captain_time`                  | Captain logs via `crane operator log-time` CLI                                              | On-demand by Captain              | `captain_minutes` |

### Per-call emission (Claude API)

Adapter at `operator/adapter/anthropic_client.py` wraps every Anthropic API call:

```python
async def chat_completion(prompt: ...) -> Response:
    resp = await self._anthropic.messages.create(...)
    in_tokens = resp.usage.input_tokens
    out_tokens = resp.usage.output_tokens
    in_cost_cents = in_tokens * MODEL_PRICING[self.model].input_per_million_cents // 1_000_000
    out_cost_cents = out_tokens * MODEL_PRICING[self.model].output_per_million_cents // 1_000_000
    _cost_event_buffer.add("claude_api_input_tokens", units=in_tokens, cents=in_cost_cents)
    _cost_event_buffer.add("claude_api_output_tokens", units=out_tokens, cents=out_cost_cents)
    return resp
```

A buffer flushes to D1 every 60s or 500 events, whichever comes first. Flushed via D1 UPSERT (`ON CONFLICT (date, driver) DO UPDATE SET amount_cents = amount_cents + excluded.amount_cents, units = units + excluded.units`).

Model pricing lives in `operator/adapter/cost_telemetry/anthropic_pricing.json`:

```json
{
  "claude-opus-4-7": { "input_per_million_cents": 1500, "output_per_million_cents": 7500 },
  "claude-sonnet-4-6": { "input_per_million_cents": 300, "output_per_million_cents": 1500 },
  "claude-haiku-4-5-20251001": { "input_per_million_cents": 80, "output_per_million_cents": 400 },
  "claude-opus-4-8": { "input_per_million_cents": 500, "output_per_million_cents": 2500 },
  "claude-opus-5": { "input_per_million_cents": 500, "output_per_million_cents": 2500 },
  "claude-sonnet-5": { "input_per_million_cents": 200, "output_per_million_cents": 1000 }
}
```

Captain updates this JSON on Anthropic price changes; CI test ensures every customer.yaml-declared `model` has a pricing entry.

### Nightly Captain job

A Cloudflare Worker scheduled cron `0 2 * * *` at `infra/workers/cost-telemetry/worker.ts` iterates every active customer:

```
for customer in list_customers():
    fly_minutes = fly_api.minutes_for_machine(f"hermes-{customer}", yesterday)
    d1_metrics  = cloudflare_api.d1_metrics(f"hermes-{customer}-d1", yesterday)
    r2_metrics  = cloudflare_api.r2_metrics(f"hermes-{customer}-r2", yesterday)
    vec_metrics = cloudflare_api.vectorize_metrics(f"hermes-{customer}-vault", yesterday)
    agentmail   = agentmail_api.usage_for_mailbox(customer_mailbox_id, yesterday)
    upsert all into customer's cost_telemetry (date=yesterday)
```

Failures (single source fails): log to Captain alerting; do NOT block other sources. Captain-only dashboard surfaces stale-source warnings.

### Captain time logging

Captain operations time is event-sourced, not rollup-keyed. Every invocation of the Captain CLI writes one row to the per-customer `captain_time_events` table (per d1-schema.md) and emits one matching summary row into `cost_telemetry` for the same date so the §17.1 COGS/MRR computation reads from a single rollup view.

**Canonical CLI:**

```
crane operator log-time --customer {slug} --minutes {N} --activity {tag} [--note "{text}"] [--date YYYY-MM-DD]
```

**Activity tags** form a closed v1 enum. The CLI rejects any tag not in the list. Freeform strings are explicitly out of scope; new tags require a PR.

**Per-event write (captain_time_events table):**

```sql
INSERT INTO captain_time_events
  (id, ts, date, activity, minutes, amount_cents, note)
VALUES (?, ?, ?, ?, ?, ?, ?)
```

where:

- `id` is a ULID
- `ts` is the wall-clock ISO 8601 UTC of the CLI invocation
- `date` is the `--date` value (defaults to today UTC)
- `activity` is the validated activity tag
- `minutes` is the `--minutes` value
- `amount_cents` is `(minutes * 200 * 100) / 60` computed at write time using the $200/hr Captain rate
- `note` is the optional free-text context (max 280 chars)

**Daily rollup into cost_telemetry:**

After each event insert, the CLI UPSERTs the day's `captain_time` summary row using the same `ON CONFLICT (date, driver) DO UPDATE SET amount_cents = amount_cents + excluded.amount_cents, units = units + excluded.units` pattern used for token events:

```sql
INSERT INTO cost_telemetry (date, driver, amount_cents, units, unit_type)
VALUES (?, 'captain_time', ?, ?, 'captain_minutes')
ON CONFLICT (date, driver) DO UPDATE
  SET amount_cents = amount_cents + excluded.amount_cents,
      units = units + excluded.units;
```

The rollup row is what §17.1 COGS/MRR computation reads. Per-event detail (activity attribution, notes) lives in `captain_time_events` for Captain-only audit and per-activity cost reporting.

**Loaded-cost rate.** The $200/hr loaded rate matches CLAUDE.md. The CLI never accepts a dollar amount from the user; the rate constant is defined once in the CLI source so a rate change requires a code change, not a flag override.

**Audit log row.** Each successful CLI invocation also writes one `audit_log` row with `action_type: CAPTAIN_TIME_LOGGED`, `actor: captain`, and a metadata JSON containing `{activity, minutes, amount_cents, date, event_id}`. This surfaces the time log in the Captain dashboard alongside other administrative events.

**Idempotency.** None, by design. Re-running the same command writes a second row in both `captain_time_events` and accumulates in `cost_telemetry`. This is intentional: Captain may log two distinct 15-minute calibration sessions on the same day for the same customer and both must persist. Correcting a mis-logged entry is a follow-on operation (`crane operator log-time --reverse` is a Phase 4 follow-on, not in scope here).

### Per-customer rollup view

The Captain control plane dashboard reads per-customer per-day cost via:

```sql
SELECT date, SUM(amount_cents) AS total_cents,
  SUM(CASE WHEN driver = 'claude_api_input_tokens' THEN amount_cents END) AS claude_in,
  SUM(CASE WHEN driver = 'claude_api_output_tokens' THEN amount_cents END) AS claude_out,
  ...
FROM cost_telemetry
WHERE date >= ?
GROUP BY date ORDER BY date DESC;
```

Surfaced in the Captain dashboard at `admin.smd.services/operator/cost/{customer}`. Phase 4 ships a customer-facing version.

### COGS/MRR ratio computation

```
month_cogs_cents = SUM(amount_cents) for the month
mrr_cents        = customer's flat-monthly SKU price in cents
ratio            = month_cogs_cents / mrr_cents
```

Alert fires when `ratio > 0.40` for two consecutive months. Audit event `COGS_RATIO_HIGH` written. Captain decides: re-price the customer, impose usage cap, or absorb temporarily.

## Failure modes

- **Anthropic API response missing usage headers**: log warning; record zero-token row with `units=null`; Captain alerted weekly on cumulative null rate.
- **Fly/Cloudflare metering API rate-limited**: nightly job retries with exponential backoff (1m/5m/15m); skips after 3 fails; Captain alerted.
- **Pricing changes mid-month**: cost_telemetry stores `amount_cents` computed at emission time using the pricing in effect then. No retroactive recomputation.
- **Captain forgets to log time**: `captain_time_events` rows stay missing and the `cost_telemetry` rollup understates true COGS. Weekly automated reminder via Slack DM to Captain summarizing untagged Captain hours. The CLI accepts `--date` up to 7 days in the past so backfilling missed days is a single command per day.
- **CLI rejects an invalid activity tag**: Captain has typed a freeform string that is not in the closed v1 taxonomy. Resolution: pick the nearest valid tag, or open a PR extending the enum. The row is not written.

## Verification

1. **Per-call emission test** at `tests/operator/cost-telemetry-emission.test.ts`: drive 100 mocked Anthropic calls, assert exactly 200 events written (1 in_tokens + 1 out_tokens per call), assert correct cents math against the pricing JSON.
2. **Nightly job test**: stub all source APIs; run the worker; assert exactly N rows per customer written for `yesterday` date; assert source failures don't block other sources.
3. **Pricing-update CI guard**: every customer.yaml's `model` must have an entry in `anthropic_pricing.json`.
4. **COGS ratio computation test**: seed cost_telemetry with a known total; assert ratio matches expected % to 4 decimal places.

## Implementation notes

- Buffer module: `operator/adapter/cost_event_buffer.py` (Python; flushes via aiohttp to D1 HTTP API). TS twin if/when adapters port.
- Wrap every Anthropic call via `anthropic_client.py` rather than skill code touching the raw SDK; CI grep blocks direct SDK imports outside this wrapper.
- Nightly Worker: `infra/workers/cost-telemetry/worker.ts`; cron `0 2 * * *` UTC; lives in main repo so it ships with platform code.
- Captain dashboard view: `src/pages/admin/operator/cost/[customer].astro` reads from per-customer D1.
- Captain time helper: `crane operator log-time` CLI subcommand. Per-event D1 schema at d1-schema.md `captain_time_events`. The CLI writes both the per-event row and the `cost_telemetry` daily rollup in a single transaction.
- Cross-references:
  - d1-schema.md (cost_telemetry table)
  - decommission-customer.md (final cost_telemetry export before D1 deletion)

## Resolved decisions

**D1 metering access pattern.** Plan around Cloudflare GraphQL Analytics for the nightly D1 read/write pull. The #824 implementation work includes a validation spike against the live Cloudflare account as its first step. **Fallback:** if GraphQL Analytics access turns out to be unworkable for our auth model or rate limits, defer D1 cost-driver instrumentation to phase 2 — Anthropic API tokens dominate the COGS surface; D1 will not be the kill-criterion driver in v1, so a temporary gap is acceptable.
