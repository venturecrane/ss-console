/**
 * Service data access layer (ADR 0046, Stage 1).
 *
 * `services` is the polymorphic commercial spine — one row per "thing a client
 * bought." Consulting engagements and operator subscriptions are typed delivery
 * children that point UP at a service via `service_id`. See migrations 0068-0070.
 *
 * All queries are parameterized. Ids are `'svc_' + crypto.randomUUID()` — the
 * `svc_` prefix is a TRUE INVARIANT shared with the backfill (0069/0070), so it
 * can never be used to distinguish backfilled from runtime-created rows.
 *
 * IMPORTANT: the SOW-signature finalization batch (src/lib/sow/service-finalize.ts)
 * INLINES its own `INSERT INTO services` rather than calling `createService` —
 * it must stay inside one atomic `db.batch([...])`. Do NOT "DRY" the two
 * together; that would break the atomic engagement+invoice+service creation.
 *
 * READ-MIGRATION STATUS (ADR 0046):
 * - Consulting services have a real forward writer (SOW finalize + the manual
 *   `createEngagement` path), so the consulting spine is authoritative and
 *   self-validating via `findSpineDrift` below.
 * - Operator: `setOperatorPrice` is the forward writer for the COMMERCIAL record
 *   (the service + its `recurring_price`) — NOT the provisioning record
 *   (`subscriptions`, which gates portal access and is owned by provisioning).
 *   The authoritative operator REGISTRY is still `customer_configs` (the live CI
 *   projection): provisioning writes a config, not a service. So the admin
 *   surfaces ITERATE the roster / `customer_configs` for the operator list and
 *   only ENRICH each row with spine data (price, commercial status) by entity_id
 *   — they must NOT iterate the operator spine as the list source, or any
 *   operator provisioned without a commercial record would silently vanish.
 *   `findSpineDrift` surfaces exactly that gap (a live config with no service).
 *   Making provisioning write the spine (so the list can iterate it) is the
 *   remaining step, on a different axis (CI/projection, ADR 0012).
 */

export interface Service {
  id: string
  org_id: string
  entity_id: string
  /** Nullable: backfilled operator rows have no quote (operator quotes arrive in Stage 2). */
  quote_id: string | null
  type: ServiceType
  cadence: ServiceCadence
  status: ServiceStatus
  /** REAL to match invoices.amount/quotes.total_price. NULL for one_time; authored per-quote for operator. */
  recurring_price: number | null
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}

export type ServiceType = 'consulting' | 'operator'
export type ServiceCadence = 'one_time' | 'recurring'
export type ServiceStatus = 'proposed' | 'active' | 'completed' | 'churned'

/**
 * Project a consulting engagement's delivery status onto the commercial
 * rollup. MUST stay in lockstep with the CASE in
 * migrations/0069_service_spine_backfill_consulting.sql (asserted in tests).
 *
 * @public Readable twin of the SQL CASE in migrations/0069_service_spine_backfill_consulting.sql.
 * tests/services.test.ts imports it and pins the mapping. The projection itself runs in SQL.
 */
export function projectConsultingStatus(engagementStatus: string): ServiceStatus {
  switch (engagementStatus) {
    case 'completed':
      return 'completed'
    case 'cancelled':
      return 'churned'
    // scheduled | active | handoff | safety_net → a committed/live revenue line
    default:
      return 'active'
  }
}

/**
 * Project an operator subscription's status onto the commercial rollup. MUST
 * stay in lockstep with the CASE in
 * migrations/0070_service_spine_backfill_operator.sql (asserted in tests).
 *
 * @public Readable twin of the SQL CASE in migrations/0070_service_spine_backfill_operator.sql.
 * tests/services.test.ts imports it and pins the mapping. The projection itself runs in SQL.
 */
export function projectOperatorStatus(subscriptionStatus: string): ServiceStatus {
  switch (subscriptionStatus) {
    case 'cancelled':
      return 'churned'
    // provisioning | active | paused → a committed/live revenue line
    default:
      return 'active'
  }
}

export interface CreateServiceData {
  entity_id: string
  type: ServiceType
  cadence: ServiceCadence
  quote_id?: string | null
  recurring_price?: number | null
  status?: ServiceStatus
  started_at?: string | null
}

