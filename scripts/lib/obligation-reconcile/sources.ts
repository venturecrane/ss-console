/**
 * The obligation reconciler's reach into the world: the sources it imports
 * obligations from, the surfaces it probes for evidence, and the two writes
 * that are not the register upsert (certification and the alert row).
 *
 * Split out of scripts/ci-reconcile-obligations.ts (2026-09-25) along the
 * phases the script's header documents; see that header for why each source
 * exists and why the universe is D1 rows, never files.
 */

import { execFileSync } from 'node:child_process'
import type { D1Database } from '@cloudflare/workers-types'
import {
  transitionObligation,
  type Obligation,
  type ObligationKind,
  type UpsertObligationInput,
} from '../../../src/lib/db/obligations'
import { importKey, type AlertInsert, type ProbeResult } from './verdicts'

export interface Seat {
  customer_slug: string
  entity_id: string
}

type GhResult = { ok: true; stdout: string } | { ok: false; error: string }

function runGh(args: string[]): GhResult {
  const override = process.env.SS_RECONCILE_GH_CMD
  try {
    const stdout = execFileSync(override ?? 'gh', args, {
      encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024,
    })
    return { ok: true, stdout }
  } catch (err) {
    return { ok: false, error: String((err as Error)?.message ?? err).split('\n')[0] }
  }
}

/** Reads that must not throw: a failed read is a control failure, not a crash. */
export async function safeAll<T>(fn: () => Promise<T[]>): Promise<{ ok: boolean; rows: T[] }> {
  try {
    return { ok: true, rows: await fn() }
  } catch {
    return { ok: false, rows: [] }
  }
}

// ------------------------------------------------------------------- import

/** One seat's open, client-labelled issues, or why they could not be read. */
function openClientIssues(
  seat: Seat,
  repo: string
): { ok: true; issues: { number: number; title: string }[] } | { ok: false; error: string } {
  const result = runGh([
    'issue',
    'list',
    '--repo',
    repo,
    '--label',
    `client:${seat.customer_slug}`,
    '--state',
    'open',
    '--limit',
    '100',
    '--json',
    'number,title',
  ])
  if (!result.ok) return { ok: false, error: result.error }
  const issues = parseIssueList(result.stdout)
  if (!issues) return { ok: false, error: 'github_output_unparseable' }
  return { ok: true, issues }
}

/**
 * `gh issue list --json number,title`, read field by field: a row without a
 * numeric number and a string title makes the whole read unparseable rather
 * than importing an obligation keyed on `undefined`.
 */
function parseIssueList(stdout: string): { number: number; title: string }[] | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(stdout)
  } catch {
    return null
  }
  if (!Array.isArray(parsed)) return null
  const issues: { number: number; title: string }[] = []
  const rows: unknown[] = parsed
  for (const row of rows) {
    if (typeof row !== 'object' || row === null || Array.isArray(row)) return null
    const { number, title }: Record<string, unknown> = { ...row }
    if (typeof number !== 'number' || typeof title !== 'string') return null
    issues.push({ number, title })
  }
  return issues
}

/**
 * Client-labelled GitHub issues become product_defect obligations.
 *
 * The issue is the source of truth for the DEFECT; the obligation records that a
 * specific client is blocked by it. The probe reads the issue's state, and a
 * closed issue is the evidence `certify` acts on -- so under Law 9 an issue
 * labelled `client:<slug>` is closed only when that client can do the thing,
 * not when the PR merges.
 */
export function importGithubIssues(
  seats: readonly Seat[],
  repo: string
): { ok: true; rows: UpsertObligationInput[] } | { ok: false; error: string } {
  const rows: UpsertObligationInput[] = []
  for (const seat of seats) {
    const read = openClientIssues(seat, repo)
    if (!read.ok) return read
    for (const issue of read.issues) {
      rows.push({
        customer_slug: seat.customer_slug,
        entity_id: seat.entity_id,
        stable_key: importKey('gh', issue.number),
        kind: 'product_defect',
        what: issue.title,
        origin: 'imported',
        origin_source: 'github',
        source_kind: 'github',
        source_ref: `${repo}#${issue.number}`,
        evidence_class: 'probeable',
        evidence_surface: 'github',
        evidence_locator: `${repo}#${issue.number}`,
      })
    }
  }
  return { ok: true, rows }
}

