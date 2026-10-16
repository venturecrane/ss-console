/**
 * Captain cost dashboard — query layer for the central cost_telemetry
 * data populated by the `ss-cost-telemetry` worker.
 *
 * One data surface: central D1 (`env.DB`). It holds `customer_configs`
 * (the customer enumeration), `subscriptions` (the delivery-status
 * display), the `services` spine (the authoritative recurring-revenue
 * figure, `recurring_price`, used by the COGS/MRR ratio — ADR 0046),
 * and — since ADR 0062 (migration 0083) — the `cost_telemetry` table
 * itself, keyed (customer_slug, date, driver).
 *
 * The per-customer-D1 HTTP fan-out this module used to perform (per the
 * original ADR 0009 storage premise) is retired: those databases were
 * never provisioned, and ADR 0062 moved cost rows to the central store
 * under the billing-reconciliation carve-out. Reserved slugs '_org'
 * (org reconciliation) and '_unmapped' (workspace usage no seat claims)
 * never appear here because the enumeration comes from customer_configs.
 *
 * Fabrication discipline (CLAUDE.md Pattern A/B): the dashboard never
 * fabricates per-skill or per-model breakdown that the underlying schema
 * does not record. The cost_telemetry schema groups Anthropic usage into
 * `claude_api_input_tokens` / `claude_api_output_tokens`. Per-model
 * decomposition is phase 2; this module surfaces only what is in the
 * data.
 */

export interface CustomerListRow {
  customer_slug: string
  entity_id: string | null
  entity_name: string | null
  subscription_status: string | null
  monthly_revenue_cents: number | null
}

interface CustomerConfigRow {
  customer_slug: string
  entity_id: string
}

interface SubscriptionRow {
  instance_slug: string | null
  status: string
  service_id: string | null
}

interface OperatorServiceRow {
  id: string
  recurring_price: number | null
}

interface EntityRow {
  id: string
  name: string
}

/**
 * Enumerate every Operator customer with the data the dashboard
 * needs: the subscription status (for the COGS-vs-revenue indicator)
 * and the entity name (display).
 *
 * The query joins customer_configs to subscriptions filtered to the
 * `operator` product slug — non-Operator customers don't apply.
 */
export async function listCostCustomers(db: D1Database): Promise<CustomerListRow[]> {
  const configsResult = await db
    .prepare(
      `SELECT customer_slug, entity_id
         FROM customer_configs
         ORDER BY customer_slug`
    )
    .all<CustomerConfigRow>()
  const configs = configsResult.results ?? []
  if (configs.length === 0) return []

  const entityIds = configs.map((c) => c.entity_id)
  const placeholders = entityIds.map(() => '?').join(',')

  // Per SEAT (migration 0126): each customer_config's own operator
  // subscriptions row (instance_slug = customer_slug), and the service that
  // row points at. Keyed by entity, a client's two seats would share one
  // status and one price.
  const subsResult = await db
    .prepare(
      `SELECT instance_slug, status, service_id
         FROM subscriptions
         WHERE product_slug = 'operator'
           AND entity_id IN (${placeholders})`
    )
    .bind(...entityIds)
    .all<SubscriptionRow>()
  const seatBySlug = new Map<string, SubscriptionRow>()
  // How many live seats share each service: the per-seat revenue for the
  // COGS ratio is the line's price split evenly across them.
  const liveSeatsPerService = new Map<string, number>()
  for (const row of subsResult.results ?? []) {
    if (row.instance_slug) seatBySlug.set(row.instance_slug, row)
    if (row.service_id && row.status !== 'cancelled') {
      liveSeatsPerService.set(row.service_id, (liveSeatsPerService.get(row.service_id) ?? 0) + 1)
    }
  }

  // ADR 0046: recurring revenue is the spine's authoritative figure
  // (`services.recurring_price`, dollars), not `subscriptions.settings_json`.
  // One source of truth — the same number that feeds the Billing MRR band.
  const svcResult = await db
    .prepare(
      `SELECT id, recurring_price
         FROM services
         WHERE type = 'operator' AND status = 'active'
           AND entity_id IN (${placeholders})`
    )
    .bind(...entityIds)
    .all<OperatorServiceRow>()
  const priceByService = new Map<string, number | null>()
  for (const row of svcResult.results ?? []) {
    priceByService.set(row.id, row.recurring_price)
  }

  function seatRevenueCents(slug: string): number | null {
    const seat = seatBySlug.get(slug)
    if (!seat?.service_id) return null
    const price = priceByService.get(seat.service_id) ?? null
    if (price == null) return null
    const sharing = Math.max(1, liveSeatsPerService.get(seat.service_id) ?? 1)
    return Math.round((price * 100) / sharing)
  }

  const entitiesResult = await db
    .prepare(`SELECT id, name FROM entities WHERE id IN (${placeholders})`)
    .bind(...entityIds)
    .all<EntityRow>()
  const namesByEntity = new Map<string, string>()
  for (const row of entitiesResult.results ?? []) {
    namesByEntity.set(row.id, row.name)
  }

  return configs.map((c) => {
    return {
      customer_slug: c.customer_slug,
      entity_id: c.entity_id,
      entity_name: namesByEntity.get(c.entity_id) ?? null,
      subscription_status: seatBySlug.get(c.customer_slug)?.status ?? null,
      // Authoritative recurring revenue from the spine, in cents for the ratio
      // math: this seat's share of the line it is priced by.
      monthly_revenue_cents: seatRevenueCents(c.customer_slug),
    }
  })
}

