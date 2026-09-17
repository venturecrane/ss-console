/**
 * Obligation register data access (ADR 0088, migration 0117).
 *
 * The register holds every piece of work SMD owes a client: requests,
 * deliverables, recurring duties, renewals, incidents, external dependencies,
 * config ops, provisioning, and links out to product defects that block a
 * client. It is internal. Nothing here is rendered on a client-facing surface.
 *
 * Two invariants live in this module rather than in its callers, because a
 * convention an agent can forget is not a control:
 *
 *   1. `verified` and `closed` require a reconcile_run_id. The schema enforces
 *      it too (0117 CHECK); this layer refuses to build the statement at all,
 *      so the failure is a typed error rather than a D1 constraint violation
 *      surfacing three frames up.
 *
 *   2. Transitions are checked against VALID_TRANSITIONS, and `parked` has an
 *      edge back to every working state. The medchron ledger made `failed`
 *      terminal and stranded a job that had actually been delivered
 *      (medchron_ledger.py:58); the shape of that bug is a state with no way
 *      out, so this table has exactly one terminal state and it is the good one.
 *
 * All queries are parameterized. Reads are scoped by customer_slug and return
 * empty rather than throwing on a miss, matching the enumeration-prevention
 * posture in src/lib/db/milestones.ts:74-77.
 */

import type { D1Database } from '@cloudflare/workers-types'

export interface Obligation {
  obligation_id: string
  customer_slug: string
  entity_id: string
  stable_key: string
  kind: ObligationKind
  what: string
  origin: ObligationOrigin
  origin_source: string
  source_kind: ObligationSourceKind
  source_ref: string
  source_quote: string | null
  date_quote: string | null
  window_start: string | null
  window_end: string | null
  due_at: string | null
  state: ObligationState
  park_reason: string | null
  resume_token: string | null
  evidence_class: EvidenceClass | null
  evidence_surface: string | null
  evidence_locator: string | null
  evidence_last_verified_at: string | null
  reconcile_run_id: string | null
  supersedes_obligation_id: string | null
  recur_rule: string | null
  recur_anchor: string | null
  parent_obligation_id: string | null
  links_json: string | null
  created_by_session: string | null
  created_at: string
  last_seen_at: string
  closed_at: string | null
  disposition: string | null
}

export type ObligationKind =
  | 'request'
  | 'deliverable'
  | 'recurring'
  | 'renewal'
  | 'incident'
  | 'external_dependency'
  | 'config_ops'
  | 'provisioning'
  | 'product_defect'

export type ObligationOrigin = 'imported' | 'captured'
export type ObligationSourceKind = 'letter' | 'git' | 'github' | 'alert_state' | 'ledger'
export type EvidenceClass = 'probeable' | 'attested'

export type ObligationState =
  | 'open'
  | 'active'
  | 'awaiting_external'
  | 'delivered'
  | 'verified'
  | 'closed'
  | 'parked'
  | 'cancelled'
  | 'void'

export const OBLIGATION_KINDS: readonly ObligationKind[] = [
  'request',
  'deliverable',
  'recurring',
  'renewal',
  'incident',
  'external_dependency',
  'config_ops',
  'provisioning',
  'product_defect',
]

/**
 * Legal transitions.
 *
 * `parked` is the failure state and it is not terminal: it returns to open,
 * active, or awaiting_external, so a stalled obligation is always resumable.
 * `closed` keeps a single outbound edge to `open` — the Captain reopen the
 * monthly closed-file audit uses when re-probed evidence no longer reads true.
 */
export const VALID_TRANSITIONS: Record<ObligationState, readonly ObligationState[]> = {
  open: ['active', 'awaiting_external', 'delivered', 'parked', 'cancelled', 'void'],
  active: ['awaiting_external', 'delivered', 'parked', 'cancelled'],
  awaiting_external: ['active', 'delivered', 'parked', 'cancelled'],
  delivered: ['verified', 'parked', 'active'],
  verified: ['closed', 'parked'],
  closed: ['open'],
  parked: ['open', 'active', 'awaiting_external', 'cancelled', 'void'],
  cancelled: ['open'],
  void: [],
}

