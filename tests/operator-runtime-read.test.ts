/**
 * Tests for the runtime read path (ADR 0043).
 *  - path A: the live console→Machine read client (runtime-read.ts) — the
 *    fail-closed, audited, single-customer contract.
 *  - path B: the console-side summary reader + staleness (runtime-summary.ts).
 *
 * The load-bearing assertions are the path-A invariants: a read always audits,
 * a transport failure fails closed (never throws into the caller), and an auth
 * failure is distinguished from unreachability.
 */

import { describe, it, expect } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../src/lib/constants'
import {
  RuntimeReadUnauthorizedError,
  readMachineRuntime,
  type MachineRuntimeTransport,
  type RuntimeReadAudit,
  type RuntimeReadResult,
} from '../src/lib/operator/runtime-read'
import {
  createRuntimeReadAudit,
  isRuntimeReadConfigured,
} from '../src/lib/operator/runtime-read-transport'
import { getRuntimeSummary, listRuntimeSummary } from '../src/lib/admin/runtime-summary'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

const ACTOR = { actor: 'smd-staff@smd.services', actorRole: 'smd_operator' }

function auditSpy() {
  const rows: Array<Record<string, string>> = []
  const audit: RuntimeReadAudit = { record: async (r) => void rows.push(r) }
  return { audit, rows }
}

// ---------------------------------------------------------------------------
// Path A — readMachineRuntime
// ---------------------------------------------------------------------------

describe('readMachineRuntime', () => {
  it('returns the data and audits an ok outcome on success', async () => {
    const transport: MachineRuntimeTransport = {
      read: async (slug, query) => ({ data: { slug, kind: query.kind, rows: [] } }),
    }
    const { audit, rows } = auditSpy()
    const result = await readMachineRuntime(
      { transport, audit },
      'smith-pi-firm',
      { kind: 'audit_log' },
      ACTOR
    )
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.kind).toBe('audit_log')
    expect(rows).toHaveLength(1)
    expect(rows[0].outcome).toBe('ok')
    expect(rows[0].customerSlug).toBe('smith-pi-firm')
  })

  it('fails closed (unreachable) when the transport throws — never throws into the caller', async () => {
    const transport: MachineRuntimeTransport = {
      read: async () => {
        throw new Error('connect ETIMEDOUT')
      },
    }
    const { audit, rows } = auditSpy()
    const result = await readMachineRuntime(
      { transport, audit },
      'smith-pi-firm',
      { kind: 'audit_log', id: 'd1' },
      ACTOR
    )
    expect(result).toEqual<RuntimeReadResult>({
      ok: false,
      kind: 'audit_log',
      reason: 'unreachable',
    })
    expect(rows[0].outcome).toBe('unreachable')
  })

  it('distinguishes unauthorized from unreachable', async () => {
    const transport: MachineRuntimeTransport = {
      read: async () => {
        throw new RuntimeReadUnauthorizedError()
      },
    }
    const { audit, rows } = auditSpy()
    const result = await readMachineRuntime(
      { transport, audit },
      'smith-pi-firm',
      { kind: 'activity', id: 'm1' },
      ACTOR
    )
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toBe('unauthorized')
    expect(rows[0].outcome).toBe('unauthorized')
  })

  it('still returns the read result even when the audit write fails', async () => {
    const transport: MachineRuntimeTransport = { read: async () => ({ data: { ok: true } }) }
    const audit: RuntimeReadAudit = {
      record: async () => {
        throw new Error('audit D1 down')
      },
    }
    const result = await readMachineRuntime(
      { transport, audit },
      'smith-pi-firm',
      { kind: 'activity' },
      ACTOR
    )
    expect(result.ok).toBe(true)
  })

  it('requires BOTH the host template and the master secret (ADR 0043 A)', () => {
    expect(isRuntimeReadConfigured({})).toBe(false)
    // URL alone is no longer sufficient — the per-customer key needs the master.
    expect(isRuntimeReadConfigured({ OPERATOR_RUNTIME_READ_URL: 'https://x' })).toBe(false)
    expect(isRuntimeReadConfigured({ OPERATOR_RUNTIME_READ_SECRET: 'm' })).toBe(false)
    expect(
      isRuntimeReadConfigured({
        OPERATOR_RUNTIME_READ_URL: 'https://x',
        OPERATOR_RUNTIME_READ_SECRET: 'm',
      })
    ).toBe(true)
  })
})

describe('createRuntimeReadAudit (D1)', () => {
  it('writes one append-only read-audit row per attempt', async () => {
    const db = await freshDb()
    const audit = createRuntimeReadAudit(db, { actorUserId: 'user-9' })
    await audit.record({
      customerSlug: 'smith-pi-firm',
      actor: 'smd-staff@smd.services',
      actorRole: 'smd_operator',
      kind: 'audit_log',
      outcome: 'ok',
    })
    const row = await db
      .prepare('SELECT * FROM operator_runtime_read_audit WHERE customer_slug = ?')
      .bind('smith-pi-firm')
      .first<Record<string, unknown>>()
    expect(row?.actor_user_id).toBe('user-9')
    expect(row?.kind).toBe('audit_log')
    expect(row?.outcome).toBe('ok')
  })
})

// ---------------------------------------------------------------------------
// Path B — runtime summary store
// ---------------------------------------------------------------------------

async function seedEntity(db: D1Database, id: string, slug: string): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(id, ORG_ID, slug, slug)
    .run()
}

async function seedSummary(
  db: D1Database,
  opts: { entity_id: string; customer_slug: string; status?: string; open_alerts?: number }
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO operator_runtime_summary
         (entity_id, customer_slug, summary_status, open_alerts, draft_queue_depth,
          last_activity_ts, pushed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      opts.entity_id,
      opts.customer_slug,
      opts.status ?? 'green',
      opts.open_alerts ?? 0,
      null,
      '2026-06-08T12:00:00.000Z',
      '2026-06-08T12:00:00.000Z'
    )
    .run()
}

describe('runtime summary store', () => {
  it('lists per-customer summaries ordered by slug', async () => {
    const db = await freshDb()
    await seedEntity(db, 'e-b', 'beta-firm')
    await seedEntity(db, 'e-a', 'alpha-firm')
    await seedSummary(db, { entity_id: 'e-b', customer_slug: 'beta-firm', open_alerts: 2 })
    await seedSummary(db, { entity_id: 'e-a', customer_slug: 'alpha-firm' })
    const rows = await listRuntimeSummary(db)
    expect(rows.map((r) => r.customer_slug)).toEqual(['alpha-firm', 'beta-firm'])
    expect(rows[1].open_alerts).toBe(2)
  })

  it('reads one summary by slug, null when absent', async () => {
    const db = await freshDb()
    await seedEntity(db, 'e-a', 'alpha-firm')
    await seedSummary(db, { entity_id: 'e-a', customer_slug: 'alpha-firm', status: 'yellow' })
    const found = await getRuntimeSummary(db, 'alpha-firm')
    expect(found?.summary_status).toBe('yellow')
    expect(await getRuntimeSummary(db, 'nobody')).toBeNull()
  })
})
