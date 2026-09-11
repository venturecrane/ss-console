/**
 * Outside-in poll of the web Worker (review 2026-09-10, wave 8.2).
 *
 * Every other condition this pager evaluates is read out of `fleet_status`,
 * which the seats write INTO the web Worker. If the web Worker itself is dead
 * (a bad deploy, D1 answering 503, a custom domain gone), the seats' writes
 * fail, `fleet_status` freezes, and the pager reads a frozen table from the
 * same database and sees nothing wrong. This module is the one probe that does
 * not go through the thing it is watching: a plain GET of `/api/health` from
 * this Worker's own network position, which is outside the web Worker and
 * inside nothing else. A dead edge is paged the way a dead seat is.
 *
 * Counting is edge-triggered like the rest of the pager, with one difference.
 * A seat condition is a state the seat reports; a probe is a sample, and one
 * sample can lie (a cold start, a routing blip, a timeout at the deadline). So
 * the poll keeps its own memory in `edge_poll_state` (migration 0115): a
 * target is DOWN after EDGE_POLL_FAIL_THRESHOLD consecutive failed probes and
 * UP again after EDGE_POLL_RECOVER_THRESHOLD consecutive good ones. In between
 * it pushes no condition at all, the same hold the connector_down class uses,
 * so a flapping edge produces one ALERT and one RECOVERED, not a page per tick.
 *
 * The alert row lives in `fleet_alert_state` under the target name as its
 * `customer_slug` (there is no seat here) and the `edge_down` condition, so the
 * open-once / recover-once machinery in index.ts is reused unchanged.
 * stale-holds.ts excludes that condition, so a host is never reported as a
 * stranded seat.
 *
 * Only smd.services carries `/api/health`: the admin and portal hosts rewrite
 * the path under their own prefix and answer with the auth redirect (HTTP 302,
 * probed 2026-09-11), so they are not targets. They ride the same Worker, so a
 * dead edge takes all three down together.
 *
 * What a green probe proves and what it does not: `/api/health` runs a real
 * `SELECT 1` against D1, so a 200 with `status: ok` means the edge answers and
 * its database answers. It says nothing about the seats, which the rest of
 * this Worker covers. Everything here is fail-soft per target: a probe that
 * throws, a store that is not migrated yet, a malformed target list, each logs
 * and pushes nothing. Silence from this module is never a page and never a
 * recovery; only a counted run is.
 */

import { EDGE_DOWN_CONDITION } from './conditions'
import type { ConditionState, Env } from './index'

export interface EdgeTarget {
  name: string
  url: string
}

export interface EdgePollThresholds {
  failThreshold: number
  recoverThreshold: number
  timeoutMs: number
}

export interface EdgeCounters {
  consecutive_failures: number
  consecutive_successes: number
}

export interface EdgeProbe {
  ok: boolean
  detail: string
}

export type EdgeVerdict = 'active' | 'inactive' | 'hold'

export interface EdgePollResult extends EdgeCounters {
  target: string
  url: string
  ok: boolean
  detail: string
  verdict: EdgeVerdict
}

export const DEFAULT_EDGE_FAIL_THRESHOLD = 3
export const DEFAULT_EDGE_RECOVER_THRESHOLD = 2
export const DEFAULT_EDGE_TIMEOUT_MS = 10_000
const MIN_EDGE_TIMEOUT_MS = 1_000
const MAX_EDGE_TIMEOUT_MS = 30_000
const MAX_BODY_CHARS = 200

/** `name=https://host/path` entries, comma-separated. Anything else is an error, not a skip. */
const TARGET_NAME = /^[a-z0-9][a-z0-9.-]{0,62}$/

export function parseEdgeTargets(raw: string | undefined): {
  targets: EdgeTarget[]
  errors: string[]
} {
  const targets: EdgeTarget[] = []
  const errors: string[] = []
  const seen = new Set<string>()
  for (const piece of (raw ?? '').split(',')) {
    const entry = piece.trim()
    if (!entry) continue
    const eq = entry.indexOf('=')
    if (eq <= 0) {
      errors.push(`edge target ${JSON.stringify(entry)} is not name=url`)
      continue
    }
    const name = entry.slice(0, eq).trim()
    const url = entry.slice(eq + 1).trim()
    if (!TARGET_NAME.test(name)) {
      errors.push(`edge target name ${JSON.stringify(name)} is not a lowercase host-like label`)
      continue
    }
    if (!url.startsWith('https://')) {
      errors.push(`edge target ${name} must be an https:// URL`)
      continue
    }
    if (seen.has(name)) {
      errors.push(`edge target ${name} is listed twice`)
      continue
    }
    seen.add(name)
    targets.push({ name, url })
  }
  return { targets, errors }
}