/** Transitions only a reconcile run may make: closure is never self-certified. */
export const RECONCILER_ONLY_STATES: readonly ObligationState[] = ['verified', 'closed']

export type TransitionResult =
  { ok: true } | { ok: false; error: 'illegal_transition' | 'requires_reconcile_run' }

/**
 * Is this state change allowed, and is it allowed for this actor?
 *
 * Pure, so the rule is testable without a database and so callers on both
 * sides (the CLI and the reconciler) check the same function rather than each
 * re-deriving it.
 */
export function checkTransition(
  from: ObligationState,
  to: ObligationState,
  reconcileRunId: string | null
): TransitionResult {
  if (!VALID_TRANSITIONS[from].includes(to)) return { ok: false, error: 'illegal_transition' }
  if (RECONCILER_ONLY_STATES.includes(to) && !reconcileRunId) {
    return { ok: false, error: 'requires_reconcile_run' }
  }
  return { ok: true }
}

/**
 * Every obligation for one client, newest-due first with undated rows last.
 *
 * Undated rows sort last deliberately: they are real obligations but they
 * never alarm (only dated ones do), so they belong below the things that have
 * a clock on them.
 */
export async function listObligationsForCustomer(
  db: D1Database,
  customerSlug: string,
  options: { includeTerminal?: boolean } = {}
): Promise<Obligation[]> {
  const includeTerminal = options.includeTerminal === true
  const sql = includeTerminal
    ? `SELECT * FROM client_obligations WHERE customer_slug = ?
       ORDER BY due_at IS NULL, due_at ASC, created_at ASC`
    : `SELECT * FROM client_obligations WHERE customer_slug = ?
         AND state NOT IN ('closed','cancelled','void')
       ORDER BY due_at IS NULL, due_at ASC, created_at ASC`
  const rows = await db.prepare(sql).bind(customerSlug).all<Obligation>()
  return rows.results ?? []
}

/** Every non-terminal obligation across the fleet — the "what's on our plate" read. */
export async function listOpenObligations(db: D1Database): Promise<Obligation[]> {
  const rows = await db
    .prepare(
      `SELECT * FROM client_obligations
        WHERE state NOT IN ('closed','cancelled','void')
        ORDER BY due_at IS NULL, due_at ASC, customer_slug ASC`
    )
    .all<Obligation>()
  return rows.results ?? []
}

export async function getObligation(
  db: D1Database,
  obligationId: string
): Promise<Obligation | null> {
  const row = await db
    .prepare(`SELECT * FROM client_obligations WHERE obligation_id = ?`)
    .bind(obligationId)
    .first<Obligation>()
  return row ?? null
}

/**
 * Both denominators in one read.
 *
 * `total` is every row; `universe` is what a reconcile run would consider. One
 * count cannot tell "nothing to do" from "the selector is broken" — printing
 * them separately is what makes a zero-row run distinguishable from a healthy
 * one, which is the whole reason this function returns a pair.
 */
export async function countObligations(
  db: D1Database
): Promise<{ total: number; universe: number }> {
  const row = await db
    .prepare(
      `SELECT
         (SELECT COUNT(*) FROM client_obligations) AS total,
         (SELECT COUNT(*) FROM client_obligations
           WHERE state NOT IN ('closed','cancelled','void')) AS universe`
    )
    .first<{ total: number; universe: number }>()
  return { total: row?.total ?? 0, universe: row?.universe ?? 0 }
}

/**
 * Obligations per source class for one client — the coverage census input.
 *
 * The census compares these counts against the artifacts each source actually
 * holds. A source with artifacts and zero obligations is the one signal in the
 * whole design capable of catching its own silence.
 */
export async function countByOriginSource(
  db: D1Database,
  customerSlug: string
): Promise<Record<string, number>> {
  const rows = await db
    .prepare(
      `SELECT origin_source, COUNT(*) AS n FROM client_obligations
        WHERE customer_slug = ? GROUP BY origin_source`
    )
    .bind(customerSlug)
    .all<{ origin_source: string; n: number }>()
  const out: Record<string, number> = {}
  for (const row of rows.results ?? []) out[row.origin_source] = row.n
  return out
}