export interface ServiceFilters {
  type?: ServiceType
  status?: ServiceStatus
}

export async function createService(
  db: D1Database,
  orgId: string,
  data: CreateServiceData
): Promise<Service> {
  const id = `svc_${crypto.randomUUID()}`
  const now = new Date().toISOString()
  const status = data.status ?? 'proposed'

  await db
    .prepare(
      `INSERT INTO services (id, org_id, entity_id, quote_id, type, cadence, status, recurring_price, started_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entity_id,
      data.quote_id ?? null,
      data.type,
      data.cadence,
      status,
      data.recurring_price ?? null,
      data.started_at ?? null,
      now,
      now
    )
    .run()

  const created = await getService(db, orgId, id)
  if (!created) {
    throw new Error(`Failed to create service ${id}`)
  }
  return created
}

export async function getService(
  db: D1Database,
  orgId: string,
  id: string
): Promise<Service | null> {
  return (
    (await db
      .prepare('SELECT * FROM services WHERE id = ? AND org_id = ?')
      .bind(id, orgId)
      .first<Service>()) ?? null
  )
}

export async function listServices(
  db: D1Database,
  orgId: string,
  filters?: ServiceFilters
): Promise<Service[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.type) {
    conditions.push('type = ?')
    params.push(filters.type)
  }
  if (filters?.status) {
    conditions.push('status = ?')
    params.push(filters.status)
  }

  const result = await db
    .prepare(`SELECT * FROM services WHERE ${conditions.join(' AND ')} ORDER BY created_at DESC`)
    .bind(...params)
    .all<Service>()
  return result.results
}

// ===========================================================================
// Operator commercial record (ADR 0046, operator arc)
// ===========================================================================

/**
 * The single operator service for an entity, or null. The partial unique index
 * `idx_services_one_operator` guarantees at most one operator service per entity.
 */
export async function getOperatorServiceForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Service | null> {
  return (
    (await db
      .prepare(`SELECT * FROM services WHERE org_id = ? AND entity_id = ? AND type = 'operator'`)
      .bind(orgId, entityId)
      .first<Service>()) ?? null
  )
}

/**
 * Author the operator's monthly recurring price (ADR 0046). This is the operator's
 * forward writer for the COMMERCIAL record (the service) — deliberately NOT the
 * delivery/provisioning record (`subscriptions`), which gates portal access and is
 * owned by the provisioning path. Upserts `recurring_price` on the entity's single
 * operator service: updates it if present, creates an `active` operator service if
 * not. Pass `price = null` to clear the price (back to unpriced). Idempotent.
 */
export async function setOperatorPrice(
  db: D1Database,
  orgId: string,
  entityId: string,
  price: number | null
): Promise<Service> {
  const existing = await getOperatorServiceForEntity(db, orgId, entityId)
  if (existing) {
    await db
      .prepare(
        `UPDATE services SET recurring_price = ?, updated_at = ? WHERE id = ? AND org_id = ?`
      )
      .bind(price, new Date().toISOString(), existing.id, orgId)
      .run()
    const updated = await getService(db, orgId, existing.id)
    if (!updated) throw new Error(`Failed to update operator service ${existing.id}`)
    return updated
  }
  // No operator service yet — create the commercial record, born active (we are
  // pricing a real operator). started_at stamps the revenue line's start.
  return createService(db, orgId, {
    entity_id: entityId,
    type: 'operator',
    cadence: 'recurring',
    status: 'active',
    recurring_price: price,
    started_at: new Date().toISOString(),
  })
}

// ===========================================================================
// Spine reconciliation (ADR 0046, Stage 1b)
// ===========================================================================

/** A consulting engagement that has no valid parent service row. */
export interface OrphanEngagement {
  id: string
  status: string
  /** NULL (never linked) or a dangling id pointing at no service row. */
  service_id: string | null
}

/** An active consulting service that has no delivery child. */
export interface ChildlessService {
  id: string
  status: string
}

/** An active operator service whose entity has no live `customer_configs` row. */
export interface OperatorServiceWithoutConfig {
  id: string
  entity_id: string
  status: string
}

/** A live operator (`customer_configs` row) with no operator service (no commercial record). */
export interface ConfigWithoutService {
  entity_id: string
  customer_slug: string
}

/**
 * Every way the spine ↔ child / registry invariant can break, across BOTH
 * delivery types. All lists empty is the healthy state (the normal case).
 */
export interface SpineDrift {
  /** In-flight consulting engagements whose `service_id` is NULL or dangles. */
  orphanEngagements: OrphanEngagement[]
  /** Active consulting services with no engagement pointing back. */
  childlessServices: ChildlessService[]
  /** Active operator services whose entity has no `customer_configs` row. */
  operatorServicesWithoutConfig: OperatorServiceWithoutConfig[]
  /** Live operators (`customer_configs`) with no operator service — uncommercialized. */
  configsWithoutService: ConfigWithoutService[]
}

/** In-flight consulting engagement statuses — those expected to have a live parent. */
const IN_FLIGHT_ENGAGEMENT_STATUSES = ['scheduled', 'active', 'handoff', 'safety_net']

/**
 * Reconcile the spine against its registries for BOTH delivery types (ADR 0046).
 * This is the READ that keeps the spine load-bearing: the services list calls it
 * on every load and surfaces drift, so a path that creates a child without a
 * service (or a live operator without a commercial record) fails loud instead of
 * rotting silently.
 *
 * Consulting: engagement ↔ service must be 1:1 (the spine is authoritative — every
 * engagement has a service and vice versa). Operator: the authoritative registry is
 * `customer_configs`, NOT the spine, so we reconcile the two directions against the
 * registry — never assume the spine enumerates operators (it does not until
 * provisioning writes it). A `configWithoutService` is the expected, useful signal
 * that a live operator has not been given a commercial record yet.
 */
export async function findSpineDrift(db: D1Database, orgId: string): Promise<SpineDrift> {
  const placeholders = IN_FLIGHT_ENGAGEMENT_STATUSES.map(() => '?').join(', ')

  const [orphans, childless, opNoConfig, configNoService] = await Promise.all([
    db
      .prepare(
        `SELECT e.id, e.status, e.service_id
           FROM engagements e
          WHERE e.org_id = ?
            AND e.status IN (${placeholders})
            AND (
              e.service_id IS NULL
              OR e.service_id NOT IN (SELECT s.id FROM services s WHERE s.org_id = ?)
            )`
      )
      .bind(orgId, ...IN_FLIGHT_ENGAGEMENT_STATUSES, orgId)
      .all<OrphanEngagement>(),
    db
      .prepare(
        `SELECT s.id, s.status
           FROM services s
          WHERE s.org_id = ?
            AND s.type = 'consulting'
            AND s.status = 'active'
            AND s.id NOT IN (
              SELECT e.service_id FROM engagements e
               WHERE e.org_id = ? AND e.service_id IS NOT NULL
            )`
      )
      .bind(orgId, orgId)
      .all<ChildlessService>(),
    db
      .prepare(
        `SELECT s.id, s.entity_id, s.status
           FROM services s
          WHERE s.org_id = ?
            AND s.type = 'operator'
            AND s.status = 'active'
            AND s.entity_id NOT IN (
              SELECT c.entity_id FROM customer_configs c WHERE c.org_id = ?
            )`
      )
      .bind(orgId, orgId)
      .all<OperatorServiceWithoutConfig>(),
    db
      .prepare(
        `SELECT c.entity_id, c.customer_slug
           FROM customer_configs c
          WHERE c.org_id = ?
            AND c.entity_id NOT IN (
              SELECT s.entity_id FROM services s WHERE s.org_id = ? AND s.type = 'operator'
            )`
      )
      .bind(orgId, orgId)
      .all<ConfigWithoutService>(),
  ])

  return {
    orphanEngagements: orphans.results,
    childlessServices: childless.results,
    operatorServicesWithoutConfig: opNoConfig.results,
    configsWithoutService: configNoService.results,
  }
}

/** True when any drift class is non-empty. */
export function hasSpineDrift(drift: SpineDrift): boolean {
  return (
    drift.orphanEngagements.length > 0 ||
    drift.childlessServices.length > 0 ||
    drift.operatorServicesWithoutConfig.length > 0 ||
    drift.configsWithoutService.length > 0
  )
}
