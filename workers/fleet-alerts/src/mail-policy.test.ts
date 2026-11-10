import { describe, expect, it } from 'vitest'
import { conditionIsQuiet, seatIsQuiet, seatKindOf, sinkSourceIsQuiet } from './mail-policy'

function dbWith(seat: unknown, fail = false): D1Database {
  return {
    prepare: () => ({
      bind: () => ({
        first: async () => {
          if (fail) throw new Error('down')
          return seat === undefined ? null : { seat_json: seat }
        },
      }),
    }),
  } as unknown as D1Database
}

describe('what still emails on a customer seat', () => {
  it.each([
    'heartbeat_red',
    'hard_stop',
    'gateway_loop_wedged',
    'gateway_supervisor_refusing',
    'work_overdue',
    'scheduler_error',
    'edge_down',
    'send_refused',
    'connector_down:smokeball',
    'connector_token_expiring:smokeball',
    'tool_failing:mcp_msgraph_mail_read',
    // Someone guessing a guide seat's invite codes is ours to act on (revoke).
    'enrollment_guessing',
    'a_condition_nobody_has_classified_yet',
  ])('%s', (condition) => {
    expect(conditionIsQuiet(condition)).toBe(false)
  })
})

describe('what is left for the triage agent', () => {
  it.each([
    'gateway_restarted',
    'rollout_failed',
    'image_drift',
    'config_drift',
    'selftest_failing',
    'spec_unverified',
    'spec_control_broken:staff.voice',
    'webhook_surface_missing:operator_seat_facts',
  ])('%s', (condition) => {
    expect(conditionIsQuiet(condition)).toBe(true)
  })

  it('sentry is quiet; healthchecks and audit tampering are not', () => {
    expect(sinkSourceIsQuiet('sentry')).toBe(true)
    expect(sinkSourceIsQuiet('healthchecks')).toBe(false)
    expect(sinkSourceIsQuiet('audit_integrity')).toBe(false)
  })
})

describe('seatIsQuiet', () => {
  it.each(['proving', 'preprod', 'internal', 'sandbox'])('%s is quiet', async (kind) => {
    expect(await seatIsQuiet(dbWith(JSON.stringify({ kind })), 's')).toBe(true)
  })

  it('a customer, an unknown seat, unreadable config and a failed read all email', async () => {
    expect(await seatIsQuiet(dbWith(JSON.stringify({ kind: 'customer' })), 's')).toBe(false)
    expect(await seatIsQuiet(dbWith(undefined), 'fleet')).toBe(false)
    expect(await seatIsQuiet(dbWith('not json'), 's')).toBe(false)
    expect(await seatIsQuiet(dbWith(null, true), 's')).toBe(false)
  })

  it('seatKindOf reads only a non-empty string', () => {
    expect(seatKindOf(JSON.stringify({ kind: '' }))).toBeNull()
    expect(seatKindOf(JSON.stringify({ kind: 7 }))).toBeNull()
    expect(seatKindOf(null)).toBeNull()
  })
})
