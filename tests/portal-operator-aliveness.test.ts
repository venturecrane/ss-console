/**
 * Tests for the Operator dashboard aliveness signal
 * (src/lib/portal/operator/aliveness.ts).
 *
 * Per #875, the dashboard header carries a per-customer "where is the
 * agent right now" signal: idle / running / sticky_stop / offline,
 * plus a last-action timestamp and (for unhealthy postures) a
 * Captain-escalation affordance.
 *
 * The resolver reads the customer's `fleet_status` heartbeat row
 * (ADR 0023 Wave 1, wired in #1678); a customer with no row resolves to
 * null and the dashboard renders nothing per
 * docs/style/empty-state-pattern.md. These tests cover:
 *
 *   - The closed AlivenessLevel vocabulary
 *   - deriveAlivenessFromBridge — the pure transition. Priority and edge
 *     cases (sticky-stop wins, in-flight wins, missing timestamp,
 *     unparseable timestamp, threshold crossing, heartbeat-driven
 *     liveness).
 *   - formatLastActionRelative — relative-time bucket boundaries
 *   - formatLastActionAbsolute — null + unparseable handling
 *   - needsEscalationAffordance — true only for the unhealthy postures
 *   - resolveAlivenessSignal — fleet_status row → signal, plus the
 *     empty-state contract (no row / failed read → null)
 *
 * OFFLINE_THRESHOLD_MINUTES is asserted as a constant so a future
 * customer-yaml override has a single source of truth to verify
 * against.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import { ORG_ID } from '../src/lib/constants'
import {
  ALIVENESS_LEVELS,
  OFFLINE_THRESHOLD_MINUTES,
  deriveAlivenessFromBridge,
  formatLastActionAbsolute,
  formatLastActionRelative,
  needsEscalationAffordance,
  resolveAlivenessSignal,
  type AlivenessBridgeReading,
  type AlivenessLevel,
} from '../src/lib/portal/operator/aliveness'
import type { SubscriptionRow } from '../src/lib/portal/product-access'

function makeReading(overrides?: Partial<AlivenessBridgeReading>): AlivenessBridgeReading {
  return {
    lastAuditTs: '2026-05-24T12:00:00.000Z',
    lastHeartbeatTs: null,
    inFlightSkill: null,
    stickyStopLevel: 'OK',
    stickyStopReason: null,
    ...overrides,
  }
}

/**
 * Minimal fake of the D1 surface the fleet_status read touches:
 * prepare().bind().first(). `row` is what first() resolves; `fail` makes
 * first() throw (missing-table case).
 */
function makeDb(opts: { row?: unknown; fail?: boolean }): D1Database {
  const db = {
    prepare() {
      return {
        bind() {
          return {
            first() {
              if (opts.fail) return Promise.reject(new Error('no such table: fleet_status'))
              return Promise.resolve(opts.row ?? null)
            },
          }
        },
      }
    },
  }
  return db as unknown as D1Database
}

function makeSubscription(overrides?: Partial<SubscriptionRow>): SubscriptionRow {
  return {
    id: 'sub_test',
    org_id: 'org_test',
    entity_id: 'ent_test',
    product_slug: 'operator',
    instance_slug: 'smd',
    status: 'active',
    started_at: '2026-05-01T00:00:00.000Z',
    ended_at: null,
    settings_json: null,
    service_id: null,
    stripe_subscription_id: null,
    created_at: '2026-05-01T00:00:00.000Z',
    updated_at: '2026-05-01T00:00:00.000Z',
    ...overrides,
  }
}

describe('ALIVENESS_LEVELS', () => {
  it('exposes the four canonical levels', () => {
    expect(ALIVENESS_LEVELS).toEqual(['idle', 'running', 'sticky_stop', 'offline'])
  })
})

describe('OFFLINE_THRESHOLD_MINUTES', () => {
  it('is 30 minutes (documented default)', () => {
    expect(OFFLINE_THRESHOLD_MINUTES).toBe(30)
  })
})

