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
 * - Operator: `setOperatorSeatCommercials` is the forward writer for the
 *   COMMERCIAL record (the service: `recurring_price`, rail, label) — NOT the
 *   provisioning record (`subscriptions`, which gates portal access and is
 *   owned by provisioning). Since migration 0126 the price belongs to the SEAT:
 *   a seat (an operator `subscriptions` row) points at its service through
 *   `subscriptions.service_id`, an entity may hold several operator services,
 *   and several seats may share one. Every reader resolves seat -> service,
 *   never entity -> service.
 *   The authoritative operator REGISTRY is still `customer_configs` (the live CI
 *   projection): provisioning writes a config, not a service. So the admin
 *   surfaces ITERATE the roster / `customer_configs` for the operator list and
 *   only ENRICH each row with spine data (price, commercial status) by its seat
 *   — they must NOT iterate the operator spine as the list source, or any
 *   operator provisioned without a commercial record would silently vanish.
 *   `findSpineDrift` surfaces exactly that gap (a live seat with no service).
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
  /**
   * The rail the recurring fee is collected by (migration 0113). Authored
   * per client next to the price; `ach` for every row that predates it.
   * `card` adds the 3% processing fee line (agreement §3.8) to the checkout
   * and to every monthly invoice. Read through `operatorPaymentMethod`.
   */
  payment_method: string
  /**
   * The Captain-authored, client-visible line name for an operator seat
   * (migration 0126), for example "Marketing Operator". NULL means the plain
   * "Operator". Never derived from a slug, which can carry the client's name.
   */
  label: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}

export type ServiceType = 'consulting' | 'operator'
export type ServiceCadence = 'one_time' | 'recurring'
export type ServiceStatus = 'proposed' | 'active' | 'completed' | 'churned'

/**
 * How the Operator retainer is collected (Operator Service Agreement §3.8;
 * Captain, 2026-08-29 for the fee, 2026-09-10 for the first card client).
 * `ach` is bank account at checkout with no fee; `card` is a card at
 * checkout plus the 3% processing fee on every monthly charge.
 */
export type OperatorPaymentMethod = 'ach' | 'card'

export function isOperatorPaymentMethod(value: unknown): value is OperatorPaymentMethod {
  return value === 'ach' || value === 'card'
}

/** The stored rail, parsed; anything unrecognised reads as ACH (no fee, the safe default). */
export function operatorPaymentMethod(
  service: { payment_method: string | null } | null | undefined
): OperatorPaymentMethod {
  return isOperatorPaymentMethod(service?.payment_method) ? service.payment_method : 'ach'
}

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
  /** The retainer rail (migration 0113); the column default `ach` when omitted. */
  payment_method?: OperatorPaymentMethod
  status?: ServiceStatus
  started_at?: string | null
}

export interface ServiceFilters {
  type?: ServiceType
  status?: ServiceStatus
}