// ---------------------------------------------------------------------------
// Central cost_telemetry reads (D1 binding, ADR 0062)
// ---------------------------------------------------------------------------

export interface CostTelemetryRow {
  date: string
  driver: string
  amount_cents: number
  units: number | null
  unit_type: string | null
}

interface RawCostRow {
  date?: string
  driver?: string
  amount_cents?: number
  units?: number | null
  unit_type?: string | null
}

/**
 * Read raw cost_telemetry rows for one customer over a date window
 * (inclusive `startDate`, exclusive `endDate` — both 'YYYY-MM-DD')
 * from the central table via the D1 binding.
 *
 * The half-open range rides the `(customer_slug, date, driver)` PK so
 * D1 plans this as an index scan. Rows come back ordered by
 * `(date, driver)` for stable rendering.
 *
 * Returns `{ rows: [], error: <message> }` when the query fails; the
 * caller renders an explicit warning rather than an empty table that
 * looks the same as "no usage yet". See docs/style/empty-state-pattern.md.
 */
export async function fetchCustomerCostRows(
  db: D1Database,
  customerSlug: string,
  startDate: string,
  endDate: string
): Promise<{ rows: CostTelemetryRow[]; error: string | null }> {
  try {
    const result = await db
      .prepare(
        'SELECT date, driver, amount_cents, units, unit_type ' +
          'FROM cost_telemetry ' +
          'WHERE customer_slug = ? AND date >= ? AND date < ? AND amount_cents >= 0 ' +
          'ORDER BY date, driver'
      )
      .bind(customerSlug, startDate, endDate)
      .all<RawCostRow>()
    return { rows: validateRows(result.results ?? []), error: null }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { rows: [], error: `cost_telemetry query failed: ${msg}` }
  }
}