describe('needsEscalationAffordance', () => {
  it('is true for the unhealthy postures only', () => {
    expect(needsEscalationAffordance('sticky_stop')).toBe(true)
    expect(needsEscalationAffordance('offline')).toBe(true)
    expect(needsEscalationAffordance('idle')).toBe(false)
    expect(needsEscalationAffordance('running')).toBe(false)
  })
})

describe('deriveAlivenessFromBridge', () => {
  // Anchor "now" at noon UTC on the same day as the default fixture.
  const NOW_MS = Date.parse('2026-05-24T12:05:00.000Z')

  describe('sticky-stop priority', () => {
    it('returns sticky_stop when stickyStopLevel is HARD_STOP', () => {
      const reading = makeReading({
        stickyStopLevel: 'HARD_STOP',
        stickyStopReason: 'consecutive_tool_failures=8',
      })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('sticky_stop')
      expect(signal.stickyStopReason).toBe('consecutive_tool_failures=8')
      expect(signal.currentSkill).toBeNull()
    })

    it('a legacy WARN / SOFT_STOP does NOT tell a client the agent is stopped', () => {
      // Two tests used to assert the opposite, and they were asserting a lie
      // to a client: neither rung restricted a single call, yet both painted
      // the portal chip "the safety substrate has pinned the agent". Both were
      // removed 2026-09-02; a seat still reporting them reads OK, which is
      // what they always meant. Cast because the union no longer admits them,
      // which is the point -- a seat can still SEND them until it reprovisions.
      for (const legacy of ['WARN', 'SOFT_STOP']) {
        const reading = makeReading({
          stickyStopLevel: legacy as unknown as 'HARD_STOP',
          stickyStopReason: 'refusals',
        })
        expect(deriveAlivenessFromBridge(reading, NOW_MS).level).not.toBe('sticky_stop')
      }
    })

    it('sticky-stop wins over an in-flight skill', () => {
      const reading = makeReading({
        stickyStopLevel: 'HARD_STOP',
        stickyStopReason: 'capped',
        inFlightSkill: 'inbox-triage',
      })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('sticky_stop')
      expect(signal.currentSkill).toBeNull()
    })

    it('preserves the last-action timestamp even when sticky-stop', () => {
      const reading = makeReading({
        stickyStopLevel: 'HARD_STOP',
        stickyStopReason: 'capped',
        lastAuditTs: '2026-05-24T11:30:00.000Z',
      })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.lastActionAt).toBe('2026-05-24T11:30:00.000Z')
    })
  })

  describe('running priority', () => {
    it('returns running when a skill is in flight', () => {
      const reading = makeReading({ inFlightSkill: 'inbox-triage' })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('running')
      expect(signal.currentSkill).toBe('inbox-triage')
      expect(signal.stickyStopReason).toBeNull()
    })

    it('ignores an empty-string in-flight skill', () => {
      const reading = makeReading({ inFlightSkill: '' })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('idle')
    })
  })

  describe('idle vs offline by audit-row age', () => {
    it('returns idle when the last audit row is within the threshold', () => {
      // Default fixture ts is at 12:00; now is 12:05 → 5 minutes ago
      // → well within 30 min threshold.
      const signal = deriveAlivenessFromBridge(makeReading(), NOW_MS)
      expect(signal.level).toBe('idle')
    })

    it('returns offline when the last audit row is older than the threshold', () => {
      const reading = makeReading({ lastAuditTs: '2026-05-24T11:30:00.000Z' })
      // now=12:05, last=11:30 → 35 minutes ago → offline (>30).
      expect(deriveAlivenessFromBridge(reading, NOW_MS).level).toBe('offline')
    })

    it('returns offline when lastAuditTs is null (no history)', () => {
      const reading = makeReading({ lastAuditTs: null })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('offline')
      expect(signal.lastActionAt).toBeNull()
    })

    it('returns offline when lastAuditTs is unparseable', () => {
      const reading = makeReading({ lastAuditTs: 'not a timestamp' })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('offline')
      expect(signal.lastActionAt).toBe('not a timestamp')
    })

    it('treats exactly threshold-age as still idle (strict >)', () => {
      // now=12:05; last=11:35 → exactly 30 min ago → still idle.
      const reading = makeReading({ lastAuditTs: '2026-05-24T11:35:00.000Z' })
      expect(deriveAlivenessFromBridge(reading, NOW_MS).level).toBe('idle')
    })
  })

  describe('heartbeat-driven liveness (#1678)', () => {
    it('a fresh heartbeat keeps a quiet Machine idle past stale audit rows', () => {
      // Audit is 3 hours old (would read offline alone) but the Machine
      // heartbeated 2 minutes ago → idle. lastActionAt stays the audit ts —
      // the last thing the operator DID, not the last time it phoned home.
      const reading = makeReading({
        lastAuditTs: '2026-05-24T09:00:00.000Z',
        lastHeartbeatTs: '2026-05-24T12:03:00.000Z',
      })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('idle')
      expect(signal.lastActionAt).toBe('2026-05-24T09:00:00.000Z')
    })

    it('a fresh heartbeat with NO audit history is idle with null lastActionAt', () => {
      // First-day Machine: heartbeating, has not acted yet. Alive, and honest
      // about having no last action.
      const reading = makeReading({
        lastAuditTs: null,
        lastHeartbeatTs: '2026-05-24T12:04:00.000Z',
      })
      const signal = deriveAlivenessFromBridge(reading, NOW_MS)
      expect(signal.level).toBe('idle')
      expect(signal.lastActionAt).toBeNull()
    })

    it('a stale heartbeat AND stale audit is offline', () => {
      const reading = makeReading({
        lastAuditTs: '2026-05-24T09:00:00.000Z',
        lastHeartbeatTs: '2026-05-24T09:05:00.000Z',
      })
      expect(deriveAlivenessFromBridge(reading, NOW_MS).level).toBe('offline')
    })

    it('an unparseable heartbeat is skipped, not treated as epoch 0', () => {
      // Fresh audit + garbage heartbeat → the audit row still proves life.
      const reading = makeReading({ lastHeartbeatTs: 'not a timestamp' })
      expect(deriveAlivenessFromBridge(reading, NOW_MS).level).toBe('idle')
    })
  })
})