function intOr(value: string | undefined, fallback: number, floor: number, cap?: number): number {
  const n = Number(value)
  if (!Number.isFinite(n) || n < floor) return fallback
  const whole = Math.floor(n)
  return cap !== undefined && whole > cap ? cap : whole
}

export function edgeThresholds(
  env: Pick<
    Env,
    'EDGE_POLL_FAIL_THRESHOLD' | 'EDGE_POLL_RECOVER_THRESHOLD' | 'EDGE_POLL_TIMEOUT_MS'
  >
): EdgePollThresholds {
  return {
    failThreshold: intOr(env.EDGE_POLL_FAIL_THRESHOLD, DEFAULT_EDGE_FAIL_THRESHOLD, 1),
    recoverThreshold: intOr(env.EDGE_POLL_RECOVER_THRESHOLD, DEFAULT_EDGE_RECOVER_THRESHOLD, 1),
    timeoutMs: intOr(
      env.EDGE_POLL_TIMEOUT_MS,
      DEFAULT_EDGE_TIMEOUT_MS,
      MIN_EDGE_TIMEOUT_MS,
      MAX_EDGE_TIMEOUT_MS
    ),
  }
}

/**
 * The health contract, parsed not cast: a 200 whose body is JSON with
 * `status: "ok"`. The route answers 503 `{status:"error"}` when its D1 probe
 * fails, and anything else (an HTML error page from the platform, a redirect,
 * an empty body) is a failure with the status and the first bytes recorded.
 */
function parseJson(body: string): unknown {
  try {
    return JSON.parse(body)
  } catch {
    return null
  }
}

export function classifyHealthResponse(status: number, body: string): EdgeProbe {
  const parsed = parseJson(body)
  const reported =
    typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>).status
      : undefined
  const reportedText =
    typeof reported === 'string' ? `body status "${reported}"` : 'body is not the health shape'
  if (status === 200 && reported === 'ok') return { ok: true, detail: 'HTTP 200, body status "ok"' }
  const excerpt =
    typeof reported === 'string'
      ? reportedText
      : `${reportedText}: ${body.slice(0, MAX_BODY_CHARS)}`
  return { ok: false, detail: `HTTP ${status}, ${excerpt}` }
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>

/** One sample. Never throws: a thrown fetch is a failed probe with the reason as detail. */
export async function probeEdge(
  target: EdgeTarget,
  fetchImpl: FetchLike,
  timeoutMs: number
): Promise<EdgeProbe> {
  try {
    const resp = await fetchImpl(target.url, {
      method: 'GET',
      redirect: 'manual',
      headers: { accept: 'application/json', 'user-agent': 'ss-fleet-alerts edge-poll' },
      signal: AbortSignal.timeout(timeoutMs),
    })
    const body = await resp.text()
    return classifyHealthResponse(resp.status, body)
  } catch (err) {
    const name = err instanceof Error ? err.name : ''
    if (name === 'TimeoutError' || name === 'AbortError') {
      return { ok: false, detail: `no answer within ${timeoutMs}ms` }
    }
    const message = err instanceof Error ? err.message : String(err)
    return { ok: false, detail: `fetch failed: ${message.slice(0, MAX_BODY_CHARS)}` }
  }
}

/**
 * Pure counting. A good probe zeroes the failure run and a bad one zeroes the
 * success run, so the two counters can never both be non-zero. The verdict is
 * the tri-state the connector_down class established: active once the failure
 * run reaches the threshold, inactive once the success run reaches its own,
 * hold in between. Pushing inactive on the first good probe after an outage
 * would send RECOVERED on a fluke; pushing active on the first bad one would
 * page on a cold start.
 */
export function evaluateEdgePoll(
  prior: EdgeCounters | null,
  probe: EdgeProbe,
  thresholds: Pick<EdgePollThresholds, 'failThreshold' | 'recoverThreshold'>
): { counters: EdgeCounters; verdict: EdgeVerdict } {
  const failures = probe.ok ? 0 : (prior?.consecutive_failures ?? 0) + 1
  const successes = probe.ok ? (prior?.consecutive_successes ?? 0) + 1 : 0
  const counters = { consecutive_failures: failures, consecutive_successes: successes }
  if (failures >= thresholds.failThreshold) return { counters, verdict: 'active' }
  if (successes >= thresholds.recoverThreshold) return { counters, verdict: 'inactive' }
  return { counters, verdict: 'hold' }
}

