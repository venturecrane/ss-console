/**
 * Chronology-package jobs, read live from a seat (routine 11, ss#2614).
 *
 * The seat's broker owns a small ledger of every chronology job the Operator
 * (or root on the box) submitted: its state, the documents it read, the pages,
 * the cents, the folder it delivered into. This module is the read + shape
 * guard + monthly roll-up behind the admin page.
 *
 * Two lanes, kept apart on purpose (the same discipline as the cost page):
 * the cents here are the RUNNER'S OWN ledger, priced from the pricing table
 * on the seat; the nightly cost plane books the same tokens to the customer's
 * workspace from the vendor's usage report. They agree within the pricing
 * table's tolerance and are never presented as one number.
 *
 * Fail-closed parsing: a row without an id and a state is dropped, and every
 * count is zeroed rather than coerced. An unreachable seat is `unreachable`,
 * never a stale table.
 */

import type { D1Database } from '@cloudflare/workers-types'

import {
  readMachineRuntime,
  type MachineRuntimeTransport,
  type RuntimeReadAudit,
  type RuntimeReadActor,
} from '../operator/runtime-read'

export type MedchronJobState = 'submitted' | 'running' | 'held' | 'delivered' | 'failed'

export interface MedchronJobRow {
  id: string
  createdAt: string
  updatedAt: string
  state: MedchronJobState
  matterNumber: string
  documents: number
  pages: number
  cents: number
  reason: string | null
  folderId: string | null
}

export interface MedchronMonthTotals {
  month: string
  jobs: number
  delivered: number
  held: number
  documents: number
  pages: number
  cents: number
  /** Pages and cents by the broker's OWN debit rule (2026-09-09): every job
   * that recorded cents, whatever state it ended in, counted against the month
   * it was CREATED in. Both halves of that rule are shared with the seat on
   * purpose. The state half, because a run that spent real money and then held
   * must not read as zero here. The keying half, because `created_at` is a
   * column both surfaces already have: a month-of-charge key would live in a
   * ledger column that is not in the broker's PROJECTION, and PROJECTION's
   * shape is pinned by the overlay this release, so a job created on the 31st
   * whose cents land on the 1st would be debited to one month on the seat and
   * shown in the other here. `pages`/`cents` above stay delivered-only: what
   * actually reached the firm. */
  pagesUsed: number
  centsUsed: number
}

/** The seat key the allowance is authored under. */
export const ALLOWANCE_SETTING = 'chronology_package_page_allowance_per_month'

export type MedchronJobsReadResult =
  | { status: 'not_enabled' }
  | { status: 'unreachable'; reason: string }
  | { status: 'empty' }
  | { status: 'items'; jobs: MedchronJobRow[]; month: MedchronMonthTotals }

const STATES: ReadonlySet<string> = new Set(['submitted', 'running', 'held', 'delivered', 'failed'])

function asCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

/** One ledger row, or null when its identity (id + state) is missing. */
export function parseJobRow(raw: unknown): MedchronJobRow | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  const id = asText(r['id'])
  const state = asText(r['state'])
  if (!id || !state || !STATES.has(state)) return null
  return {
    id,
    createdAt: asText(r['created_at']) ?? '',
    updatedAt: asText(r['updated_at']) ?? '',
    state: state as MedchronJobState,
    matterNumber: asText(r['matter_number']) ?? '',
    documents: asCount(r['documents']),
    pages: asCount(r['pages']),
    cents: asCount(r['cents']),
    reason: asText(r['reason']),
    folderId: asText(r['folder_id']),
  }
}

/** The calendar month's roll-up. Documents and cents count DELIVERED jobs only
 * (the allowance metric and the cost that actually reached the firm); held
 * and failed jobs show in the table, not the totals. */