export interface UpsertObligationInput {
  customer_slug: string
  entity_id: string
  stable_key: string
  kind: ObligationKind
  what: string
  origin: ObligationOrigin
  origin_source: string
  source_kind: ObligationSourceKind
  source_ref: string
  source_quote?: string | null
  date_quote?: string | null
  window_start?: string | null
  window_end?: string | null
  due_at?: string | null
  evidence_class?: EvidenceClass | null
  evidence_surface?: string | null
  evidence_locator?: string | null
  recur_rule?: string | null
  recur_anchor?: string | null
  links_json?: string | null
  created_by_session?: string | null
}

/**
 * Insert an obligation, or refresh one that already exists.
 *
 * Identity is (customer_slug, kind, stable_key). On conflict this refreshes
 * the human sentence, the quotes, the dates and last_seen_at — and
 * deliberately does NOT touch `state`. Re-extracting a letter or re-importing
 * from GitHub proves an obligation is still stated at its source; it must
 * never resurrect a row somebody already closed.
 */
export async function upsertObligation(
  db: D1Database,
  input: UpsertObligationInput
): Promise<string> {
  const id = crypto.randomUUID()
  await db
    .prepare(
      `INSERT INTO client_obligations (
         obligation_id, customer_slug, entity_id, stable_key, kind, what,
         origin, origin_source, source_kind, source_ref, source_quote, date_quote,
         window_start, window_end, due_at,
         evidence_class, evidence_surface, evidence_locator,
         recur_rule, recur_anchor, links_json, created_by_session)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(customer_slug, kind, stable_key) DO UPDATE SET
         what = excluded.what,
         source_ref = excluded.source_ref,
         source_quote = excluded.source_quote,
         date_quote = excluded.date_quote,
         window_start = excluded.window_start,
         window_end = excluded.window_end,
         due_at = excluded.due_at,
         links_json = excluded.links_json,
         last_seen_at = datetime('now')`
    )
    .bind(
      id,
      input.customer_slug,
      input.entity_id,
      input.stable_key,
      input.kind,
      input.what,
      input.origin,
      input.origin_source,
      input.source_kind,
      input.source_ref,
      input.source_quote ?? null,
      input.date_quote ?? null,
      input.window_start ?? null,
      input.window_end ?? null,
      input.due_at ?? null,
      input.evidence_class ?? null,
      input.evidence_surface ?? null,
      input.evidence_locator ?? null,
      input.recur_rule ?? null,
      input.recur_anchor ?? null,
      input.links_json ?? null,
      input.created_by_session ?? null
    )
    .run()

  const row = await db
    .prepare(
      `SELECT obligation_id FROM client_obligations
        WHERE customer_slug = ? AND kind = ? AND stable_key = ?`
    )
    .bind(input.customer_slug, input.kind, input.stable_key)
    .first<{ obligation_id: string }>()
  return row?.obligation_id ?? id
}

export interface TransitionInput {
  obligationId: string
  to: ObligationState
  reconcileRunId?: string | null
  parkReason?: string | null
  resumeToken?: string | null
  evidenceSurface?: string | null
  evidenceLocator?: string | null
  disposition?: string | null
}

export type TransitionOutcome =
  | { ok: true; from: ObligationState; to: ObligationState }
  | {
      ok: false
      error: 'not_found' | 'illegal_transition' | 'requires_reconcile_run' | 'park_needs_reason'
    }

/**
 * Move an obligation to a new state, or refuse and say why.
 *
 * Refusals are returned, not thrown: every caller has to handle them, and a
 * reconciler that swallowed a refusal would report converged while the row sat
 * where it was.
 */