function validateRows(resultRows: RawCostRow[]): CostTelemetryRow[] {
  const rows: CostTelemetryRow[] = []
  for (const r of resultRows) {
    if (typeof r.date !== 'string' || typeof r.driver !== 'string') continue
    if (typeof r.amount_cents !== 'number' || !Number.isFinite(r.amount_cents)) continue
    rows.push({
      date: r.date,
      driver: r.driver,
      amount_cents: Math.round(r.amount_cents),
      units: typeof r.units === 'number' && Number.isFinite(r.units) ? r.units : null,
      unit_type: typeof r.unit_type === 'string' ? r.unit_type : null,
    })
  }
  return rows
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

/**
 * Driver categories that the dashboard groups raw drivers into. Mirrors
 * the buckets in operator/adapter/cost_rollup.py — keeping the two
 * lists in sync is intentional. The Worker side uses Python; this side
 * uses TypeScript; both read the same closed enum from
 * cost-telemetry-events.md "Drivers + emission sources".
 *
 * Unknown drivers bucket into `other` so a new emitter doesn't silently
 * disappear; the dashboard surfaces the raw driver name in the
 * `byDriver` map for triage.
 */
export type DriverCategory =
  | 'anthropic_llm'
  | 'fly_compute'
  | 'cloudflare_d1'
  | 'cloudflare_r2'
  | 'cloudflare_vectorize'
  | 'agentmail'
  | 'captain_time'
  | 'other'

const DRIVER_TO_CATEGORY: Record<string, DriverCategory> = {
  claude_api_input_tokens: 'anthropic_llm',
  claude_api_output_tokens: 'anthropic_llm',
  fly_machine_minutes: 'fly_compute',
  d1_reads: 'cloudflare_d1',
  d1_writes: 'cloudflare_d1',
  r2_storage_gb_hours: 'cloudflare_r2',
  r2_class_a_ops: 'cloudflare_r2',
  r2_class_b_ops: 'cloudflare_r2',
  vectorize_queries: 'cloudflare_vectorize',
  vectorize_dimensions_stored: 'cloudflare_vectorize',
  agentmail_messages: 'agentmail',
  agentmail_mailbox_days: 'agentmail',
  captain_time: 'captain_time',
}

export const DRIVER_CATEGORIES: DriverCategory[] = [
  'anthropic_llm',
  'fly_compute',
  'cloudflare_d1',
  'cloudflare_r2',
  'cloudflare_vectorize',
  'agentmail',
  'captain_time',
  'other',
]

export const DRIVER_CATEGORY_LABELS: Record<DriverCategory, string> = {
  anthropic_llm: 'Anthropic LLM',
  fly_compute: 'Fly compute',
  cloudflare_d1: 'Cloudflare D1',
  cloudflare_r2: 'Cloudflare R2',
  cloudflare_vectorize: 'Cloudflare Vectorize',
  agentmail: 'AgentMail',
  captain_time: 'Captain time',
  other: 'Other',
}

export function categoryForDriver(driver: string): DriverCategory {
  return DRIVER_TO_CATEGORY[driver] ?? 'other'
}

export interface DailyCost {
  date: string
  total_cents: number
}

export interface DriverBreakdown {
  driver: string
  category: DriverCategory
  total_cents: number
  units: number
  unit_type: string | null
}

export interface CustomerCostSummary {
  customer_slug: string
  windowStart: string
  windowEnd: string
  totalCents: number
  byCategory: Record<DriverCategory, number>
  byDriver: DriverBreakdown[]
  byDay: DailyCost[]
  /**
   * 7-day rolling average of `total_cents` per day. Only days that have
   * at least 7 prior days inside the window get a rolling value — earlier
   * days are null rather than computed against a short sample (an avg
   * over 1-6 days would be misleading). The 30-day default window yields
   * 24 rolling-avg points.
   */
  rolling7dCents: Array<{ date: string; avg_cents: number | null }>
  rowCount: number
}

/**
 * Compute the 30-day (or arbitrary-window) per-customer summary from
 * raw cost_telemetry rows. Pure function — the caller fetches; this
 * computes.
 *
 * `byDay` includes every date in the window even if no rows landed
 * that day (zero-filled) so the rolling avg is computed against a
 * dense series and the dashboard renders a stable timeline.
 */
export function summarizeCostRows(
  customerSlug: string,
  windowStart: string,
  windowEnd: string,
  rows: CostTelemetryRow[]
): CustomerCostSummary {
  const byCategory: Record<DriverCategory, number> = {
    anthropic_llm: 0,
    fly_compute: 0,
    cloudflare_d1: 0,
    cloudflare_r2: 0,
    cloudflare_vectorize: 0,
    agentmail: 0,
    captain_time: 0,
    other: 0,
  }
  const driverAgg = new Map<string, DriverBreakdown>()
  const dailyAgg = new Map<string, number>()
  let total = 0

  for (const row of rows) {
    if (row.amount_cents < 0) continue
    total += row.amount_cents
    const cat = categoryForDriver(row.driver)
    byCategory[cat] += row.amount_cents
    dailyAgg.set(row.date, (dailyAgg.get(row.date) ?? 0) + row.amount_cents)

    const existing = driverAgg.get(row.driver)
    if (existing) {
      existing.total_cents += row.amount_cents
      existing.units += row.units ?? 0
    } else {
      driverAgg.set(row.driver, {
        driver: row.driver,
        category: cat,
        total_cents: row.amount_cents,
        units: row.units ?? 0,
        unit_type: row.unit_type,
      })
    }
  }

  const byDay: DailyCost[] = []
  for (const d of enumerateDates(windowStart, windowEnd)) {
    byDay.push({ date: d, total_cents: dailyAgg.get(d) ?? 0 })
  }

  const rolling7dCents: Array<{ date: string; avg_cents: number | null }> = []
  for (let i = 0; i < byDay.length; i++) {
    if (i < 6) {
      rolling7dCents.push({ date: byDay[i].date, avg_cents: null })
      continue
    }
    let sum = 0
    for (let j = i - 6; j <= i; j++) sum += byDay[j].total_cents
    rolling7dCents.push({ date: byDay[i].date, avg_cents: Math.round(sum / 7) })
  }

  const byDriver = Array.from(driverAgg.values()).sort((a, b) => b.total_cents - a.total_cents)

  return {
    customer_slug: customerSlug,
    windowStart,
    windowEnd,
    totalCents: total,
    byCategory,
    byDriver,
    byDay,
    rolling7dCents,
    rowCount: rows.length,
  }
}

/**
 * Enumerate every date string 'YYYY-MM-DD' in the half-open window
 * `[startDate, endDate)`. Uses UTC throughout — cost_telemetry.date is
 * a calendar UTC date per cost-telemetry-events.md.
 */
export function enumerateDates(startDate: string, endDate: string): string[] {
  const dates: string[] = []
  let cur = parseUtcDate(startDate)
  const end = parseUtcDate(endDate)
  if (cur >= end) return dates
  while (cur < end) {
    dates.push(formatUtcDate(cur))
    cur = new Date(cur.getTime() + 86_400_000)
  }
  return dates
}

function parseUtcDate(yyyymmdd: string): Date {
  const [y, m, d] = yyyymmdd.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

function formatUtcDate(d: Date): string {
  const yyyy = d.getUTCFullYear()
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

/**
 * Compute the 30-day window ending at `today` (exclusive) in UTC.
 * Used by callers that don't override the window via query params.
 */
export function defaultWindow(today: Date = new Date()): { start: string; end: string } {
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()))
  const endMs = end.getTime() + 86_400_000 // exclusive end = tomorrow 00:00 UTC
  const start = new Date(endMs - 30 * 86_400_000)
  return {
    start: formatUtcDate(start),
    end: formatUtcDate(new Date(endMs)),
  }
}

// ---------------------------------------------------------------------------
// COGS / MRR ratio
// ---------------------------------------------------------------------------

/**
 * Convert 30-day COGS into an estimated month-equivalent figure. The
 * underlying telemetry is per-day; the COGS/MRR ratio in
 * cost-telemetry-events.md §"COGS/MRR ratio computation" is monthly.
 * 30 days * (~30.44 average month length) is close enough — labelled
 * "estimated" on the dashboard so the reader knows it is a normalization,
 * not a billed-period total.
 */
export function thirtyDayCogsToMonthlyEstimateCents(cogsCents: number): number {
  return Math.round((cogsCents * 30.4375) / 30)
}

export interface CogsRatio {
  basis_points: number | null
  status: 'unpriced' | 'healthy' | 'watch' | 'kill'
}

/**
 * Compute the COGS/MRR ratio for one customer. Returns basis points
 * (1/100 of a percent) so downstream comparisons are integer math.
 *
 * `kill` threshold mirrors platform-prd §17.1: ratio > 0.40 (4000 bps)
 * is the documented kill criterion. `watch` is 0.30-0.40 (3000-4000 bps),
 * a Captain heuristic surface for "this is on the way to the threshold."
 * `healthy` is < 0.30. `unpriced` is "the customer has no MRR figure
 * configured" — the ratio is undefined and the dashboard renders "—"
 * rather than zero.
 */
export function cogsRatio(monthlyCogsCents: number, mrrCents: number | null): CogsRatio {
  if (mrrCents === null || mrrCents <= 0) {
    return { basis_points: null, status: 'unpriced' }
  }
  const bps = Math.round((monthlyCogsCents * 10_000) / mrrCents)
  let status: CogsRatio['status']
  if (bps >= 4000) status = 'kill'
  else if (bps >= 3000) status = 'watch'
  else status = 'healthy'
  return { basis_points: bps, status }
}

// ---------------------------------------------------------------------------
// CSV serialization
// ---------------------------------------------------------------------------

/**
 * Render the raw cost_telemetry rows for one customer as CSV for the
 * billing reconciliation export. Header columns mirror the table
 * schema verbatim so the file diffs cleanly against an UPSERT log.
 */
export function rowsToCsv(customerSlug: string, rows: CostTelemetryRow[]): string {
  const header = ['customer_slug', 'date', 'driver', 'amount_cents', 'units', 'unit_type'].join(',')
  const lines = rows.map((r) =>
    [
      csvEscape(customerSlug),
      csvEscape(r.date),
      csvEscape(r.driver),
      String(r.amount_cents),
      r.units == null ? '' : String(r.units),
      csvEscape(r.unit_type),
    ].join(',')
  )
  return [header, ...lines].join('\n') + '\n'
}

function csvEscape(value: string | null | undefined): string {
  if (value == null) return ''
  const s = String(value)
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}
