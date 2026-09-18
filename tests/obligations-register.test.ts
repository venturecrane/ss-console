/**
 * Obligation register: schema, state machine, and the certification control
 * (ADR 0088, migrations 0117/0118).
 *
 * Law 12 governs this file. Each control gets a case that would FAIL if the
 * control were absent — the falsifier is written next to the assertion, not
 * left implicit. The three that matter most:
 *
 *   - `parked` reaches every working state. If someone re-terminalises the
 *     failure state (the medchron_ledger.py:58 bug), that test goes red.
 *   - A hand-written UPDATE to `verified` naming an invented run is REJECTED by
 *     the foreign key. Writing this test is what established that; the design
 *     had assumed the forgery would land and need detecting afterwards.
 *   - What the key cannot stop — fabricating the run row too — is caught by
 *     findUnwitnessedCertifications, because only CI can supply a
 *     workflow_run_url. That is the check that can still fail in the field.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  checkTransition,
  countByOriginSource,
  countObligations,
  findUnwitnessedCertifications,
  finishReconcileRun,
  getObligation,
  listOpenObligations,
  priorHighWaterMark,
  startReconcileRun,
  transitionObligation,
  upsertObligation,
  VALID_TRANSITIONS,
  type ObligationState,
  type UpsertObligationInput,
} from '../src/lib/db/obligations'

const migrationsDir = resolve(process.cwd(), 'migrations')

const SLUG = 'ashton-price'
const ENTITY = 'e-test-ap'

/** The org the migration chain itself seeds (0003_seed_data.sql:11). */
const ORG = '01JQFK0000SMDSERVICES000'

async function seedClient(db: D1Database): Promise<void> {
  await db
    .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)`)
    .bind(ENTITY, ORG, 'Ashton & Price', 'ashton-price')
    .run()
  await seedSeat(db, SLUG, ENTITY)
}

/** A customer_configs row is the live client key the register hangs off. */
async function seedSeat(db: D1Database, slug: string, entityId: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO customer_configs
         (customer_slug, entity_id, org_id, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1', '[]', 'deadbeef', datetime('now'))`
    )
    .bind(slug, entityId, ORG)
    .run()
}

function input(overrides: Partial<UpsertObligationInput> = {}): UpsertObligationInput {
  return {
    customer_slug: SLUG,
    entity_id: ENTITY,
    stable_key: 'smokeball-task-cleanup',
    kind: 'deliverable',
    what: 'Clean up the duplicated Smokeball task set on the firm’s matters.',
    origin: 'captured',
    origin_source: 'letter',
    source_kind: 'letter',
    source_ref: 'operator/customers/ashton-price/correspondence/26_x.md',
    source_quote: 'we will clean up the duplicated task set on your matters',
    ...overrides,
  }
}