export const LOAD_EDGE_COUNTERS_SQL =
  'SELECT consecutive_failures, consecutive_successes FROM edge_poll_state WHERE target = ?'

/**
 * `last_ok_at` keeps its prior value across a failed probe (COALESCE on the
 * excluded NULL), so the row always says when the edge last answered well.
 */
export const SAVE_EDGE_POLL_SQL = `INSERT INTO edge_poll_state
         (target, url, consecutive_failures, consecutive_successes, last_ok, last_detail,
          last_checked_at, last_ok_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (target) DO UPDATE SET
         url = excluded.url,
         consecutive_failures = excluded.consecutive_failures,
         consecutive_successes = excluded.consecutive_successes,
         last_ok = excluded.last_ok,
         last_detail = excluded.last_detail,
         last_checked_at = excluded.last_checked_at,
         last_ok_at = COALESCE(excluded.last_ok_at, edge_poll_state.last_ok_at),
         updated_at = excluded.updated_at`

export async function loadEdgeCounters(
  db: D1Database,
  target: string
): Promise<EdgeCounters | null> {
  const row = await db.prepare(LOAD_EDGE_COUNTERS_SQL).bind(target).first<Record<string, unknown>>()
  if (!row) return null
  const failures = row.consecutive_failures
  const successes = row.consecutive_successes
  if (typeof failures !== 'number' || typeof successes !== 'number') return null
  return { consecutive_failures: failures, consecutive_successes: successes }
}

export async function saveEdgePoll(
  db: D1Database,
  result: Pick<
    EdgePollResult,
    'target' | 'url' | 'ok' | 'detail' | 'consecutive_failures' | 'consecutive_successes'
  >,
  checkedAtIso: string
): Promise<void> {
  await db
    .prepare(SAVE_EDGE_POLL_SQL)
    .bind(
      result.target,
      result.url,
      result.consecutive_failures,
      result.consecutive_successes,
      result.ok ? 1 : 0,
      result.detail,
      checkedAtIso,
      result.ok ? checkedAtIso : null,
      checkedAtIso
    )
    .run()
}

function conditionFor(
  result: EdgePollResult,
  thresholds: EdgePollThresholds
): ConditionState | null {
  if (result.verdict === 'hold') return null
  const detail =
    result.verdict === 'active'
      ? `${result.consecutive_failures} consecutive failed probes of ${result.url}; last: ${result.detail}. ` +
        `Resolves after ${thresholds.recoverThreshold} consecutive good probes. ` +
        'The seats write through this edge, so their fleet_status rows may be frozen too.'
      : `${result.url} answered (${result.detail}), ${result.consecutive_successes} consecutive good probes`
  return {
    customer_slug: result.target,
    condition: EDGE_DOWN_CONDITION,
    active: result.verdict === 'active',
    detail,
  }
}

/**
 * Probe every configured target once and turn the counted runs into
 * ConditionStates for the transition machinery. Fail-soft per target.
 */
export async function runEdgePolls(
  env: Env,
  nowMs: number,
  fetchImpl: FetchLike = (input, init) => fetch(input, init)
): Promise<{ conditions: ConditionState[]; polls: EdgePollResult[] }> {
  const { targets, errors } = parseEdgeTargets(env.EDGE_POLL_TARGETS)
  for (const e of errors) console.error(`[fleet-alerts] edge poll config: ${e}`)
  const thresholds = edgeThresholds(env)
  const checkedAt = new Date(nowMs).toISOString()
  const conditions: ConditionState[] = []
  const polls: EdgePollResult[] = []
  for (const target of targets) {
    try {
      const prior = await loadEdgeCounters(env.DB, target.name)
      const probe = await probeEdge(target, fetchImpl, thresholds.timeoutMs)
      const { counters, verdict } = evaluateEdgePoll(prior, probe, thresholds)
      const result: EdgePollResult = {
        target: target.name,
        url: target.url,
        ...probe,
        ...counters,
        verdict,
      }
      await saveEdgePoll(env.DB, result, checkedAt)
      polls.push(result)
      const condition = conditionFor(result, thresholds)
      if (condition) conditions.push(condition)
    } catch (err) {
      console.error('[fleet-alerts] edge poll failed:', target.name, err)
    }
  }
  return { conditions, polls }
}
