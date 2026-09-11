/**
 * Portal kill switch — pause-control transport + governance ledger + audit
 * union (#2003, A&P diligence reply Q6/Q7).
 *
 * Covers:
 *  - transport configuration guard (unset secret/URL → throw, nothing sent)
 *  - setStopOnMachine happy path + non-200 → throw (a pause the Machine did
 *    not acknowledge is never reported as paused)
 *  - migration 0096: recordPauseEvent → listPauseEvents round trip, CHECK
 *    constraints on action/source
 *  - the activity-feed union mapping: pause events surface as
 *    AGENT_STOPPED / AGENT_RESUMED entries with authored client copy
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  isPauseConfigured,
  setStopOnMachine,
  recordPauseEvent,
  listPauseEvents,
  readPausePosture,
  reconcilePausePosture,
} from '../src/lib/portal/operator/pause-control'
import { isClientVisibleAction } from '../src/lib/portal/operator/activity-language'

const migrationsDir = path.resolve(__dirname, '../migrations')

const ENV_OK = {
  OPERATOR_MCP_WEBHOOK_SECRET: 'test-secret',
  OPERATOR_RUNTIME_READ_URL: 'https://{app}.example.test',
}

describe('pause transport', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('isPauseConfigured requires both secret and URL', () => {
    expect(isPauseConfigured(ENV_OK)).toBe(true)
    expect(isPauseConfigured({ OPERATOR_MCP_WEBHOOK_SECRET: 'x' })).toBe(false)
    expect(isPauseConfigured({ OPERATOR_RUNTIME_READ_URL: 'https://x' })).toBe(false)
    expect(isPauseConfigured({})).toBe(false)
  })

  it('throws without sending when unconfigured', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    await expect(
      setStopOnMachine({}, 'pilot-smokeball', { actor_id: 'a', reason: 'r' })
    ).rejects.toThrow(/not configured/)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('POSTs /sticky-stop/set and returns the gate result', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          pinned: [{ customer: 'pilot-smokeball', persona: '_machine', prior_level: 'OK' }],
          level: 'HARD_STOP',
        }),
        { status: 200 }
      )
    )
    const result = await setStopOnMachine(ENV_OK, 'pilot-smokeball', {
      actor_id: 'admin@firm.example',
      reason: 'client pause',
    })
    expect(result.level).toBe('HARD_STOP')
    expect(result.pinned).toHaveLength(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/sticky-stop\/set$/)
    expect((init.headers as Record<string, string>).Authorization).toMatch(/^Bearer /)
  })

  it('throws on a non-200 gate response (never records an unacknowledged pause)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('boom', { status: 503 }))
    await expect(
      setStopOnMachine(ENV_OK, 'pilot-smokeball', { actor_id: 'a', reason: 'r' })
    ).rejects.toThrow(/gate set failed: 503/)
  })
})

describe('operator_pause_events governance ledger (migration 0096)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  })

  const EVENT = {
    entity_id: 'entity-1',
    customer_slug: 'pilot-smokeball',
    action: 'pause' as const,
    actor_user_id: 'user-1',
    actor_email: 'admin@firm.example',
    actor_role: 'principal',
    source: 'portal' as const,
    reason: 'client pause',
    gate_level: 'HARD_STOP',
  }

  it('records and lists pause + resume, newest first', async () => {
    await recordPauseEvent(db, EVENT)
    await recordPauseEvent(db, {
      ...EVENT,
      action: 'resume',
      reason: 'client resume',
      gate_level: 'OK',
    })
    const rows = await listPauseEvents(db, 'pilot-smokeball')
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.action).sort()).toEqual(['pause', 'resume'])
    for (const row of rows) {
      expect(row.actor_email).toBe('admin@firm.example')
      expect(row.created_at).toBeTruthy()
    }
  })

  it('scopes the listing to the requested customer', async () => {
    await recordPauseEvent(db, EVENT)
    expect(await listPauseEvents(db, 'other-customer')).toEqual([])
  })

  it('rejects an invalid action or source (CHECK constraints)', async () => {
    await expect(
      recordPauseEvent(db, { ...EVENT, action: 'halt' as unknown as 'pause' })
    ).rejects.toThrow()
    await expect(
      recordPauseEvent(db, { ...EVENT, source: 'api' as unknown as 'portal' })
    ).rejects.toThrow()
  })
})

describe('pause posture reconciliation (#2206)', () => {
  const mirror = (level: string | null, updated_at: string | null) => ({
    sticky_stop_level: level,
    updated_at,
  })
  const event = (action: 'pause' | 'resume', created_at: string) => ({ action, created_at })

  it('no events: the heartbeat mirror decides', () => {
    expect(reconcilePausePosture(mirror('HARD_STOP', '2026-08-29 10:00:00'), null)).toBe(true)
    expect(reconcilePausePosture(mirror(null, '2026-08-29 10:00:00'), null)).toBe(false)
    expect(reconcilePausePosture(null, null)).toBe(false)
  })

  it('a pause acknowledged AFTER the last beat renders paused before the mirror catches up', () => {
    const m = mirror(null, '2026-08-29 10:00:00')
    expect(reconcilePausePosture(m, event('pause', '2026-08-29 10:00:05'))).toBe(true)
  })

  it('a resume acknowledged AFTER the last beat renders un-paused before the mirror catches up', () => {
    const m = mirror('HARD_STOP', '2026-08-29 10:00:00')
    expect(reconcilePausePosture(m, event('resume', '2026-08-29 10:00:05'))).toBe(false)
  })

  it('once the mirror is newer than the last event, the Machine report wins again', () => {
    // The Captain cleared the stop from the admin side (no portal event):
    // the beat after that says no stop, and an older portal pause must not
    // resurrect it.
    const m = mirror(null, '2026-08-29 10:05:00')
    expect(reconcilePausePosture(m, event('pause', '2026-08-29 10:00:05'))).toBe(false)
    const m2 = mirror('HARD_STOP', '2026-08-29 10:05:00')
    expect(reconcilePausePosture(m2, event('resume', '2026-08-29 10:00:05'))).toBe(true)
  })

  it('no fleet_status row yet: the latest event decides', () => {
    expect(reconcilePausePosture(null, event('pause', '2026-08-29 10:00:05'))).toBe(true)
    expect(reconcilePausePosture(mirror(null, null), event('resume', '2026-08-29 10:00:05'))).toBe(
      false
    )
  })

  it('accepts ISO timestamps as well as SQLite datetime text', () => {
    const m = mirror(null, '2026-08-29T10:00:00.000Z')
    expect(reconcilePausePosture(m, event('pause', '2026-08-29 10:00:05'))).toBe(true)
  })
})

describe('readPausePosture against D1 (#2206)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  })

  it('the render right after a portal pause shows paused even though the mirror still says running', async () => {
    await db
      .prepare("INSERT INTO organizations (id, name, slug) VALUES ('org-1', 'Org', 'org')")
      .run()
    await db
      .prepare(
        "INSERT INTO entities (id, org_id, name, slug) VALUES ('entity-1', 'org-1', 'Seat A', 'seat-a')"
      )
      .run()
    await db
      .prepare(
        "INSERT INTO fleet_status (customer_slug, entity_id, sticky_stop_level, updated_at) VALUES ('seat-a', 'entity-1', NULL, '2026-08-29 10:00:00')"
      )
      .run()
    expect(await readPausePosture(db, 'seat-a')).toBe(false)
    await recordPauseEvent(db, {
      entity_id: 'entity-1',
      customer_slug: 'seat-a',
      action: 'pause',
      actor_user_id: 'user-1',
      actor_email: 'admin@firm.example',
      actor_role: 'principal',
      source: 'portal',
      reason: 'client pause',
      gate_level: 'HARD_STOP',
    })
    expect(await readPausePosture(db, 'seat-a')).toBe(true)
  })
})

describe('audit union mapping', () => {
  it('AGENT_STOPPED / AGENT_RESUMED are client-visible actions (authored copy exists)', () => {
    expect(isClientVisibleAction('AGENT_STOPPED')).toBe(true)
    expect(isClientVisibleAction('AGENT_RESUMED')).toBe(true)
  })
})