describe('obligation register schema', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedClient(db)
  })

  it('creates both tables with the register indexes', async () => {
    const tables = await db
      .prepare(
        `SELECT name FROM sqlite_master WHERE type='table'
          AND name IN ('client_obligations','reconcile_runs')`
      )
      .all<{ name: string }>()
    expect(tables.results.map((r) => r.name).sort()).toEqual([
      'client_obligations',
      'reconcile_runs',
    ])

    const indexes = await db
      .prepare(`PRAGMA index_list('client_obligations')`)
      .all<{ name: string }>()
    const names = indexes.results.map((r) => r.name)
    expect(names).toContain('idx_client_obligations_open')
    expect(names).toContain('idx_client_obligations_due')
  })

  it('keeps all four cost_anomaly_alerts indexes across the 0118 rebuild', async () => {
    // Falsifier: a rebuild that forgot to recreate an index leaves the email
    // sink's scan degraded and nothing fails loudly. This is the only place
    // that loss would ever be visible.
    const indexes = await db
      .prepare(
        `SELECT name FROM sqlite_master WHERE type='index'
          AND tbl_name='cost_anomaly_alerts' AND name LIKE 'idx_%'`
      )
      .all<{ name: string }>()
    expect(indexes.results.map((r) => r.name).sort()).toEqual([
      'idx_cost_anomaly_alerts_entity',
      'idx_cost_anomaly_alerts_open',
      'idx_cost_anomaly_alerts_source_open',
      'idx_cost_anomaly_alerts_undelivered',
    ])
  })

  it('admits an obligation alert source and still admits the original four', async () => {
    for (const source of ['cost', 'sentry', 'healthchecks', 'audit_integrity', 'obligation']) {
      await db
        .prepare(
          `INSERT INTO cost_anomaly_alerts (entity_id, customer_slug, alert_date, driver,
             source, daily_cents, rolling_avg_cents, ratio_bps, threshold_bps)
           VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0)`
        )
        .bind(ENTITY, SLUG, '2026-09-17', `d-${source}`, source)
        .run()
    }
    const rows = await db
      .prepare(`SELECT COUNT(*) AS n FROM cost_anomaly_alerts`)
      .first<{ n: number }>()
    expect(rows?.n).toBe(5)

    // Falsifier: the CHECK must still reject something, or it constrains nothing.
    await expect(
      db
        .prepare(
          `INSERT INTO cost_anomaly_alerts (entity_id, customer_slug, alert_date, driver,
             source, daily_cents, rolling_avg_cents, ratio_bps, threshold_bps)
           VALUES (?, ?, '2026-09-18', 'x', 'not_a_source', 0, 0, 0, 0)`
        )
        .bind(ENTITY, SLUG)
        .run()
    ).rejects.toThrow()
  })

  it('refuses a due date with no quote grounding it', async () => {
    await expect(
      db
        .prepare(
          `INSERT INTO client_obligations (obligation_id, customer_slug, entity_id, stable_key,
             kind, what, origin, origin_source, source_kind, source_ref, due_at)
           VALUES ('o1', ?, ?, 'k', 'deliverable', 'w', 'captured', 'letter', 'letter', 'r', '2026-10-01')`
        )
        .bind(SLUG, ENTITY)
        .run()
    ).rejects.toThrow()
  })

  it('refuses a captured obligation with no source quote', async () => {
    await expect(
      db
        .prepare(
          `INSERT INTO client_obligations (obligation_id, customer_slug, entity_id, stable_key,
             kind, what, origin, origin_source, source_kind, source_ref)
           VALUES ('o2', ?, ?, 'k', 'deliverable', 'w', 'captured', 'letter', 'letter', 'r')`
        )
        .bind(SLUG, ENTITY)
        .run()
    ).rejects.toThrow()
  })

  it('refuses a parked row with no reason', async () => {
    await expect(
      db
        .prepare(
          `INSERT INTO client_obligations (obligation_id, customer_slug, entity_id, stable_key,
             kind, what, origin, origin_source, source_kind, source_ref, state)
           VALUES ('o3', ?, ?, 'k', 'incident', 'w', 'imported', 'ledger', 'ledger', 'r', 'parked')`
        )
        .bind(SLUG, ENTITY)
        .run()
    ).rejects.toThrow()
  })

  it('refuses verified or closed without a reconcile run id', async () => {
    for (const state of ['verified', 'closed']) {
      await expect(
        db
          .prepare(
            `INSERT INTO client_obligations (obligation_id, customer_slug, entity_id, stable_key,
               kind, what, origin, origin_source, source_kind, source_ref, state)
             VALUES (?, ?, ?, ?, 'deliverable', 'w', 'imported', 'github', 'github', 'r', ?)`
          )
          .bind(`o-${state}`, SLUG, ENTITY, `k-${state}`, state)
          .run()
      ).rejects.toThrow()
    }
  })
})