export async function transitionObligation(
  db: D1Database,
  input: TransitionInput
): Promise<TransitionOutcome> {
  const current = await getObligation(db, input.obligationId)
  if (!current) return { ok: false, error: 'not_found' }

  const runId = input.reconcileRunId ?? null
  const check = checkTransition(current.state, input.to, runId)
  if (!check.ok) return { ok: false, error: check.error }
  if (input.to === 'parked' && !input.parkReason) return { ok: false, error: 'park_needs_reason' }

  const closing = input.to === 'closed'
  await db
    .prepare(
      `UPDATE client_obligations SET
         state = ?,
         reconcile_run_id = COALESCE(?, reconcile_run_id),
         park_reason = ?,
         resume_token = ?,
         evidence_surface = COALESCE(?, evidence_surface),
         evidence_locator = COALESCE(?, evidence_locator),
         evidence_last_verified_at = CASE WHEN ? IN ('verified','closed')
           THEN datetime('now') ELSE evidence_last_verified_at END,
         closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END,
         disposition = COALESCE(?, disposition)
       WHERE obligation_id = ?`
    )
    .bind(
      input.to,
      runId,
      input.to === 'parked' ? (input.parkReason ?? null) : null,
      input.to === 'parked' ? (input.resumeToken ?? null) : null,
      input.evidenceSurface ?? null,
      input.evidenceLocator ?? null,
      input.to,
      closing ? 1 : 0,
      input.disposition ?? null,
      input.obligationId
    )
    .run()

  return { ok: true, from: current.state, to: input.to }
}

/** Open a reconcile run. Called before the run writes anything it will certify. */
export async function startReconcileRun(
  db: D1Database,
  workflowRunUrl: string | null
): Promise<string> {
  const runId = crypto.randomUUID()
  await db
    .prepare(`INSERT INTO reconcile_runs (run_id, workflow_run_url) VALUES (?, ?)`)
    .bind(runId, workflowRunUrl)
    .run()
  return runId
}

export async function finishReconcileRun(
  db: D1Database,
  runId: string,
  counts: {
    total: number
    universe: number
    verified: number
    overdue: number
    cannotEvaluate: number
    exitCode: number
  }
): Promise<void> {
  await db
    .prepare(
      `UPDATE reconcile_runs SET finished_at = datetime('now'), total_rows = ?,
         universe_rows = ?, verified_count = ?, overdue_count = ?,
         cannot_evaluate_count = ?, exit_code = ? WHERE run_id = ?`
    )
    .bind(
      counts.total,
      counts.universe,
      counts.verified,
      counts.overdue,
      counts.cannotEvaluate,
      counts.exitCode,
      runId
    )
    .run()
}

/**
 * The high-water mark: the largest total_rows any prior run recorded.
 *
 * An empty register on day one is not a broken selector, but an empty register
 * after a run that saw 400 rows is. Without this, the reconciler alarms from
 * the moment it ships and teaches the Captain to ignore it — which is exactly
 * how the cadence engine reached 134 days overdue.
 */
export async function priorHighWaterMark(db: D1Database): Promise<number> {
  const row = await db
    .prepare(`SELECT MAX(total_rows) AS hwm FROM reconcile_runs WHERE total_rows IS NOT NULL`)
    .first<{ hwm: number | null }>()
  return row?.hwm ?? 0
}

/**
 * Certifications no CI run stands behind.
 *
 * The layers, weakest claim to strongest:
 *
 *   1. The CHECK in 0117 stops `verified`/`closed` with a NULL run id.
 *   2. The FOREIGN KEY on reconcile_run_id stops an INVENTED run id outright —
 *      verified against the live D1 shim, which enforces it. So the naive
 *      forgery (hand-write a state with a made-up run id) fails at the
 *      database, not at review.
 *   3. What layer 2 leaves: fabricate a reconcile_runs row too, then point at
 *      it. That row has no workflow_run_url, because only CI has one to write.
 *      This read finds those, and the reconciler raises them as
 *      `obligation_unverifiable`.
 *
 * Layer 3 is the one that can actually fail in the field, which is why the
 * check targets it rather than layer 2's already-impossible case. A run id IS
 * legitimately NULL-urled during a local reconcile; that is precisely the
 * signal — a local run may read and report, but nothing it certified should
 * stand unexamined.
 */
export async function findUnwitnessedCertifications(db: D1Database): Promise<Obligation[]> {
  const rows = await db
    .prepare(
      `SELECT o.* FROM client_obligations o
        LEFT JOIN reconcile_runs r ON r.run_id = o.reconcile_run_id
        WHERE o.state IN ('verified','closed')
          AND (r.run_id IS NULL OR r.workflow_run_url IS NULL)`
    )
    .all<Obligation>()
  return rows.results ?? []
}