describe('formatLastActionRelative', () => {
  const NOW_MS = Date.parse('2026-05-24T12:00:00.000Z')

  it('returns null for null input', () => {
    expect(formatLastActionRelative(null, NOW_MS)).toBeNull()
  })

  it('returns null for unparseable input', () => {
    expect(formatLastActionRelative('garbage', NOW_MS)).toBeNull()
  })

  it('returns "just now" for ages under a minute', () => {
    expect(formatLastActionRelative('2026-05-24T11:59:30.000Z', NOW_MS)).toBe('just now')
  })

  it('returns "just now" for negative ages (clock skew)', () => {
    expect(formatLastActionRelative('2026-05-24T12:00:10.000Z', NOW_MS)).toBe('just now')
  })

  it('returns minute-bucketed labels under an hour', () => {
    expect(formatLastActionRelative('2026-05-24T11:59:00.000Z', NOW_MS)).toBe('1 minute ago')
    expect(formatLastActionRelative('2026-05-24T11:55:00.000Z', NOW_MS)).toBe('5 minutes ago')
    expect(formatLastActionRelative('2026-05-24T11:01:00.000Z', NOW_MS)).toBe('59 minutes ago')
  })

  it('returns hour-bucketed labels under a day', () => {
    expect(formatLastActionRelative('2026-05-24T11:00:00.000Z', NOW_MS)).toBe('1 hour ago')
    expect(formatLastActionRelative('2026-05-24T05:00:00.000Z', NOW_MS)).toBe('7 hours ago')
  })

  it('returns day-bucketed labels beyond a day', () => {
    expect(formatLastActionRelative('2026-05-23T12:00:00.000Z', NOW_MS)).toBe('1 day ago')
    expect(formatLastActionRelative('2026-05-20T12:00:00.000Z', NOW_MS)).toBe('4 days ago')
  })
})