describe('obligation state machine', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedClient(db)
  })

  it('parked is not terminal and returns to every working state', () => {
    // The medchron bug, stated as a test: `failed` had no outbound edge and a
    // delivered job stayed stranded. If someone empties this list, this fails.
    for (const target of ['open', 'active', 'awaiting_external'] as ObligationState[]) {
      expect(VALID_TRANSITIONS.parked).toContain(target)
    }
  })

  it('closed is the only terminal state, and it can still be reopened', () => {
    const noWayOut = (Object.keys(VALID_TRANSITIONS) as ObligationState[]).filter(
      (s) => VALID_TRANSITIONS[s].length === 0
    )
    expect(noWayOut).toEqual(['void'])
    expect(VALID_TRANSITIONS.closed).toEqual(['open'])
  })

  it('refuses verified and closed without a run id, and allows them with one', () => {
    expect(checkTransition('delivered', 'verified', null)).toEqual({
      ok: false,
      error: 'requires_reconcile_run',
    })
    expect(checkTransition('delivered', 'verified', 'run-1')).toEqual({ ok: true })
    expect(checkTransition('verified', 'closed', null)).toEqual({
      ok: false,
      error: 'requires_reconcile_run',
    })
  })

  it('refuses an illegal jump', () => {
    expect(checkTransition('open', 'verified', 'run-1')).toEqual({
      ok: false,
      error: 'illegal_transition',
    })
  })

  it('parks with a reason and resumes', async () => {
    const id = await upsertObligation(db, input({ kind: 'incident', stable_key: 'matter-a-run' }))
    await transitionObligation(db, { obligationId: id, to: 'active' })
    const parked = await transitionObligation(db, {
      obligationId: id,
      to: 'parked',
      parkReason: 'gate failure mid-run',
      resumeToken: 'job-01M2K6',
    })
    expect(parked.ok).toBe(true)

    const row = await getObligation(db, id)
    expect(row?.state).toBe('parked')
    expect(row?.resume_token).toBe('job-01M2K6')

    const resumed = await transitionObligation(db, { obligationId: id, to: 'active' })
    expect(resumed.ok).toBe(true)
  })

  it('refuses to park without a reason', async () => {
    const id = await upsertObligation(db, input({ stable_key: 'no-reason' }))
    const result = await transitionObligation(db, { obligationId: id, to: 'parked' })
    expect(result).toEqual({ ok: false, error: 'park_needs_reason' })
  })

  it('stamps evidence and closed_at only on certification', async () => {
    const runId = await startReconcileRun(db, 'https://github.com/x/y/actions/runs/1')
    const id = await upsertObligation(db, input({ stable_key: 'cert-path' }))
    await transitionObligation(db, { obligationId: id, to: 'delivered' })

    const beforeCert = await getObligation(db, id)
    expect(beforeCert?.evidence_last_verified_at).toBeNull()

    await transitionObligation(db, {
      obligationId: id,
      to: 'verified',
      reconcileRunId: runId,
      evidenceSurface: 'r2',
      evidenceLocator: 'r2://deliverables/matter-a.pdf',
    })
    const verified = await getObligation(db, id)
    expect(verified?.evidence_last_verified_at).not.toBeNull()
    expect(verified?.closed_at).toBeNull()

    await transitionObligation(db, { obligationId: id, to: 'closed', reconcileRunId: runId })
    const closed = await getObligation(db, id)
    expect(closed?.closed_at).not.toBeNull()
  })
})