/**
 * @public The plain DAL insert, exercised by tests/services.test.ts. No
 * production path calls it since migration 0126: the SOW finalize inlines its
 * own INSERT (see the module header) and the Operator writer inserts and links
 * a seat's service in one batch (`setOperatorSeatCommercials`).
 */
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
      `INSERT INTO services (id, org_id, entity_id, quote_id, type, cadence, status, recurring_price, payment_method, started_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
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
      data.payment_method ?? 'ach',
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

/**
 * @public The plain DAL list, exercised by tests/services.test.ts. The admin
 * revenue surfaces stopped reading it in migration 0126: they price each seat
 * through `listOperatorSeatCommercials`, never an entity-keyed service map.
 */
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
// Operator commercial record (ADR 0046, operator arc; migration 0126)
// ===========================================================================

/**
 * The longest label the hub accepts. A label is a line name the client reads
 * on Billing and Stripe prints on every invoice ("Marketing Operator"), not
 * prose.
 */
export const OPERATOR_LABEL_MAX = 60

/** Parse an authored label: whitespace collapsed, bounded; '' reads as null (no label). */
export function parseOperatorLabel(
  raw: unknown
): { ok: true; value: string | null } | { ok: false } {
  if (raw === null || raw === undefined) return { ok: true, value: null }
  if (typeof raw !== 'string') return { ok: false }
  const value = raw.replace(/\s+/g, ' ').trim()
  if (value === '') return { ok: true, value: null }
  if (value.length > OPERATOR_LABEL_MAX) return { ok: false }
  return { ok: true, value }
}

/**
 * Every operator service an entity holds, oldest first.
 *
 * There is deliberately no "the entity's operator service" getter any more:
 * since migration 0126 an entity may hold several (one per separately priced
 * seat), and every surface that prices something prices a SEAT, through
 * {@link getOperatorServiceForSeat}. The entity-keyed getter it replaced had
 * seven callers, each of which would have read a client's second seat at its
 * first seat's price.
 */
export async function listOperatorServicesForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Service[]> {
  const result = await db
    .prepare(
      `SELECT * FROM services
        WHERE org_id = ? AND entity_id = ? AND type = 'operator'
        ORDER BY created_at ASC, id ASC`
    )
    .bind(orgId, entityId)
    .all<Service>()
  return result.results ?? []
}

/**
 * The operator service a seat (an operator `subscriptions` row) is priced
 * by, through `subscriptions.service_id`. Null when the seat has no
 * commercial record yet, or its link dangles or names a service of another
 * org or type: never a neighbouring seat's price.
 */
export async function getOperatorServiceForSeat(
  db: D1Database,
  orgId: string,
  seat: { service_id: string | null }
): Promise<Service | null> {
  if (!seat.service_id) return null
  return (
    (await db
      .prepare(`SELECT * FROM services WHERE id = ? AND org_id = ? AND type = 'operator'`)
      .bind(seat.service_id, orgId)
      .first<Service>()) ?? null
  )
}

/** One operator seat with the commercial record it is priced by (if any). */
export interface OperatorSeatCommercial {
  /** subscriptions.id */
  subscription_id: string
  entity_id: string
  /** subscriptions.instance_slug: the seat's customer_slug. */
  instance_slug: string | null
  subscription_status: string
  stripe_subscription_id: string | null
  settings_json: string | null
  service_id: string | null
  recurring_price: number | null
  /** Raw stored rail; parse with {@link operatorPaymentMethod}. Null when unlinked. */
  payment_method: string | null
  label: string | null
  service_status: string | null
}

/**
 * Every live operator seat (non-cancelled operator `subscriptions` row) in
 * the org, optionally for one entity, joined to its service. A seat whose
 * link is NULL or dangles comes back with null commercial fields, never
 * dropped.
 */
export async function listOperatorSeatCommercials(
  db: D1Database,
  orgId: string,
  entityId?: string
): Promise<OperatorSeatCommercial[]> {
  const entityClause = entityId === undefined ? '' : 'AND sub.entity_id = ?'
  const binds = entityId === undefined ? [orgId] : [orgId, entityId]
  const result = await db
    .prepare(
      `SELECT sub.id AS subscription_id, sub.entity_id, sub.instance_slug,
              sub.status AS subscription_status, sub.stripe_subscription_id, sub.settings_json,
              s.id AS service_id, s.recurring_price, s.payment_method, s.label,
              s.status AS service_status
         FROM subscriptions sub
         LEFT JOIN services s
           ON s.id = sub.service_id AND s.org_id = sub.org_id AND s.type = 'operator'
        WHERE sub.org_id = ?
          AND sub.product_slug = 'operator'
          AND sub.status != 'cancelled'
          ${entityClause}
        ORDER BY sub.created_at ASC, sub.id ASC`
    )
    .bind(...binds)
    .all<OperatorSeatCommercial>()
  return result.results ?? []
}

/** What the hub authors for one seat. */
export interface OperatorSeatCommercialsInput {
  /** Monthly price in dollars; null clears it back to unpriced. */
  price: number | null
  /** The rail; null keeps the stored rail (a new service takes the default, `ach`). */
  method: OperatorPaymentMethod | null
  /** The client-visible line name; null clears it, undefined keeps it. */
  label?: string | null
}

/** The addressed instance is not a live operator seat of the entity. */
export class OperatorSeatNotFoundError extends Error {
  constructor(entityId: string, instanceSlug: string) {
    super(`No live operator seat ${instanceSlug} on entity ${entityId}`)
    this.name = 'OperatorSeatNotFoundError'
  }
}

/**
 * Author ONE seat's monthly price, rail and label (ADR 0046; migrations 0113,
 * 0126; code review 2026-09-10, Architecture 8).
 *
 * The seat is a live operator `subscriptions` row of this entity, addressed
 * by its instance slug. This writes the COMMERCIAL record (the seat's
 * service), never the seat's status or Stripe attachment, which
 * provisioning and billing own. When the seat already points at a service,
 * that service is updated in ONE statement, so price, rail and label land
 * together or not at all. When it has none, a new `active` operator service
 * is inserted and the seat linked to it in ONE `db.batch`, so a priced
 * service never exists without the seat that it prices.
 *
 * A service shared by several seats (SMD's own $0 seats) is updated in place:
 * the price belongs to the line, and the line is what those seats share.
 * Writing seat 2 never touches seat 1's service.
 *
 * Throws {@link OperatorSeatNotFoundError} when the instance is not a live
 * operator seat of this entity (another client's slug included).
 */
export async function setOperatorSeatCommercials(
  db: D1Database,
  orgId: string,
  entityId: string,
  instanceSlug: string,
  input: OperatorSeatCommercialsInput
): Promise<Service> {
  const seat = await db
    .prepare(
      `SELECT id, service_id FROM subscriptions
        WHERE org_id = ? AND entity_id = ? AND product_slug = 'operator'
          AND instance_slug = ? AND status != 'cancelled'`
    )
    .bind(orgId, entityId, instanceSlug)
    .first<{ id: string; service_id: string | null }>()
  if (!seat) throw new OperatorSeatNotFoundError(entityId, instanceSlug)

  const now = new Date().toISOString()
  const existing = await getOperatorServiceForSeat(db, orgId, seat)
  if (!existing) {
    const id = `svc_${crypto.randomUUID()}`
    await db.batch([
      db
        .prepare(
          `INSERT INTO services (id, org_id, entity_id, quote_id, type, cadence, status, recurring_price, payment_method, label, started_at, created_at, updated_at)
           VALUES (?, ?, ?, NULL, 'operator', 'recurring', 'active', ?, ?, ?, ?, ?, ?)`
        )
        .bind(
          id,
          orgId,
          entityId,
          input.price,
          input.method ?? 'ach',
          input.label ?? null,
          now,
          now,
          now
        ),
      db
        .prepare(`UPDATE subscriptions SET service_id = ?, updated_at = ? WHERE id = ?`)
        .bind(id, now, seat.id),
    ])
    const created = await getService(db, orgId, id)
    if (!created) throw new Error(`Failed to create operator service ${id}`)
    return created
  }

  const keepLabel = input.label === undefined ? 1 : 0
  await db
    .prepare(
      `UPDATE services
          SET recurring_price = ?,
              payment_method = COALESCE(?, payment_method),
              label = CASE WHEN ? = 1 THEN label ELSE ? END,
              updated_at = ?
        WHERE id = ? AND org_id = ?`
    )
    .bind(input.price, input.method, keepLabel, input.label ?? null, now, existing.id, orgId)
    .run()
  const updated = await getService(db, orgId, existing.id)
  if (!updated) throw new Error(`Failed to update operator service ${existing.id}`)
  return updated
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

/** A live operator seat (operator `subscriptions` row) with no commercial record. */
export interface SeatWithoutService {
  /** subscriptions.id */
  id: string
  entity_id: string
  instance_slug: string | null
  /** NULL (never linked) or a dangling id naming no operator service of this org. */
  service_id: string | null
}

/** An active operator service that no live seat points at. */
export interface OperatorServiceWithoutSeat {
  id: string
  entity_id: string
  status: string
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
  /** Live operator seats priced by nothing (migration 0126: price is per seat). */
  seatsWithoutService: SeatWithoutService[]
  /** Active operator services no live seat points at: a price nobody is charged. */
  operatorServicesWithoutSeat: OperatorServiceWithoutSeat[]
}

/** In-flight consulting engagement statuses — those expected to have a live parent. */
const IN_FLIGHT_ENGAGEMENT_STATUSES = ['scheduled', 'active', 'handoff', 'safety_net']

/**
 * Reconcile the spine against its children for BOTH delivery types (ADR 0046).
 * This is the READ that keeps the spine load-bearing: the services list calls it
 * on every load and surfaces drift, so a path that creates a child without a
 * service (or a live seat without a commercial record) fails loud instead of
 * rotting silently.
 *
 * Consulting: engagement ↔ service must be 1:1 (the spine is authoritative — every
 * engagement has a service and vice versa). Operator (migration 0126): the
 * commercial line is priced PER SEAT, through `subscriptions.service_id`, so the
 * two directions are keyed by seat, never by entity: a live seat that points at
 * no operator service, and an active operator service that no live seat points
 * at. Keyed by entity, a client's second seat would read as priced because its
 * first seat was.
 */
export async function findSpineDrift(db: D1Database, orgId: string): Promise<SpineDrift> {
  const placeholders = IN_FLIGHT_ENGAGEMENT_STATUSES.map(() => '?').join(', ')

  const [orphans, childless, seatNoService, serviceNoSeat] = await Promise.all([
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
        `SELECT sub.id, sub.entity_id, sub.instance_slug, sub.service_id
           FROM subscriptions sub
          WHERE sub.org_id = ?
            AND sub.product_slug = 'operator'
            AND sub.status != 'cancelled'
            AND (
              sub.service_id IS NULL
              OR sub.service_id NOT IN (
                SELECT s.id FROM services s WHERE s.org_id = ? AND s.type = 'operator'
              )
            )`
      )
      .bind(orgId, orgId)
      .all<SeatWithoutService>(),
    db
      .prepare(
        `SELECT s.id, s.entity_id, s.status
           FROM services s
          WHERE s.org_id = ?
            AND s.type = 'operator'
            AND s.status = 'active'
            AND s.id NOT IN (
              SELECT sub.service_id FROM subscriptions sub
               WHERE sub.org_id = ? AND sub.product_slug = 'operator'
                 AND sub.status != 'cancelled' AND sub.service_id IS NOT NULL
            )`
      )
      .bind(orgId, orgId)
      .all<OperatorServiceWithoutSeat>(),
  ])

  return {
    orphanEngagements: orphans.results,
    childlessServices: childless.results,
    seatsWithoutService: seatNoService.results,
    operatorServicesWithoutSeat: serviceNoSeat.results,
  }
}

/** True when any drift class is non-empty. */
export function hasSpineDrift(drift: SpineDrift): boolean {
  return (
    drift.orphanEngagements.length > 0 ||
    drift.childlessServices.length > 0 ||
    drift.seatsWithoutService.length > 0 ||
    drift.operatorServicesWithoutSeat.length > 0
  )
}