/**
 * Open fleet_alert_state conditions become obligations.
 *
 * A token expiring is a renewal we owe; any other condition is an incident we
 * owe remediation for. The alert says something is wrong; the obligation says
 * somebody owes work about it, and stays until the work is proven done.
 */
export function importAlertState(
  alertRows: readonly { customer_slug: string; condition: string }[],
  seatsBySlug: Map<string, Seat>
): UpsertObligationInput[] {
  const rows: UpsertObligationInput[] = []
  for (const alert of alertRows) {
    const seat = seatsBySlug.get(alert.customer_slug)
    if (!seat) continue
    const isRenewal = alert.condition.startsWith('connector_token_expiring:')
    const kind: ObligationKind = isRenewal ? 'renewal' : 'incident'
    rows.push({
      customer_slug: alert.customer_slug,
      entity_id: seat.entity_id,
      stable_key: importKey('alert', alert.condition),
      kind,
      what: isRenewal
        ? `Renew the credential behind ${alert.condition.split(':')[1] ?? 'a connector'} before it expires.`
        : `Clear the seat condition "${alert.condition}".`,
      origin: 'imported',
      origin_source: 'fleet_alert_state',
      source_kind: 'alert_state',
      source_ref: `${alert.customer_slug}:${alert.condition}`,
      evidence_class: 'probeable',
      evidence_surface: 'fleet_alert_state',
      evidence_locator: `${alert.customer_slug}:${alert.condition}`,
    })
  }
  return rows
}

/** Operator change requests become external_dependency obligations. */
export function importChangeRequests(
  changeRows: readonly { id: string; customer_slug: string; summary: string | null }[],
  seatsBySlug: Map<string, Seat>
): UpsertObligationInput[] {
  const rows: UpsertObligationInput[] = []
  for (const cr of changeRows) {
    const seat = seatsBySlug.get(cr.customer_slug)
    if (!seat) continue
    rows.push({
      customer_slug: cr.customer_slug,
      entity_id: seat.entity_id,
      stable_key: importKey('cr', cr.id),
      kind: 'external_dependency',
      what: cr.summary || 'Operator change request awaiting resolution.',
      origin: 'imported',
      origin_source: 'operator_change_requests',
      source_kind: 'ledger',
      source_ref: `operator_change_requests:${cr.id}`,
      evidence_class: 'probeable',
      evidence_surface: 'd1',
      evidence_locator: `operator_change_requests:${cr.id}`,
    })
  }
  return rows
}

// -------------------------------------------------------------------- probe

function probeGithub(locator: string): ProbeResult {
  const [repo, number] = locator.split('#')
  const result = runGh(['issue', 'view', number, '--repo', repo, '--json', 'state', '-q', '.state'])
  if (!result.ok) return { status: 'unreachable' }
  return { status: result.stdout.trim() === 'CLOSED' ? 'present' : 'absent' }
}

/** One `status` column, read without throwing; `undefined` when the row is absent. */
async function readStatus(
  db: D1Database,
  sql: string,
  values: unknown[]
): Promise<{ ok: boolean; status: string | undefined }> {
  const found = await safeAll(async () => {
    const res = await db
      .prepare(sql)
      .bind(...values)
      .all<{ status: string }>()
    return res.results
  })
  return { ok: found.ok, status: found.rows[0]?.status }
}

async function probeAlertState(locator: string, db: D1Database): Promise<ProbeResult> {
  const [slug, ...rest] = locator.split(':')
  const read = await readStatus(
    db,
    `SELECT status FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?`,
    [slug, rest.join(':')]
  )
  if (!read.ok) return { status: 'unreachable' }
  // No row, or a resolved row, means the condition cleared.
  return { status: !read.status || read.status === 'resolved' ? 'present' : 'absent' }
}

async function probeD1Row(locator: string, db: D1Database): Promise<ProbeResult> {
  const [table, id] = locator.split(':')
  // The table name cannot be a bound parameter, so it is allow-shaped
  // rather than escaped: anything but a bare identifier is refused.
  if (!/^[a-z_]+$/.test(table)) return { status: 'unreachable' }
  const read = await readStatus(db, `SELECT status FROM ${table} WHERE id = ?`, [id])
  if (!read.ok) return { status: 'unreachable' }
  // A declined change request is settled too, just not by delivery. Before
  // 2026-09-25 only resolved/completed counted, so a declined request
  // stayed on the owed list forever (cr-3: a July test of the portal form,
  // declined, still listed as A&P work 72 days later).
  if (read.status === 'declined') return { status: 'withdrawn' }
  const done = read.status === 'resolved' || read.status === 'completed'
  return { status: done ? 'present' : 'absent' }
}