describe('formatLastActionAbsolute', () => {
  it('returns null for null input', () => {
    expect(formatLastActionAbsolute(null)).toBeNull()
  })

  it('returns the input verbatim when unparseable', () => {
    expect(formatLastActionAbsolute('not-a-timestamp')).toBe('not-a-timestamp')
  })

  it('formats a parseable input in UTC with month + day + time', () => {
    const out = formatLastActionAbsolute('2026-05-24T12:00:00.000Z')
    expect(out).not.toBeNull()
    expect(out).toMatch(/2026/)
    expect(out).toMatch(/UTC/)
  })
})

describe('resolveAlivenessSignal', () => {
  const NOW_MS = Date.parse('2026-05-24T12:05:00.000Z')

  it('returns null when the customer has no fleet_status row (empty-state contract)', async () => {
    const signal = await resolveAlivenessSignal(makeDb({}), makeSubscription(), NOW_MS)
    expect(signal).toBeNull()
  })

  it('returns null when the fleet_status read fails (missing table)', async () => {
    const signal = await resolveAlivenessSignal(makeDb({ fail: true }), makeSubscription(), NOW_MS)
    expect(signal).toBeNull()
  })

  it('derives idle from a fresh heartbeat row', async () => {
    const db = makeDb({
      row: {
        last_heartbeat_ts: '2026-05-24T12:04:00.000Z',
        last_audit_ts: '2026-05-24T11:00:00.000Z',
        sticky_stop_level: 'OK',
      },
    })
    const signal = await resolveAlivenessSignal(db, makeSubscription(), NOW_MS)
    expect(signal?.level).toBe('idle')
    expect(signal?.lastActionAt).toBe('2026-05-24T11:00:00.000Z')
  })

  it('derives sticky_stop from a Machine-reported breaker level', async () => {
    const db = makeDb({
      row: {
        last_heartbeat_ts: '2026-05-24T12:04:00.000Z',
        last_audit_ts: '2026-05-24T12:00:00.000Z',
        sticky_stop_level: 'HARD_STOP',
      },
    })
    const signal = await resolveAlivenessSignal(db, makeSubscription(), NOW_MS)
    expect(signal?.level).toBe('sticky_stop')
    // Reason text is not pushed on the heartbeat; the chip shows the posture.
    expect(signal?.stickyStopReason).toBeNull()
  })

  it('treats an unknown sticky_stop_level as OK (under-report, never false-alarm)', async () => {
    const db = makeDb({
      row: {
        last_heartbeat_ts: '2026-05-24T12:04:00.000Z',
        last_audit_ts: '2026-05-24T12:00:00.000Z',
        sticky_stop_level: 'SOMETHING_NEW',
      },
    })
    const signal = await resolveAlivenessSignal(db, makeSubscription(), NOW_MS)
    expect(signal?.level).toBe('idle')
  })

  it('derives offline from a row whose heartbeat and audit are both stale', async () => {
    const db = makeDb({
      row: {
        last_heartbeat_ts: '2026-05-24T09:00:00.000Z',
        last_audit_ts: '2026-05-24T08:00:00.000Z',
        sticky_stop_level: null,
      },
    })
    const signal = await resolveAlivenessSignal(db, makeSubscription(), NOW_MS)
    expect(signal?.level).toBe('offline')
  })
})

