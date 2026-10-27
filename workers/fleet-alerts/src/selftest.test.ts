/**
 * selftest_failing (migration 0138). The page can be wrong three ways: silent
 * after a bad boot, paging on one racy hourly run, or resolving on a NULL.
 * Each test names the direction it forbids.
 */

import { describe, it, expect } from 'vitest'
import { conditionLabel, evaluateConditions, type FleetStatusRow } from './index'
import { SELFTEST_HOURLY_STREAK, parseSelftestVerdict, selftestConditions } from './selftest'

const NOW = Date.parse('2026-10-23T12:05:00.000Z')

function row(overrides: Partial<FleetStatusRow>): FleetStatusRow {
  return {
    customer_slug: 'smd-staging',
    last_heartbeat_ts: '2026-10-23T12:04:00.000Z',
    sticky_stop_level: 'OK',
    sticky_stop_reason: null,
    sticky_stop_condition: null,
    scheduler_ok: null,
    scheduler_max_overdue_seconds: null,
    connectors_json: null,
    connector_check_ok: null,
    connector_token_age_json: null,
    spec_control_json: null,
    spec_control_ok: null,
    webhook_surface_json: null,
    webhook_surface_ok: null,
    gateway_loop_ok: null,
    gateway_loop_age_seconds: null,
    gateway_supervisor_state: null,
    gateway_restarts_last_hour: null,
    ...overrides,
  }
}

function verdict(over: Record<string, unknown>): string {
  return JSON.stringify({
    v: 1,
    run_kind: 'hourly',
    finished_at: '2026-10-23T12:00:00Z',
    version: '1'.repeat(40),
    image_ref: 'registry.fly.io/hermes-fleet:rel-11111111-202610230000',
    hermes_sha: '3'.repeat(40),
    total: 74,
    failed_count: 0,
    warned_count: 0,
    failed_streak: 0,
    failed: [],
    warned: [],
    ...over,
  })
}

const failing = (over: Record<string, unknown>) =>
  verdict({ failed_count: 1, failed_streak: 1, failed: ['retired-skills-absent'], ...over })

function state(json: string | null | undefined) {
  const out = selftestConditions(row({ selftest_json: json }))
  return out.length ? out[0] : null
}

describe('selftest_failing', () => {
  it('NULL or absent pushes nothing: no run yet is a hold, never a recovery', () => {
    expect(state(null)).toBeNull()
    expect(state(undefined)).toBeNull()
    expect(state('{not json')).toBeNull()
    expect(state(verdict({ run_kind: 'weekly' }))).toBeNull()
  })

  it('a BOOT run with a failure opens on its FIRST reading (no hour of silence after a bad boot)', () => {
    const s = state(failing({ run_kind: 'boot', failed_streak: 1 }))
    expect(s?.active).toBe(true)
    expect(s?.detail).toContain('retired-skills-absent')
    expect(s?.detail).toContain('boot self-test')
  })

  it('a stale or unreadable verdict opens on its first reading', () => {
    expect(state(failing({ run_kind: 'stale', failed: ['selftest-loop-dead'] }))?.active).toBe(true)
  })

  it('an HOURLY failure is inactive on the first failed run and active on the second', () => {
    const first = state(failing({ failed_streak: 1 }))
    expect(first?.active).toBe(false)
    const second = state(failing({ failed_streak: SELFTEST_HOURLY_STREAK }))
    expect(second?.active).toBe(true)
  })

  it('a run with no failed check resolves, warnings or not', () => {
    const s = state(verdict({ warned_count: 2, warned: ['medchron-daemon-idle'] }))
    expect(s?.active).toBe(false)
    expect(s?.detail).toContain('passed')
  })

  it('a run in which no seat check ran pages on its first reading, never resolves', () => {
    // FALSIFIER: drop hollow() and a crashed loop's record (one WARN, failed 0,
    // rewritten fresh on every restart by images before 2026-10-27) resolves
    // the page, as does a check list that would not load (total 0).
    const crash = state(
      verdict({ total: 1, warned_count: 1, warned: ['selftest-crashlooping'], failed_streak: 0 })
    )
    expect(crash?.active).toBe(true)
    expect(crash?.detail).toContain('selftest-crashlooping')
    expect(crash?.detail).not.toContain('passed')
    const empty = state(verdict({ total: 0 }))
    expect(empty?.active).toBe(true)
    expect(empty?.detail).toContain('ran no checks')
  })

  it('a real run that also carries a runner warning still resolves', () => {
    const s = state(verdict({ total: 74, warned_count: 1, warned: ['selftest-crashlooping'] }))
    expect(s?.active).toBe(false)
  })

  it('names the ids it was given and says how many more there were', () => {
    const s = state(failing({ run_kind: 'boot', failed_count: 40, failed: ['a', 'b'] }))
    expect(s?.detail).toContain('a, b and 38 more')
  })

  it('a malformed id is not trusted: the row holds', () => {
    expect(parseSelftestVerdict(failing({ failed: ['Bad Id'] }))).toBeNull()
  })

  it('is wired into evaluateConditions and labelled', () => {
    const out = evaluateConditions(
      [row({ selftest_json: failing({ run_kind: 'boot' }) })],
      NOW,
      600
    )
    expect(out.find((c) => c.condition === 'selftest_failing')?.active).toBe(true)
    expect(conditionLabel('selftest_failing')).toBe('Seat self-test failed a check')
  })
})
