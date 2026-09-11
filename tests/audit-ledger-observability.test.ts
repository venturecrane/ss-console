/**
 * #2498 — the ledger stops being silent about itself.
 *
 * THE STATE THIS SEPARATES. `fleet_status` carried `last_audit_ts` and nothing
 * else about the ledger, so three different seats arrived as one picture:
 * routines deliberately off (ashton-price since #2332), routines awake with
 * nothing to do, and an audit writer that has been failing. Every audit hook on
 * the Machine swallows a write failure by design — the ledger is observability
 * and an enforced decision is not rolled back because its row failed to
 * persist — so a failure leaves a GAP, and a gap is what a quiet seat leaves
 * too.
 *
 * Each test below names the confusion it removes. The parity gate
 * (tests/heartbeat-field-parity.test.ts) already proves each field reaches its
 * column; this proves the fields MEAN what the admin page and the alert read
 * them to mean.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'

installWorkerdPolyfills()

const captureWarning = vi.fn()
const captureError = vi.fn()
vi.mock('../src/lib/observability/sentry', () => ({
  captureWarning: (...args: unknown[]) => captureWarning(...args),
  captureError: (...args: unknown[]) => captureError(...args),
}))

// Import AFTER the mock so the route binds it.
const { POST } = await import('../src/pages/api/internal/heartbeat')
const { auditWriteFailureDisplay, listFleetStatus } = await import('../src/lib/admin/fleet-status')
const { env: testEnv } = await import('cloudflare:workers')

const migrationsDir = path.join(path.resolve(__dirname, '..'), 'migrations')

const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const ORG_ID = 'org-ledger'
const ENTITY = 'ent-ledger'
const SLUG = 'ledger-co'
const HEAD_A = 'a'.repeat(64)

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Ledger Org', 'ledger-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY, ORG_ID, SLUG, SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-08-20T00:00:00Z')`
    )
    .bind(ENTITY, ORG_ID, SLUG)
    .run()
}

function beat(body: Record<string, unknown>): Parameters<typeof POST>[0] {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': SLUG,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ heartbeat_ts: '2026-08-20T00:00:00.000Z', ...body }),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0]
}

async function stored(db: D1Database): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `SELECT audit_write_failures, audit_head, audit_rows, last_audit_ts
         FROM fleet_status WHERE customer_slug = ?`
    )
    .bind(SLUG)
    .first<Record<string, unknown>>()
}

describe('#2498 the heartbeat carries what the ledger says about itself', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, MACHINE_HEARTBEAT_KEY: MACHINE_KEY })
    captureWarning.mockClear()
    captureError.mockClear()
  })

  it('a reported zero is STORED as zero, not dropped as falsy', async () => {
    // The load-bearing value of the whole issue. 0 is what says "the writer is
    // up and has lost nothing", which is the only thing that separates a quiet
    // ledger from a broken one. A truthiness check anywhere in the chain would
    // turn the healthy case back into silence.
    await POST(beat({ audit_write_failures: 0 }))
    expect((await stored(db))?.audit_write_failures).toBe(0)
  })

  it('a beat that omits the count leaves a known count alone', async () => {
    // COALESCE, not overwrite-including-NULL. NULL from the seat means "cannot
    // answer" (no .smd dir); letting that erase a known count would delete the
    // record of failures we have already seen AND silently reset the delta
    // baseline to zero, which reads as healthy.
    await POST(beat({ audit_write_failures: 4 }))
    await POST(beat({}))
    expect((await stored(db))?.audit_write_failures).toBe(4)
  })

  // One case per test, not five migrated databases inside one 5-second test:
  // the loop form timed out under full-suite load on two of three verify runs
  // on 2026-09-10 while passing in isolation. Each case gets the fresh DB the
  // suite's beforeEach already builds, and its own timeout budget.
  it.each([
    ['negative', -1],
    ['fractional', 1.5],
    ['numeric string', '3'],
    ['boolean', true],
    ['null', null],
  ])('a junk count (%s) is stored NULL rather than guessed at', async (_label, junk) => {
    await POST(beat({ audit_write_failures: junk }))
    expect((await stored(db))?.audit_write_failures ?? null).toBeNull()
  })

  it('the chain head is stored only when it is actually a hash', async () => {
    // Parsed, never cast. This value becomes the pin an integrity check later
    // compares against; junk pinned as a head makes every future verification
    // fail against something that was never a hash.
    await POST(beat({ audit_head: HEAD_A, audit_rows: 7 }))
    const row = await stored(db)
    expect(row?.audit_head).toBe(HEAD_A)
    expect(row?.audit_rows).toBe(7)
  })

  it.each([
    ['too short', 'a'.repeat(63)],
    ['too long', 'a'.repeat(65)],
    ['uppercase', 'A'.repeat(64)],
    ['not hex', 'z'.repeat(64)],
    ['not a string', 12345],
  ])('a %s head is refused and stored NULL', async (_label, value) => {
    await POST(beat({ audit_head: value }))
    expect((await stored(db))?.audit_head ?? null).toBeNull()
  })

  it('an empty ledger reports zero rows and no head, and those do not contradict', async () => {
    // Rows written before the #1686 chain upgrade carry NULL row_hash, so a
    // NULL head beside a non-zero count is information, not a contradiction.
    await POST(beat({ audit_rows: 0 }))
    const row = await stored(db)
    expect(row?.audit_rows).toBe(0)
    expect(row?.audit_head ?? null).toBeNull()
  })
})

describe('#2498 a rise in lost rows reaches a person', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, MACHINE_HEARTBEAT_KEY: MACHINE_KEY })
    captureWarning.mockClear()
    captureError.mockClear()
  })

  it('the first non-zero count raises an event', async () => {
    // prior NULL + reported > 0 is a rise: we have just LEARNED of failures.
    await POST(beat({ audit_write_failures: 3 }))
    expect(captureWarning).toHaveBeenCalledTimes(1)
    const [message, area, extra] = captureWarning.mock.calls[0]
    expect(message).toContain(SLUG)
    expect(area).toBe('operator-audit-write-failure')
    expect(extra).toMatchObject({ lost: 3, reported_total: 3 })
  })

  it('a further rise reports only the NEW losses', async () => {
    await POST(beat({ audit_write_failures: 3 }))
    captureWarning.mockClear()
    await POST(beat({ audit_write_failures: 5 }))
    expect(captureWarning).toHaveBeenCalledTimes(1)
    expect(captureWarning.mock.calls[0][2]).toMatchObject({ lost: 2, prior_total: 3 })
  })

  it('a steady count is silent — a seat that already failed must not re-page every minute', async () => {
    await POST(beat({ audit_write_failures: 3 }))
    captureWarning.mockClear()
    await POST(beat({ audit_write_failures: 3 }))
    await POST(beat({ audit_write_failures: 3 }))
    expect(captureWarning).not.toHaveBeenCalled()
  })

  it('a healthy seat reporting zero is silent', async () => {
    await POST(beat({ audit_write_failures: 0 }))
    expect(captureWarning).not.toHaveBeenCalled()
  })

  it('a seat that cannot answer is silent, not treated as zero', async () => {
    await POST(beat({}))
    expect(captureWarning).not.toHaveBeenCalled()
  })

  it('a count that went DOWN is silent rather than alarming', async () => {
    // The tally file was removed or the volume replaced. Not a rise, and
    // reporting a negative loss would be a fabricated number.
    await POST(beat({ audit_write_failures: 9 }))
    captureWarning.mockClear()
    await POST(beat({ audit_write_failures: 2 }))
    expect(captureWarning).not.toHaveBeenCalled()
  })

  it('the beat still succeeds when the delta read throws', async () => {
    // Losing liveness reporting to protect an alert trades a big signal for a
    // small one. The alert path is best-effort; the heartbeat is not.
    const broken = {
      prepare: (sql: string) => {
        if (sql.includes('SELECT audit_write_failures')) throw new Error('D1 down')
        return db.prepare(sql)
      },
    }
    Object.assign(testEnv, { DB: broken })
    const res = await POST(beat({ audit_write_failures: 1 }))
    expect(res.status).toBe(200)
    expect(captureError).toHaveBeenCalledTimes(1)
    expect(captureWarning).not.toHaveBeenCalled()
  })
})

describe('#2498 the admin page shows the counter beside the last-audit time', () => {
  it('a seat that cannot answer never renders as "none"', () => {
    // The failure this issue is about, in one assertion: a reassuring answer we
    // did not receive must not be shown as a reassuring answer we did.
    expect(auditWriteFailureDisplay(null).label).toBe('not reported')
    expect(auditWriteFailureDisplay(null).label).not.toBe('none')
  })

  it('a real zero reads as none, in the ordinary color', () => {
    const display = auditWriteFailureDisplay(0)
    expect(display.label).toBe('none')
    expect(display.colorClass).not.toContain('error')
  })

  it('lost rows read as a count, in the error color', () => {
    const display = auditWriteFailureDisplay(12)
    expect(display.label).toBe('12 lost')
    expect(display.colorClass).toContain('error')
  })

  it('a nonsense stored value reads as unanswered, not as a count', () => {
    expect(auditWriteFailureDisplay(-1).label).toBe('not reported')
    expect(auditWriteFailureDisplay(Number.NaN).label).toBe('not reported')
  })

  it('the admin reader actually selects the column', async () => {
    // The falsifier for the three above: a perfect display helper fed by a
    // SELECT that never asks for the column renders "not reported" forever.
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    Object.assign(testEnv, { DB: db, MACHINE_HEARTBEAT_KEY: MACHINE_KEY })
    await POST(beat({ audit_write_failures: 6 }))
    const [row] = await listFleetStatus(db)
    expect(row.audit_write_failures).toBe(6)
    expect(auditWriteFailureDisplay(row.audit_write_failures).label).toBe('6 lost')
  })
})