describe('certification integrity', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedClient(db)
  })

  it('rejects a hand-written certification naming a run that never happened', async () => {
    // The mutation test: exactly what an agent holding the shared D1
    // credential could attempt — bypass the module, write the state directly,
    // satisfy the NOT NULL CHECK with an invented run id. The foreign key
    // refuses it at the database. If this stops throwing, the naive forgery
    // path has opened and layer 2 of the control is gone.
    const id = await upsertObligation(db, input({ stable_key: 'forged' }))
    await expect(
      db
        .prepare(
          `UPDATE client_obligations SET state = 'verified', reconcile_run_id = 'run-that-never-ran'
            WHERE obligation_id = ?`
        )
        .bind(id)
        .run()
    ).rejects.toThrow()

    const row = await getObligation(db, id)
    expect(row?.state).toBe('open')
  })

  it('flags a certification whose run no CI workflow stands behind', async () => {
    // What the foreign key cannot stop: fabricate the run row as well. Only CI
    // can supply workflow_run_url, so a certification resting on a URL-less run
    // is the residual forgery — and the one the reconciler must surface.
    const localRun = await startReconcileRun(db, null)
    const id = await upsertObligation(db, input({ stable_key: 'unwitnessed' }))
    await transitionObligation(db, { obligationId: id, to: 'delivered' })
    await transitionObligation(db, { obligationId: id, to: 'verified', reconcileRunId: localRun })

    const flagged = await findUnwitnessedCertifications(db)
    expect(flagged.map((o) => o.obligation_id)).toEqual([id])
  })

  it('does not flag a certification from a real CI run', async () => {
    // Falsifier for the check above: if it flagged everything, the previous
    // test would pass while the control was useless.
    const ciRun = await startReconcileRun(
      db,
      'https://github.com/venturecrane/ss-console/actions/runs/1'
    )
    const id = await upsertObligation(db, input({ stable_key: 'honest' }))
    await transitionObligation(db, { obligationId: id, to: 'delivered' })
    await transitionObligation(db, { obligationId: id, to: 'verified', reconcileRunId: ciRun })

    expect(await findUnwitnessedCertifications(db)).toEqual([])
  })
})

describe('register reads', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedClient(db)
  })

  it('re-upserting refreshes the sentence without resurrecting a closed row', async () => {
    const runId = await startReconcileRun(db, null)
    const id = await upsertObligation(db, input({ stable_key: 'reimport' }))
    await transitionObligation(db, { obligationId: id, to: 'delivered' })
    await transitionObligation(db, { obligationId: id, to: 'verified', reconcileRunId: runId })
    await transitionObligation(db, { obligationId: id, to: 'closed', reconcileRunId: runId })

    const again = await upsertObligation(
      db,
      input({ stable_key: 'reimport', what: 'Restated in a later letter.' })
    )
    expect(again).toBe(id)

    const row = await getObligation(db, id)
    expect(row?.state).toBe('closed')
    expect(row?.what).toBe('Restated in a later letter.')
  })

  it('returns both denominators', async () => {
    const runId = await startReconcileRun(db, null)
    const open = await upsertObligation(db, input({ stable_key: 'a' }))
    const done = await upsertObligation(db, input({ stable_key: 'b' }))
    await transitionObligation(db, { obligationId: done, to: 'delivered' })
    await transitionObligation(db, { obligationId: done, to: 'verified', reconcileRunId: runId })
    await transitionObligation(db, { obligationId: done, to: 'closed', reconcileRunId: runId })

    expect(await countObligations(db)).toEqual({ total: 2, universe: 1 })
    expect((await listOpenObligations(db)).map((o) => o.obligation_id)).toEqual([open])
  })

  it('counts obligations per source class for the coverage census', async () => {
    await upsertObligation(db, input({ stable_key: 'l1', origin_source: 'letter' }))
    await upsertObligation(
      db,
      input({
        stable_key: 'g1',
        origin: 'imported',
        origin_source: 'github',
        source_kind: 'github',
        source_quote: null,
      })
    )
    expect(await countByOriginSource(db, SLUG)).toEqual({ letter: 1, github: 1 })
  })

  it('tracks a high-water mark so an empty register is quiet only on day one', async () => {
    expect(await priorHighWaterMark(db)).toBe(0)

    const runId = await startReconcileRun(db, null)
    await finishReconcileRun(db, runId, {
      total: 12,
      universe: 5,
      verified: 1,
      overdue: 2,
      cannotEvaluate: 0,
      exitCode: 2,
    })
    expect(await priorHighWaterMark(db)).toBe(12)
  })
})