export function monthTotals(
  jobs: MedchronJobRow[],
  month: string = new Date().toISOString().slice(0, 7)
): MedchronMonthTotals {
  const inMonth = jobs.filter((j) => j.createdAt.slice(0, 7) === month)
  const delivered = inMonth.filter((j) => j.state === 'delivered')
  const debited = inMonth.filter((j) => j.cents > 0)
  return {
    month,
    jobs: inMonth.length,
    delivered: delivered.length,
    held: inMonth.filter((j) => j.state === 'held').length,
    documents: delivered.reduce((s, j) => s + j.documents, 0),
    pages: delivered.reduce((s, j) => s + j.pages, 0),
    cents: delivered.reduce((s, j) => s + j.cents, 0),
    pagesUsed: debited.reduce((s, j) => s + j.pages, 0),
    centsUsed: debited.reduce((s, j) => s + j.cents, 0),
  }
}

/**
 * The seat's authored monthly PAGE allowance, from the D1 projection's
 * `personas_json`. Fail-closed: an unparseable projection, a disabled skill, a
 * missing key, or a value that is not a non-negative integer all read as "no
 * allowance authored", which the page shows as an absent denominator rather
 * than inventing one. Never throws.
 */
export function allowanceFromPersonas(raw: unknown): number | null {
  const personas = parseJson(raw)
  if (!Array.isArray(personas)) return null
  for (const persona of personas) {
    if (typeof persona !== 'object' || persona === null) continue
    const rawSkills = (persona as Record<string, unknown>)['skills']
    if (!Array.isArray(rawSkills)) continue
    const skills: unknown[] = rawSkills
    const skill = skills.find(
      (s) =>
        typeof s === 'object' &&
        s !== null &&
        (s as Record<string, unknown>)['name'] === 'medical-chronology-maintainer'
    )
    if (skill !== undefined) return allowanceOfSkill(skill as Record<string, unknown>)
  }
  return null
}

/**
 * The seat's authored page allowance for one customer, from the D1 projection.
 * Lives here rather than in the page's frontmatter because raw D1 prepares stay
 * out of pages (`tests/page-sql-readers.test.ts`). Fail-closed like the parser
 * it wraps: an absent row is no allowance, not a zero.
 */
export async function loadAuthoredPageAllowance(
  db: D1Database,
  customerSlug: string
): Promise<number | null> {
  const row = await db
    .prepare('SELECT personas_json FROM customer_configs WHERE customer_slug = ?')
    .bind(customerSlug)
    .first<{ personas_json: string }>()
  return allowanceFromPersonas(row?.personas_json)
}

function parseJson(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw
  try {
    return JSON.parse(raw) as unknown
  } catch {
    return null
  }
}

function allowanceOfSkill(skill: Record<string, unknown>): number | null {
  if (skill['enabled'] === false) return null
  const settings = skill['settings']
  if (typeof settings !== 'object' || settings === null) return null
  const value = (settings as Record<string, unknown>)[ALLOWANCE_SETTING]
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

interface MedchronReadDeps {
  transport: MachineRuntimeTransport
  audit: RuntimeReadAudit
}

/**
 * Read one seat's chronology-job ledger. `configured` mirrors the
 * runtime-observe loader: a dark read path returns `not_enabled` without a
 * read and without an audit row.
 */
export async function loadMedchronJobsView(
  deps: MedchronReadDeps,
  customerSlug: string,
  actor: RuntimeReadActor,
  configured: boolean,
  month?: string
): Promise<MedchronJobsReadResult> {
  if (!configured) return { status: 'not_enabled' }
  const result = await readMachineRuntime(deps, customerSlug, { kind: 'medchron_jobs' }, actor)
  if (!result.ok) return { status: 'unreachable', reason: result.reason }
  const payload = result.data as { entries?: unknown } | null
  const entries = Array.isArray(payload?.entries) ? payload.entries : []
  const jobs: MedchronJobRow[] = []
  for (const raw of entries) {
    const parsed = parseJobRow(raw)
    if (parsed) jobs.push(parsed)
  }
  if (jobs.length === 0) return { status: 'empty' }
  jobs.sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0))
  return { status: 'items', jobs, month: monthTotals(jobs, month) }
}

export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}