/**
 * #2281 regression — multi-seat entities.
 *
 * Migration 0093 re-keyed `fleet_status` from `entity_id` onto `customer_slug`
 * precisely because several seats share one entity; `entity_id` is a plain,
 * non-unique index. A read keyed on `entity_id` + `.first()` therefore returns
 * an ARBITRARY sibling seat's heartbeat. Live prod carries one entity with four
 * fleet_status rows, so the chip was rendering another seat's aliveness.
 *
 * These run against a real migrated SQLite (crane-test-harness) rather than the
 * hand-rolled fake above, because the defect lives in the WHERE clause — a fake
 * that ignores the SQL cannot see it.
 */
describe('resolveAlivenessSignal — seat identity on a multi-seat entity (#2281)', () => {
  const NOW_MS = Date.parse('2026-05-24T12:05:00.000Z')
  const ENTITY_ID = 'ent-multi-seat'
  const migrationsDir = resolve(process.cwd(), 'migrations')

  let db: D1Database

  async function seedSeat(slug: string, lastAuditTs: string, stickyStopLevel: string) {
    await db
      .prepare(
        'INSERT INTO fleet_status ' +
          '(customer_slug, entity_id, last_heartbeat_ts, last_audit_ts, sticky_stop_level) ' +
          'VALUES (?, ?, ?, ?, ?)'
      )
      .bind(slug, ENTITY_ID, '2026-05-24T12:04:00.000Z', lastAuditTs, stickyStopLevel)
      .run()
  }

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    // fleet_status.entity_id is a real FK; the entity must exist or the seed
    // fails on the constraint instead of exercising the read.
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(ENTITY_ID, ORG_ID, 'Multi Seat Firm', 'multi-seat-firm')
      .run()
  })

  it('reads the addressed seat, not a sibling seat sharing the entity', async () => {
    // Insert order matters: the sibling lands first, so an entity-keyed
    // `.first()` returns IT and the assertion below fails on unfixed code.
    await seedSeat('sibling-seat', '2026-05-24T08:00:00.000Z', 'HARD_STOP')
    await seedSeat('addressed-seat', '2026-05-24T12:00:00.000Z', 'OK')

    const signal = await resolveAlivenessSignal(
      db,
      makeSubscription({ entity_id: ENTITY_ID, instance_slug: 'addressed-seat' }),
      NOW_MS
    )

    expect(signal?.lastActionAt).toBe('2026-05-24T12:00:00.000Z')
    expect(signal?.level).toBe('idle')
  })

  it('reads the sibling seat when the sibling is the one addressed', async () => {
    await seedSeat('sibling-seat', '2026-05-24T08:00:00.000Z', 'HARD_STOP')
    await seedSeat('addressed-seat', '2026-05-24T12:00:00.000Z', 'OK')

    const signal = await resolveAlivenessSignal(
      db,
      makeSubscription({ entity_id: ENTITY_ID, instance_slug: 'sibling-seat' }),
      NOW_MS
    )

    expect(signal?.lastActionAt).toBe('2026-05-24T08:00:00.000Z')
    expect(signal?.level).toBe('sticky_stop')
  })

  it('returns null when a seat of this entity exists but the addressed slug has no row', async () => {
    await seedSeat('sibling-seat', '2026-05-24T12:00:00.000Z', 'OK')

    const signal = await resolveAlivenessSignal(
      db,
      makeSubscription({ entity_id: ENTITY_ID, instance_slug: 'no-such-seat' }),
      NOW_MS
    )

    // Empty-state contract: a sibling's heartbeat is never borrowed to fill in.
    expect(signal).toBeNull()
  })

  it('returns null when the subscription carries no instance slug', async () => {
    await seedSeat('sibling-seat', '2026-05-24T12:00:00.000Z', 'OK')

    const signal = await resolveAlivenessSignal(
      db,
      makeSubscription({ entity_id: ENTITY_ID, instance_slug: null }),
      NOW_MS
    )

    // No seat identity → no chip. Falling back to the entity read is exactly
    // the defect this test guards.
    expect(signal).toBeNull()
  })
})