/**
 * Re-read the surface an obligation's evidence points at.
 *
 * Returns 'present' (the thing is really there), 'absent' (it is not, so the
 * obligation stands), or 'unreachable' (we could not tell -- never conflated
 * with absent, because "I could not look" and "it is not there" demand opposite
 * responses).
 */
export async function probeEvidence(row: Obligation, db: D1Database): Promise<ProbeResult> {
  if (!row.evidence_locator) return { status: 'absent' }
  switch (row.evidence_surface) {
    case 'github':
      return probeGithub(row.evidence_locator)
    case 'fleet_alert_state':
      return probeAlertState(row.evidence_locator, db)
    case 'd1':
      return probeD1Row(row.evidence_locator, db)
    default:
      // An attested surface has no CI-reachable probe by definition. The
      // receipt's freshness is what stands in, and no receipt proves nothing.
      // Two live today: a Smokeball filing (the seat's receipt) and
      // `engagements` (the sent letter `register deliver` read off the private
      // repo's origin/main, which no CI credential can open).
      if (row.evidence_class === 'attested') {
        return { status: row.evidence_last_verified_at ? 'present' : 'unreachable' }
      }
      return { status: 'unreachable' }
  }
}

// ------------------------------------------------------------------- writes

/**
 * Move a row whose evidence probed present into `verified`.
 *
 * A `delivered` row goes straight there. An IMPORTED row still sitting in a
 * working state is walked through `delivered` first: its source clearing (the
 * alert resolved, the change request completed, the issue closed) IS the
 * delivery, and nobody else will ever mark it. Before this, those rows were
 * counted in "verified this run" every night and never moved, so the count said
 * one thing and the register another.
 *
 * A captured row is never walked: it reaches `delivered` only through
 * `register deliver`, which cites the letter that kept the promise.
 *
 * Returns null on success, or the refusal. Refusals are reported, never
 * swallowed -- a reconciler that dropped one would read converged while the row
 * sat where it was.
 */
export async function certify(
  db: D1Database,
  row: Obligation,
  runId: string
): Promise<string | null> {
  if (row.state !== 'delivered') {
    if (row.origin !== 'imported') return `a ${row.origin} row in ${row.state} probed present`
    const step = await transitionObligation(db, {
      obligationId: row.obligation_id,
      to: 'delivered',
    })
    if (!step.ok) return step.error
  }
  const done = await transitionObligation(db, {
    obligationId: row.obligation_id,
    to: 'verified',
    reconcileRunId: runId,
  })
  return done.ok ? null : done.error
}

/**
 * Move an imported row whose source withdrew the ask into `cancelled`.
 *
 * Imported rows only: a captured row is a promise stated in a letter, and only
 * the Captain retires one of those. Returns null on success, or the refusal.
 */
export async function withdraw(db: D1Database, row: Obligation): Promise<string | null> {
  if (row.origin !== 'imported') return `a ${row.origin} row probed withdrawn`
  const done = await transitionObligation(db, {
    obligationId: row.obligation_id,
    to: 'cancelled',
    disposition: `source withdrew the ask: ${row.source_ref}`,
  })
  return done.ok ? null : done.error
}

export async function writeAlert(db: D1Database, alert: AlertInsert): Promise<void> {
  await db
    .prepare(
      `INSERT INTO cost_anomaly_alerts (entity_id, customer_slug, alert_date, driver, source,
         daily_cents, rolling_avg_cents, ratio_bps, threshold_bps, summary, details_json, detected_at)
       VALUES (?, ?, ?, ?, 'obligation', 0, 0, 0, 0, ?, ?, datetime('now'))
       ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET
         summary = excluded.summary, details_json = excluded.details_json,
         detected_at = excluded.detected_at`
    )
    .bind(
      alert.entity_id,
      alert.customer_slug,
      alert.alert_date,
      alert.driver,
      alert.summary,
      alert.details_json
    )
    .run()
}
