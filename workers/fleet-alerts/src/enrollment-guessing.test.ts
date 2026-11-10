/**
 * enrollment_guessing (migration 0152, ADR 0106). The page can be wrong three
 * ways: silent through a guessing run, paging on one person's typo, or
 * resolving on a NULL. Each test names the direction it forbids.
 */

import { describe, it, expect } from 'vitest'
import { conditionLabel, evaluateConditions, type FleetStatusRow } from './index'
import {
  ENROLLMENT_FAILED_24H_THRESHOLD,
  enrollmentGuessingConditions,
  parseSenderEnrollment,
} from './enrollment-guessing'

const NOW = Date.parse('2026-11-09T12:05:00.000Z')

function row(overrides: Partial<FleetStatusRow>): FleetStatusRow {
  return {
    customer_slug: 'smd-guide',
    last_heartbeat_ts: '2026-11-09T12:04:00.000Z',
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

const facts = (failed: number, locked = false, enrolled = 2) =>
  JSON.stringify({ enrolled, failed_24h: failed, locked })

function state(json: string | null | undefined) {
  const out = enrollmentGuessingConditions(row({ sender_enrollment_json: json }))
  return out.length ? out[0] : null
}

describe('enrollment_guessing', () => {
  it('NULL, absent or malformed pushes nothing: a hold, never a recovery', () => {
    expect(state(null)).toBeNull()
    expect(state(undefined)).toBeNull()
    expect(state('{not json')).toBeNull()
    expect(state(JSON.stringify({ enrolled: 1, failed_24h: -1, locked: false }))).toBeNull()
    expect(state(JSON.stringify({ enrolled: 1, failed_24h: 3, locked: 'no' }))).toBeNull()
  })

  it('a few failures (a person mistyping) do not page, and resolve an open alert', () => {
    const s = state(facts(ENROLLMENT_FAILED_24H_THRESHOLD - 1))
    expect(s?.active).toBe(false)
    expect(state(facts(0))?.active).toBe(false)
  })

  it('the threshold of failures in a day pages on its first reading', () => {
    const s = state(facts(ENROLLMENT_FAILED_24H_THRESHOLD))
    expect(s?.active).toBe(true)
    expect(s?.detail).toContain(`${ENROLLMENT_FAILED_24H_THRESHOLD} failed invite-code attempt(s)`)
    expect(s?.detail).toContain('hermes-smd-guide')
  })

  it('a locked seat pages whatever the day count says', () => {
    const s = state(facts(0, true))
    expect(s?.active).toBe(true)
    expect(s?.detail).toContain('LOCKED')
  })

  it('parses only the whole shape', () => {
    expect(parseSenderEnrollment(facts(3))).toEqual({ enrolled: 2, failed_24h: 3, locked: false })
    expect(parseSenderEnrollment('[]')).toBeNull()
  })

  it('is wired into evaluateConditions and labelled', () => {
    const out = evaluateConditions([row({ sender_enrollment_json: facts(12) })], NOW, 600)
    expect(out.find((c) => c.condition === 'enrollment_guessing')?.active).toBe(true)
    const quiet = evaluateConditions([row({ sender_enrollment_json: null })], NOW, 600)
    expect(quiet.find((c) => c.condition === 'enrollment_guessing')).toBeUndefined()
    expect(conditionLabel('enrollment_guessing')).toBe('Invite codes being guessed on a guide seat')
  })
})
